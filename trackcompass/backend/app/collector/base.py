"""采集器基座：HTTP 客户端、原始信号模型、数据源适配器抽象。

对齐《技术文档》§3.1「数据源接入」与 §3.2「数据管道」：

    数据源 → 采集器 → （清洗/标准化）→ 实体抽取 → 写库

本模块只负责「数据源 → 采集器」这一段：把外部站点的内容取回来，
统一成 `RawSignal`。清洗、去重、实体抽取在 `normalize.py` 与
`app/services/signal_ingest.py` 完成。

设计约束（沿用项目既有原则）：
① **零新增依赖** —— 只用 `httpx` + 标准库（`xml.etree` / `html.parser`）。
② **礼貌采集** —— 固定 UA、请求间隔、指数退避、超时上限。
③ **失败不致命** —— 单源失败只记录，不影响其他源；整轮采集不抛异常。
"""

from __future__ import annotations

import html
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

logger = logging.getLogger("trackcompass.collector")

# 统一 UA：部分政府站点对空 UA / 脚本 UA 返回 403（实测工信部即如此）
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 TrackCompass/1.0"
)

DEFAULT_TIMEOUT = 20.0
DEFAULT_INTERVAL = 0.6      # 同一适配器内两次请求的最小间隔（秒）
MAX_RETRIES = 2


class CollectorError(RuntimeError):
    """采集过程中的可预期错误（网络、限流、解析）。"""


# ------------------------------------------------------------------ 模型
@dataclass
class RawSignal:
    """采集回来的原始条目（尚未清洗/入库）。"""

    signal_type: str
    source: str
    title: str
    content: str = ""
    url: str | None = None
    published_at: datetime | None = None
    industry: str | None = None
    region: str | None = None
    keywords: list[str] = field(default_factory=list)
    entities: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    sentiment: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """转成 `SignalIn` 兼容的字典。"""
        return {
            "signal_type": self.signal_type,
            "source": self.source,
            "title": self.title[:500],
            "content": self.content,
            "url": self.url,
            "industry": self.industry,
            "region": self.region,
            "keywords": self.keywords,
            "entities": self.entities,
            "metrics": self.metrics,
            "sentiment": self.sentiment,
            "published_at": self.published_at,
        }


@dataclass
class CollectContext:
    """一轮采集的上下文（所有适配器共享）。"""

    keywords: list[str] = field(default_factory=list)
    limit: int = 20
    industry: str | None = None
    since: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def first_keyword(self, default: str = "") -> str:
        return self.keywords[0] if self.keywords else default


