"""市场/产业类数据源：科技媒体 RSS。

取自《技术文档》§3.1：市场 → 招标、应用商店评论 → 爬虫 + API → 每日。
产业媒体是**资本动向与市场热度**的可公开替代源（融资/财报类需授权密钥，
见 `api_key_stubs.py`），也是设计里「社媒/论坛」缺席时的噪声兜底。

设计要点：RSS 用标准库 `xml.etree.ElementTree` 解析，**不引入 feedparser**，
保持项目「offline-friendly、零新增依赖」的约定。
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from app.collector.base import CollectContext, RawSignal, SourceAdapter, parse_datetime, strip_html

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# "2026-09-15 11:48:11  +0800" → "2026-09-15 11:48:11 +0800"
_MULTISPACE_RE = re.compile(r"\s+")


class RSSAdapter(SourceAdapter):
    """通用 RSS / Atom 适配器。子类只需给出 `feed_url` 等元信息。"""

    key = "rss"
    signal_type = "news"
    name = "RSS 源"
    homepage = ""
    feed_url = ""

    def fetch(self, ctx: CollectContext) -> list[RawSignal]:
        text = self.client.get(self.feed_url, expect="text")
        items = self._parse(text)
        out: list[RawSignal] = []
        for it in items[: max(1, ctx.limit)]:
            title = strip_html(it.get("title"))
            if not title:
                continue
            out.append(RawSignal(
                signal_type=self.signal_type,
                source=self.name,
                title=title,
                content=strip_html(it.get("description"))[:3000],
                url=it.get("link"),
                published_at=parse_datetime(_norm_time(it.get("pubDate"))),
                region="中国" if self._is_cn else None,
                raw={"guid": it.get("guid")},
            ))
        return out

    _is_cn = True

    # -------------------------------------------------------------- 解析
    def _parse(self, text: str) -> list[dict]:
        """解析 RSS 2.0 / Atom，返回统一的条目字典列表。"""
        try:
            root = ET.fromstring(text.strip())
        except ET.ParseError as exc:
            raise ValueError(f"RSS 解析失败：{exc}") from exc

        items: list[dict] = []
        # RSS 2.0
        for node in root.iter("item"):
            items.append({
                "title": _find(node, "title"),
                "link": _find(node, "link"),
                "description": _find(node, "description") or _find(node, "encoded", NS["content"]),
                "pubDate": _find(node, "pubDate") or _find(node, "date", NS["dc"]),
                "guid": _find(node, "guid"),
            })
        # Atom
        if not items:
            for node in root.iter("{http://www.w3.org/2005/Atom}entry"):
                link_el = node.find("atom:link", NS)
                items.append({
                    "title": _find(node, "title", NS["atom"]),
                    "link": link_el.get("href") if link_el is not None else None,
                    "description": _find(node, "summary", NS["atom"])
                                   or _find(node, "content", NS["atom"]),
                    "pubDate": _find(node, "updated", NS["atom"])
                               or _find(node, "published", NS["atom"]),
                    "guid": _find(node, "id", NS["atom"]),
                })
        return items


class TechNews36KrAdapter(RSSAdapter):
    key = "news_36kr"
    name = "36氪"
    homepage = "https://36kr.com/"
    feed_url = "https://www.36kr.com/feed"


class ITHomeAdapter(RSSAdapter):
    key = "news_ithome"
    name = "IT之家"
    homepage = "https://www.ithome.com/"
    feed_url = "https://www.ithome.com/rss/"


# 便于扩展：设计里「行业论坛 / 应用商店评论」可挂到同类 RSS 上
class JiemianAdapter(RSSAdapter):
    key = "news_jiemian"
    name = "界面新闻"
    homepage = "https://www.jiemian.com/"
    feed_url = "https://a.jiemian.com/index.php?m=article&a=rss"


# ------------------------------------------------------------------ 工具
def _find(node: ET.Element, tag: str, ns: str | None = None) -> str | None:
    path = f"{{{ns}}}{tag}" if ns else tag
    el = node.find(path)
    if el is None:
        return None
    if el.text:
        return el.text.strip()
    # Atom 的 <content type="html"> 可能嵌子节点
    return "".join(el.itertext()).strip() or None


def _norm_time(value: str | None) -> str | None:
    return _MULTISPACE_RE.sub(" ", value).strip() if value else None


__all__ = ["RSSAdapter", "TechNews36KrAdapter", "ITHomeAdapter", "JiemianAdapter"]
