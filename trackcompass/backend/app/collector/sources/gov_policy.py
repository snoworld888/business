"""政策类数据源：中国政府网「国务院政策文件库」。

取自《技术文档》§3.1：政策 → 政府网站、产业政策 → 爬虫 + API → 每日。

实测结论（2026-09-15）：
`https://www.gov.cn/zhengce/zuixin.htm` 是 JS 跳转页，不能直接解析；
真正的稳定入口是**国务院政策文件库的搜索 JSON 接口**，字段完整
（文号、发文机关、摘要、成文日期、URL），无需 HTML 解析。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.collector.base import CollectContext, RawSignal, SourceAdapter, parse_datetime, strip_html

SEARCH_API = "https://sousuo.www.gov.cn/search-gov/data"

# 政策文件库的两个子库（对应设计里「政府网站 / 产业政策」两类）
LIB_SCOPES = {
    "gw": "国务院文件",
    "bm": "部门文件",
}


class GovPolicyAdapter(SourceAdapter):
    key = "gov_policy"
    signal_type = "policy"
    name = "中国政府网·政策文件库"
    homepage = "https://www.gov.cn/zhengce/zhengceku/"
    frequency = "daily"

    def fetch(self, ctx: CollectContext) -> list[RawSignal]:
        out: list[RawSignal] = []
        seen: set[str] = set()

        for scope, scope_name in LIB_SCOPES.items():
            if len(out) >= ctx.limit:
                break
            want = max(1, ctx.limit - len(out))
            try:
                rows = self._query(ctx.first_keyword(), scope, want)
            except Exception:
                # 子库失败不阻断另一个子库
                continue
            for row in rows:
                url = row.get("url")
                title = strip_html(row.get("title"))
                if not title or not url or url in seen:
                    continue
                seen.add(url)
                out.append(self._to_signal(row, title, url, scope_name))
                if len(out) >= ctx.limit:
                    break
        return out

    # -------------------------------------------------------------- 内部
    def _query(self, keyword: str, scope: str, n: int) -> list[dict]:
        """调用政策文件库检索接口。"""
        params = {
            "t": f"zhengcelibrary_{scope}",
            "q": keyword or "产业",
            "timetype": "timeqb",
            "sort": "pubtime",
            "sortType": "1",
            "p": 1,
            "n": max(1, min(n, 20)),
        }
        data = self.client.get(
            SEARCH_API, params=params, expect="json",
            headers={"Referer": "https://sousuo.www.gov.cn/"},
        )
        vo = (data or {}).get("searchVO") or {}
        return list(vo.get("listVO") or [])

    def _to_signal(self, row: dict, title: str, url: str, scope_name: str) -> RawSignal:
        published = (
            parse_datetime(row.get("pubtime"))
            or parse_datetime(row.get("pubtimeStr"))
            or None
        )
        summary = strip_html(row.get("summary"))
        org = (row.get("puborg") or "").strip()
        pcode = (row.get("pcode") or "").strip()
        category = (row.get("childtype") or "").strip()

        # 正文 = 摘要，并保留文号/发文机关等结构化线索，便于后续 RAG 与实体抽取
        parts = [p for p in [
            f"发文机关：{org}" if org else "",
            f"文号：{pcode}" if pcode else "",
            f"分类：{category}" if category else "",
            summary,
        ] if p]
        content = "\n".join(parts)

        return RawSignal(
            signal_type=self.signal_type,
            source=scope_name,
            title=title,
            content=content,
            url=url,
            published_at=published,
            region="中国",
            metrics={"policy_code": pcode, "issuer": org, "category": category},
            raw={"id": row.get("id"), "index": row.get("index")},
        )


class RecentPolicyAdapter(GovPolicyAdapter):
    """近 N 天政策增量子集 —— 用于「每日」频率的定时采集。

    与全量检索的区别：只保留 `since` 之后的条目，避免重复拉取历史文件。
    """

    key = "gov_policy_recent"
    name = "中国政府网·政策文件库（近期）"

    def __init__(self, client=None, days: int = 7):
        super().__init__(client)
        self.days = days

    def fetch(self, ctx: CollectContext) -> list[RawSignal]:
        rows = super().fetch(ctx)
        cutoff = ctx.since or (datetime.now(timezone.utc) - timedelta(days=self.days))
        fresh = [r for r in rows if r.published_at and r.published_at >= cutoff]
        # 时间解析失败的条目保留原样，避免因单条脏数据整批丢弃
        return fresh or rows