# ------------------------------------------------------------------ HTTP
class HttpClient:
    """带重试、退避、限流的 HTTP 客户端。

    `trust_env=False`：绕过本机 `http_proxy` 环境变量（实测该代理会把
    政府站点请求变成 502/000 假象，直连反而通）。可通过
    `COLLECTOR_USE_PROXY=true` 打开。
    """

    def __init__(self, use_proxy: bool = False, timeout: float = DEFAULT_TIMEOUT):
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            trust_env=use_proxy,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        self._last_call = 0.0

    def _throttle(self, interval: float = DEFAULT_INTERVAL) -> None:
        gap = time.monotonic() - self._last_call
        if gap < interval:
            time.sleep(interval - gap)
        self._last_call = time.monotonic()

    def get(self, url: str, *, params: dict | None = None,
            headers: dict | None = None, interval: float = DEFAULT_INTERVAL,
            expect: str = "auto") -> Any:
        """GET 并按 `expect` 返回 text / json。失败抛 `CollectorError`。"""
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            self._throttle(interval)
            try:
                resp = self._client.get(url, params=params, headers=headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise CollectorError(f"HTTP {resp.status_code}（服务端限流/错误）")
                if resp.status_code >= 400:
                    raise CollectorError(f"HTTP {resp.status_code}")
                if expect == "json":
                    return resp.json()
                if expect == "text":
                    return resp.text
                ctype = resp.headers.get("content-type", "")
                if "json" in ctype:
                    return resp.json()
                return resp.text
            except CollectorError as exc:
                last_err = exc
                # 4xx（非 429）是确定性错误，重试无意义
                if "HTTP 4" in str(exc) and "429" not in str(exc):
                    break
            except Exception as exc:  # 网络层异常
                last_err = exc
            if attempt < MAX_RETRIES:
                backoff = 1.5 * (attempt + 1)
                logger.debug("重试 %s（第 %d 次）: %s", url, attempt + 1, last_err)
                time.sleep(backoff)
        raise CollectorError(f"请求失败 {url}: {last_err}")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ------------------------------------------------------------------ 适配器
class SourceAdapter(ABC):
    """数据源适配器抽象。

    子类实现 `fetch()`，把外部数据取回并转成 `RawSignal` 列表。
    `available()` 用于表达「需要密钥但我没有」，运行器会跳过并如实上报。
    """

    key: str = ""                  # 适配器唯一键（配置与统计用）
    signal_type: str = "news"      # 对应 SignalIn.signal_type
    name: str = ""                 # 中文名（前端展示）
    homepage: str = ""             # 站点首页（文档/前端展示）
    requires_key: bool = False     # 是否需要密钥/授权
    key_env: str = ""              # 密钥环境变量名
    # 是否必须给定检索词。列表页型源（如招标公告）在无检索词时会拉回全量最新
    # 公告，与公司关注领域无关，会污染信号库并进而污染趋势 —— 因此置 True，
    # 由运行器在缺检索词时跳过并如实上报。
    requires_keywords: bool = False
    frequency: str = "daily"       # 设计 §3.1 的采集频率

    def __init__(self, client: HttpClient | None = None):
        self.client = client or HttpClient()

    # ---- 可用性 ----
    def available(self, config: dict[str, Any] | None = None) -> tuple[bool, str]:
        """返回 (是否可用, 原因)。需要密钥但未配置时返回 False。"""
        if not self.requires_key:
            return True, "公开源"
        import os

        if self.key_env and os.getenv(self.key_env):
            return True, "密钥已配置"
        return False, f"需要配置 {self.key_env or '密钥'}"

    # ---- 采集 ----
    @abstractmethod
    def fetch(self, ctx: CollectContext) -> list[RawSignal]:
        """取回原始条目。实现里不应吞掉异常 —— 由运行器统一处理与上报。"""

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} key={self.key}>"


# ------------------------------------------------------------------ 工具
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# 时间字符串里「空白 + 时区」的组合，如 `11:48:11 +0800`
_TZ_SPACE_RE = re.compile(r"\s+([+-]\d{2}:?\d{2})$")


def strip_html(text: str | None) -> str:
    """去标签 + 反转义 + 压空白。用于 RSS description / 搜索结果摘要。"""
    if not text:
        return ""
    cleaned = _TAG_RE.sub(" ", str(text))
    cleaned = html.unescape(cleaned)
    cleaned = cleaned.replace("\u3000", " ")
    return _WS_RE.sub(" ", cleaned).strip()


def parse_datetime(value: Any, *, patterns: Iterable[str] = ()) -> datetime | None:
    """尽力把各种时间表示解析成带时区的 datetime。失败返回 None。"""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e11:      # 毫秒
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None

    text = str(value).strip()
    # 归一化时区写法：`2026-09-15 11:48:11  +0800` → `2026-09-15 11:48:11+0800`
    # （36氪等 RSS 的 pubDate 就是这种「双空格 + 时区」，%z 不容忍中间的空格）
    text = _TZ_SPACE_RE.sub(r"\1", text)
    candidates: list[str] = list(patterns) + [
        "%a, %d %b %Y %H:%M:%S %z",     # RFC 822（RSS pubDate）
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",          # ISO 8601
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y.%m.%d",
        "%Y/%m/%d",
        "%Y年%m月%d日",
    ]
    for fmt in candidates:
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
