"""清洗与标准化 —— 对齐《技术文档》§3.2「清洗：去重、去噪、格式化、语言识别」。

    数据源 → 采集器 → 清洗/标准化 → 实体抽取 → 写库
                        ↑ 本模块

采集回来的条目质量参差：标题带站点后缀、正文混着 HTML 残留、行业字段空着、
中英文混杂。本模块把它们统一成与 `SignalIn` 一致的规范形态，**不触碰数据库**。

去重策略（这是本项目反复踩过的坑，单独说明）：
● 第一道：`content_hash` = sha256(归一化标题)[:32] —— 与 `api/v1/signals.py`
  既有写入逻辑保持一致，保证「API 手工写入」与「采集器回传」互相可见去重结果。
● 第二道：URL 精确匹配 —— 同一篇文章在不同批次被采集时兜底。
两道的判重与落库在 `app/services/signal_ingest.py` 统一执行。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from app.collector.base import RawSignal, strip_html

# ---------------------------------------------------------------- 行业口径
# 与 seeds / tracks 表保持一致（见 app/seed/__init__.py）
INDUSTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "AI+制造": (
        "制造", "工厂", "产线", "生产线", "质检", "工业", "机器视觉", "缺陷检测",
        "智能制造", "工业互联网", "数字孪生", "设备", "车间", "柔性生产", "plc",
    ),
    "AI+医疗": (
        "医疗", "医学", "临床", "诊断", "影像", "药物", "制药", "健康", "医院",
        "病理", "手术", "生物", "病历", "辅助诊疗",
    ),
    "具身智能": (
        "机器人", "人形", "机械臂", "具身", "灵巧手", "运动控制", "协作机器人",
        "agv", "amr", "无人车", "自动驾驶", "伺服",
    ),
    "新能源科技": (
        "电池", "储能", "光伏", "风电", "新能源", "锂电", "氢能", "充电",
        "固态电池", "电解液", "正极", "负极", "逆变器", "电力",
    ),
}

# 标题命中的权重倍数（见 infer_industry）
TITLE_WEIGHT = 3

# ---------------------------------------------------------------- 去噪
# 标题常见的站点后缀，采集回来常粘连在标题尾部
_TITLE_SUFFIX_RE = re.compile(
    r"[\s\-—_|·]+(?:36氪|IT之家|界面新闻|新华网|人民网|央视网|钛媒体|"
    r"雷锋网|机器之心|新浪科技|网易科技|腾讯科技|中国政府采购网|"
    r"中国政府网|中华人民共和国|国务院)[\s\-—_|·]*$"
)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 剥标签时会把 `<em>人工智能</em>+行动` 变成 `人工智能 +行动`。
# 中文语境的空格是噪声 —— 把「中文 空格 中文/符号」里的空格去掉。
_CN_SPACE_RE = re.compile(r"([\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])")
_CN_SIGN_SPACE_RE = re.compile(r"([\u4e00-\u9fff]) +(?=[+＋#])")
_PUNCT_EDGE = " \t\r\n-—_·|,，。;；:：'\"“”‘’()（）[]【】<>《》"


def clean_title(title: str | None) -> str:
    """标题去噪：剥 HTML、去站点后缀、压空白、去首尾标点。"""
    text = strip_html(title)
    if not text:
        return ""
    text = _CTRL_RE.sub("", text)
    text = _TITLE_SUFFIX_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = _CN_SPACE_RE.sub(r"\1", text)         # 中文 空格 中文 → 紧排
    text = _CN_SIGN_SPACE_RE.sub(r"\1", text)    # 中文 空格 + → 紧排（如「人工智能+行动」）
    return text.strip(_PUNCT_EDGE)[:500]


def clean_content(content: str | None, limit: int = 4000) -> str:
    """正文规范化：剥 HTML、压空白、按上限截断。"""
    text = strip_html(content)
    if not text:
        return ""
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


# ---------------------------------------------------------------- 语言
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{3,}")


def detect_language(text: str) -> str:
    """粗粒度语言识别：`zh` / `en` / `mixed` / `unknown`。

    对齐设计 §3.2 的「语言识别」要求。只做二分统计，不引入语言模型依赖。
    """
    cjk = len(_CJK_RE.findall(text))
    words = len(_ASCII_WORD_RE.findall(text))
    if cjk == 0 and words == 0:
        return "unknown"
    if cjk and words:
        # 中文条目里常夹带英文术语，比例悬殊时仍判为中文
        return "mixed" if words > cjk * 2 else "zh"
    return "zh" if cjk else "en"


# ---------------------------------------------------------------- 行业
def infer_industry(*texts: str, fallback: str | None = None) -> str | None:
    """按命中词频推断行业；命中数为 0 时返回 `fallback`。

    标题命中的权重是正文的 3 倍 —— 标题是作者对内容主题的显式概括，
    比正文里偶然出现的词可信得多（正文提到「设备」不代表这篇讲智能制造）。
    """
    title = (texts[0] if texts else "").lower()
    rest = " ".join(t.lower() for t in texts[1:] if t)
    if not title and not rest:
        return fallback

    best, best_score = None, 0
    for industry, words in INDUSTRY_KEYWORDS.items():
        in_title = sum(1 for w in words if w in title)
        in_body = sum(1 for w in words if w in rest)
        score = in_title * TITLE_WEIGHT + in_body
        if score > best_score:
            best, best_score = industry, score
    return best or fallback


# ---------------------------------------------------------------- 质量闸门
MIN_TITLE_LEN = 8
_SPAM_PATTERNS = (
    "验证码", "登录后查看", "该内容已被删除", "页面不存在", "403 forbidden",
    "频繁访问", "access denied",
)


def is_low_quality(title: str, content: str) -> tuple[bool, str]:
    """质量闸门：挡住明显无效的条目，返回 (是否丢弃, 原因)。"""
    if len(title) < MIN_TITLE_LEN:
        return True, "标题过短"
    low = f"{title} {content}".lower()
    for pat in _SPAM_PATTERNS:
        if pat in low:
            return True, f"命中无效页特征：{pat}"
    # 纯英文标题但正文为空 —— 常见于 RSS 摘要缺失，仍保留（标题本身即信号）
    return False, ""


# ---------------------------------------------------------------- 主流程
def normalize(raw: RawSignal, *, industry_hint: str | None = None,
              default_industry: str | None = None) -> dict[str, Any] | None:
    """把 `RawSignal` 规范成可直接写库的 payload。低质量条目返回 None。"""
    title = clean_title(raw.title)
    content = clean_content(raw.content)

    dropped, _reason = is_low_quality(title, content)
    if dropped:
        return None

    # 行业归属的优先级（顺序很重要）：
    # ① 信号自带 industry —— 采集器明确知道的最可信；
    # ② 从**标题与正文**推断 —— 让「医疗影像」的信号归到 AI+医疗；
    # ③ 公司行业兜底 —— 检索词本就来自公司关注领域，兜底是合理的；
    # ④ 调用方给的默认值。
    # ⚠️ 不能把 ③ 放在前面：那会把所有外部信号（无论内容讲什么）一律
    # 打成公司所在行业，趋势雷达就再也分不出赛道差异了。
    industry = (
        raw.industry
        or infer_industry(title, content, " ".join(raw.keywords))
        or industry_hint
        or default_industry
    )

    published = raw.published_at or datetime.now(timezone.utc)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)

    return {
        "signal_type": raw.signal_type,
        "source": raw.source,
        "title": title,
        "content": content,
        "url": (raw.url or "").strip() or None,
        "industry": industry,
        "region": raw.region,
        "keywords": [k for k in dict.fromkeys(raw.keywords) if k][:12],
        "entities": raw.entities or {},
        "metrics": {
            **(raw.metrics or {}),
            "language": detect_language(f"{title} {content}"),
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "adapter": raw.source,
        },
        "sentiment": raw.sentiment,
        "published_at": published,
    }


def content_hash(title: str) -> str:
    """与 `api/v1/signals.py` 完全一致：sha256(原始标题)[:32]。

    ⚠️ 必须用**清洗前的原始标题**调用，否则同一篇文章经 API 与采集器
    两条路径写入时会算出不同 hash，去重失效。
    """
    return hashlib.sha256((title or "").encode("utf-8")).hexdigest()[:32]


__all__ = [
    "INDUSTRY_KEYWORDS", "clean_title", "clean_content", "detect_language",
    "infer_industry", "is_low_quality", "normalize", "content_hash",
]
