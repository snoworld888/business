"""技术类数据源：学术论文平台。

取自《技术文档》§3.1：技术 → 论文、专利 → API（Google Patents, arXiv）→ 每日。

实测（2026-09-15）：arXiv 官方 API 经本机出口访问返回 `Rate exceeded`
（共享出口 IP 被限流）；改用**开放学术 API**，均为 JSON、无需密钥：

● OpenAlex —— 覆盖 2.5 亿+ 学术作品，含摘要（倒排索引）、DOI、期刊、引用数
● Crossref —— DOI 注册机构官方 API，元数据权威

两者互补：OpenAlex 提供摘要与引用热度，Crossref 保证元数据完整性。
设计 §3.1 要的「技术拐点」判断，靠的正是论文的时间密度与引用增速。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.collector.base import CollectContext, RawSignal, SourceAdapter, parse_datetime

# 与技术评估 Agent 的六维评分（TRL/CRL/…）对齐的检索限定
DEFAULT_FROM_DATE = "2024-01-01"


class OpenAlexAdapter(SourceAdapter):
    key = "openalex"
    signal_type = "paper"
    name = "OpenAlex·学术论文"
    homepage = "https://openalex.org/"
    frequency = "daily"

    API = "https://api.openalex.org/works"

    def fetch(self, ctx: CollectContext) -> list[RawSignal]:
        params = {
            "search": ctx.first_keyword() or "artificial intelligence",
            "per_page": max(1, min(ctx.limit, 50)),
            "filter": f"from_publication_date:{DEFAULT_FROM_DATE}",
        }
        # ⚠️ 不要加 `sort=publication_date:desc`：那会把排序从「相关性」改成
        # 「最新」，实测导致搜「计算机视觉」却返回《泛逻辑分析》这类无关论文。
        # 有 search 参数时 OpenAlex 默认按 relevance_score 排序，正是我们要的。
        # 带上邮箱进入「礼貌池」，限流更宽松（官方推荐做法）
        params["mailto"] = "trackcompass@example.com"
        data = self.client.get(self.API, params=params, expect="json")
        return [self._to_signal(w) for w in (data or {}).get("results", []) if w.get("title")]

    def _to_signal(self, work: dict) -> RawSignal:
        authors = [
            (a.get("author") or {}).get("display_name")
            for a in (work.get("authorships") or [])[:6]
        ]
        authors = [a for a in authors if a]
        venue = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
        abstract = _deinvert(work.get("abstract_inverted_index"))
        topics = [
            t.get("display_name")
            for t in (work.get("topics") or [])[:5] if t.get("display_name")
        ]

        parts = [p for p in [
            f"作者：{'、'.join(authors)}" if authors else "",
            f"发表：{venue}" if venue else "",
            f"主题：{'、'.join(topics)}" if topics else "",
            abstract,
        ] if p]

        return RawSignal(
            signal_type=self.signal_type,
            source="OpenAlex",
            title=work["title"],
            content="\n".join(parts)[:4000],
            url=work.get("doi") or (work.get("primary_location") or {}).get("landing_page_url")
                or f"https://openalex.org/{work.get('id', '').rsplit('/', 1)[-1]}",
            published_at=parse_datetime(work.get("publication_date")),
            # ⚠️ 源主题**不进 keywords**：它们是英文标签（如 "Image Processing
            # Techniques"），与本项目的中文能力词体系（"计算机视觉"）匹配不上，
            # 混进 keywords 只会污染趋势聚类与能力匹配。关键词统一由入库时的
            # `extract_tech_keywords` 从标题+正文抽取（中英兼顾）。
            keywords=[],
            metrics={
                "cited_by_count": work.get("cited_by_count") or 0,
                "type": work.get("type"),
                "doi": work.get("doi"),
                "open_access": ((work.get("open_access") or {}).get("is_oa")),
                "topics": topics,          # 保留原始主题，供后续按需使用
            },
            raw={"openalex_id": work.get("id")},
        )


class CrossrefAdapter(SourceAdapter):
    key = "crossref"
    signal_type = "paper"
    name = "Crossref·论文元数据"
    homepage = "https://www.crossref.org/"
    frequency = "daily"

    API = "https://api.crossref.org/works"

    def fetch(self, ctx: CollectContext) -> list[RawSignal]:
        params = {
            "query": ctx.first_keyword() or "artificial intelligence",
            "rows": max(1, min(ctx.limit, 50)),
            "select": "DOI,title,abstract,author,container-title,published,issued,type,is-referenced-by-count,subject",
        }
        # 同理不加 `sort=published`：Crossref 有 query 时默认按相关性排序，
        # 指定日期排序会让「计算机视觉」返回物流、教育等无关领域的新文章。
        data = self.client.get(self.API, params=params, expect="json")
        items = ((data or {}).get("message") or {}).get("items") or []
        out: list[RawSignal] = []
        for it in items:
            titles = it.get("title") or []
            if not titles:
                continue
            out.append(self._to_signal(it, titles[0]))
        return out

    def _to_signal(self, item: dict, title: str) -> RawSignal:
        authors = []
        for a in (item.get("author") or [])[:6]:
            name = " ".join(x for x in [a.get("given"), a.get("family")] if x)
            if name:
                authors.append(name)
        venue = (item.get("container-title") or [None])[0]
        subjects = list(item.get("subject") or [])[:5]

        parts = [p for p in [
            f"作者：{'、'.join(authors)}" if authors else "",
            f"发表：{venue}" if venue else "",
            f"学科：{'、'.join(subjects)}" if subjects else "",
            _clean_abstract(item.get("abstract")),
        ] if p]

        doi = item.get("DOI")
        published = (
            parse_datetime((item.get("published") or {}).get("date-parts", [[None]])[0])
            if isinstance((item.get("published") or {}).get("date-parts"), list)
            else None
        )

        return RawSignal(
            signal_type=self.signal_type,
            source="Crossref",
            title=title,
            content="\n".join(parts)[:4000],
            url=f"https://doi.org/{doi}" if doi else None,
            published_at=published,
            # 同 OpenAlex：源学科标签不进 keywords（理由见上方注释）
            keywords=[],
            metrics={
                "cited_by_count": item.get("is-referenced-by-count") or 0,
                "type": item.get("type"),
                "doi": doi,
                "subjects": subjects,
            },
        )


# ------------------------------------------------------------------ 工具
def _deinvert(inverted: dict | None) -> str:
    """把 OpenAlex 的摘要倒排索引还原成正文。"""
    if not inverted:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inverted.items():
        for i in idxs:
            positions[i] = word
    if not positions:
        return ""
    return " ".join(positions[i] for i in sorted(positions))[:1500]


def _clean_abstract(text: str | None) -> str:
    """Crossref 摘要常带 JATS 标签（<jats:p>），去掉后压空白。"""
    from app.collector.base import strip_html

    return strip_html(text)[:1500]


__all__ = ["OpenAlexAdapter", "CrossrefAdapter"]
