"""市场类数据源：中国政府采购网（招标/成交公告）。

取自《技术文档》§3.1：市场 → 招标、应用商店评论 → 爬虫 + API → 每日。

实测（2026-09-15）：
● 搜索接口 `search.ccgp.gov.cn/bxsearch` 有反爬（返回「频繁访问!」页），不可用；
● 列表页 `www.ccgp.gov.cn/cggg/zygg/`（中央公告）可正常获取，且**链接 URL 里
  内嵌了发布日期**（`t20260915_27327769.htm`），无需再解析相对时间。

因此本适配器采用「列表页 + HTML 解析」：用标准库 `html.parser` 抽取
`<a href title>`，再从 URL 还原日期。招标公告是**需求侧的直接证据**，
对应需求探针 Agent 的「招标」输入（设计 §7.4）。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

from app.collector.base import CollectContext, RawSignal, SourceAdapter, strip_html

BASE = "http://www.ccgp.gov.cn"

# 公告类型：对应采购流程的不同阶段。
# 取值来自实测（2026-09-15 抓取 `cggg/zygg/` 列表页时出现的全部 kind）。
ANNOUNCE_TYPES = {
    # 采购公告
    "gkzb": "公开招标",
    "jzxcs": "竞争性磋商",
    "jzxtp": "竞争性谈判",
    "xjgg": "询价公告",
    "dyly": "单一来源",
    "yqzb": "邀请招标",
    # 结果公告
    "zbgg": "中标公告",
    "cjgg": "成交公告",
    # 变更/终止
    "gzgg": "更正公告",
    "fblbgg": "废标公告",
    "zzgg": "终止公告",
}

# 列表页路径（中央 + 地方）
LIST_PAGES = [
    ("/cggg/zygg/", "中央公告"),
    ("/cggg/dfgg/", "地方公告"),
]

# 从 `./gkzb/202609/t20260915_27327769.htm` 还原类型与日期
_ITEM_RE = re.compile(
    r"\./(?P<kind>[a-z]+)/(?P<ym>\d{6})/t(?P<day>\d{8})_(?P<num>\d+)\.htm"
)


class _LinkCollector(HTMLParser):
    """抽取页面里的 <a href title> 链接。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        a = dict(attrs)
        href = (a.get("href") or "").strip()
        if href:
            self._current = {"href": href, "title": (a.get("title") or "").strip(), "text": ""}

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current is not None:
            self.links.append(self._current)
            self._current = None


class CCGPBiddingAdapter(SourceAdapter):
    key = "ccgp_bidding"
    signal_type = "bidding"
    name = "中国政府采购网"
    homepage = BASE
    requires_keywords = True      # 列表页无检索能力，必须靠检索词过滤，否则会拉回全量无关公告
    frequency = "daily"

    def fetch(self, ctx: CollectContext) -> list[RawSignal]:
        keywords = [k.strip() for k in ctx.keywords if k and k.strip()]
        out: list[RawSignal] = []
        # 去重用**公告编号**而非 URL：同一条公告可能同时出现在中央公告页与
        # 地方公告页，两处相对路径不同（拼接出的绝对 URL 也不同），但
        # `t20260915_27327769.htm` 里的编号是稳定的。
        seen: set[str] = set()

        for path, channel in LIST_PAGES:
            if len(out) >= ctx.limit:
                break
            # ⚠️ 必须基于**列表页 URL** 解析相对链接：href 形如
            # `./gkzb/202609/t20260915_27327769.htm`，若基于站点根拼接会
            # 得到 `http://www.ccgp.gov.cn/gkzb/...`（丢了 /cggg/zygg/，404）
            page_url = urljoin(BASE, path)
            try:
                html_text = self.client.get(page_url, expect="text")
            except Exception:
                continue
            for item in self._extract(html_text, channel, page_url):
                key = str(item.metrics.get("notice_id") or item.url)
                if key in seen:
                    continue
                # 有检索词时按标题过滤；命中不了的丢弃，避免污染信号库
                if keywords and not any(k in item.title for k in keywords):
                    continue
                seen.add(key)
                out.append(item)
                if len(out) >= ctx.limit:
                    break
        return out

    # -------------------------------------------------------------- 内部
    def _extract(self, html_text: str, channel: str, page_url: str = BASE) -> list[RawSignal]:
        parser = _LinkCollector()
        try:
            parser.feed(html_text)
        except Exception:
            return []

        rows: list[RawSignal] = []
        for link in parser.links:
            href = link["href"]
            if href.startswith(("http://", "https://")) and "ccgp.gov.cn" not in href:
                continue
            m = _ITEM_RE.search(href)
            if not m:
                continue
            title = link["title"] or strip_html(link["text"])
            if not title or len(title) < 6:
                continue
            kind = m.group("kind")
            rows.append(RawSignal(
                signal_type=self.signal_type,
                source=f"{self.name}·{channel}",
                title=title,
                content=f"公告类型：{ANNOUNCE_TYPES.get(kind, kind)}；发布渠道：{channel}",
                url=urljoin(page_url, href),
                published_at=_parse_day(m.group("day")),
                region="中国",
                metrics={"announce_type": ANNOUNCE_TYPES.get(kind, kind),
                         "channel": channel, "notice_id": m.group("num")},
            ))
        return rows


def _parse_day(day: str) -> datetime | None:
    """`20260915` → datetime(2026, 9, 15, tz=UTC)。"""
    try:
        return datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


__all__ = ["CCGPBiddingAdapter"]
