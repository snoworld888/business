"""需要密钥/授权的数据源：完整适配器骨架。

《技术文档》§3.1 定义的七类源里，以下四类**无法公开采集**，必须获得授权：

| 类型 | 设计来源 | 现实约束 |
| --- | --- | --- |
| 专利 | Google Patents | 无官方 API；PatentsView 需申请密钥 |
| 资本 | Crunchbase / 企查查 | 商业 API，需付费账号 |
| 人才 | 招聘 JD | 无开放 API，且反爬严格 |
| 社媒 | 抖音 / 小红书 | 需开放平台资质 |
| 供应链 | 企业公告 | 无统一 API |

本模块把它们实现为**可直接启用的适配器**：请求参数按官方文档写好，
只要在 `.env` 里填入对应密钥，`available()` 即转为 True，运行器自动纳入采集。

⚠️ 诚实标注：这些源的**参数结构依据官方文档**，但**未在本机联网验证**
（因为没有密钥）。首次启用时请先跑 `--dry-run` 观察返回结构，
必要时微调 `_parse()` —— 解析逻辑已与网络请求分离，改动面很小。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from app.collector.base import CollectContext, RawSignal, SourceAdapter, parse_datetime, strip_html


class KeyRequiredAdapter(SourceAdapter):
    """需要密钥的适配器基类。

    子类给出 `key_env`（环境变量名）与 `signup_url`（申请入口），
    并实现 `_fetch_keyed()`（拿到密钥后才执行的真正逻辑）。
    """

    requires_key = True
    signup_url = ""
    docs = ""

    def fetch(self, ctx: CollectContext) -> list[RawSignal]:
        ok, reason = self.available()
        if not ok:
            raise PermissionError(f"{self.name}：{reason}")
        return self._fetch_keyed(ctx, os.getenv(self.key_env, ""))

    def _fetch_keyed(self, ctx: CollectContext, api_key: str) -> list[RawSignal]:
        raise NotImplementedError


class PatentsViewAdapter(KeyRequiredAdapter):
    """专利：PatentsView（美国专利商标局官方开放数据）。"""

    key = "patentsview"
    signal_type = "patent"
    name = "PatentsView·专利"
    homepage = "https://patentsview.org/"
    key_env = "PATENTSVIEW_API_KEY"
    signup_url = "https://search.patentsview.org/docs/docs/Search%20API/SearchAPIReference/"
    frequency = "daily"

    API = "https://search.patentsview.org/api/v1/patent/"

    def _fetch_keyed(self, ctx: CollectContext, api_key: str) -> list[RawSignal]:
        keyword = ctx.first_keyword()
        params = {
            "q": f'{{"_text_any":{{"patent_title":"{keyword}"}}}}' if keyword else '{"_gte":{"patent_date":"2024-01-01"}}',
            "f": '["patent_id","patent_title","patent_abstract","patent_date"]',
            "o": '{"per_page":%d}' % max(1, min(ctx.limit, 50)),
            "s": '[{"patent_date":"desc"}]',
        }
        data = self.client.get(
            self.API, params=params, expect="json",
            headers={"X-Api-Key": api_key},
        )
        return [
            RawSignal(
                signal_type=self.signal_type,
                source="PatentsView",
                title=strip_html(p.get("patent_title")) or p.get("patent_id", ""),
                content=strip_html(p.get("patent_abstract"))[:3000],
                url=f"https://patents.google.com/patent/US{p.get('patent_id')}",
                published_at=parse_datetime(p.get("patent_date")),
                metrics={"patent_id": p.get("patent_id")},
            )
            for p in (data or {}).get("patents", []) if p.get("patent_title")
        ]


class CrunchbaseAdapter(KeyRequiredAdapter):
    """资本：Crunchbase（融资事件）。"""

    key = "crunchbase"
    signal_type = "funding"
    name = "Crunchbase·融资事件"
    homepage = "https://www.crunchbase.com/"
    key_env = "CRUNCHBASE_API_KEY"
    signup_url = "https://data.crunchbase.com/docs"
    frequency = "daily"

    API = "https://api.crunchbase.com/api/v4/searches/organizations"

    def _fetch_keyed(self, ctx: CollectContext, api_key: str) -> list[RawSignal]:
        payload = {
            "field_ids": ["identifier", "short_description", "last_funding_type",
                          "last_funding_at", "funding_total", "location_identifiers"],
            "query": [
                {"type": "predicate", "field_id": "facet_ids", "operator_id": "includes", "values": ["company"]},
                {"type": "predicate", "field_id": "short_description", "operator_id": "contains", "values": [ctx.first_keyword()]},
            ],
            "limit": max(1, min(ctx.limit, 50)),
        }
        self.client._client.headers.update({"X-cb-user-key": api_key})
        data = self.client.get(self.API, expect="json")
        out: list[RawSignal] = []
        for ent in ((data or {}).get("entities") or []):
            props = ent.get("properties") or {}
            ident = props.get("identifier") or {}
            name = ident.get("value")
            if not name:
                continue
            out.append(RawSignal(
                signal_type=self.signal_type,
                source="Crunchbase",
                title=f"{name}：{props.get('last_funding_type') or '融资事件'}",
                content=strip_html(props.get("short_description"))[:3000],
                url=f"https://www.crunchbase.com/organization/{ident.get('permalink', '')}",
                published_at=parse_datetime(props.get("last_funding_at")),
                metrics={"funding_type": props.get("last_funding_type"),
                         "funding_total": props.get("funding_total")},
            ))
        return out


class RecruitAdapter(KeyRequiredAdapter):
    """人才：招聘 JD。

    招聘平台无统一开放 API 且反爬严格，设计上需按平台分别对接。
    这里保留完整骨架与配置位，接入时只需实现 `_fetch_keyed`。
    """

    key = "recruit"
    signal_type = "recruit"
    name = "招聘平台·JD"
    key_env = "RECRUIT_API_KEY"
    signup_url = ""
    frequency = "daily"

    def _fetch_keyed(self, ctx: CollectContext, api_key: str) -> list[RawSignal]:
        raise NotImplementedError(
            "招聘 JD 需按目标平台单独对接（无统一 API）。"
            "请在 RecruitAdapter._fetch_keyed 中实现请求逻辑。"
        )


class SocialAdapter(KeyRequiredAdapter):
    """社媒：抖音 / 小红书 / 微博。

    社媒开放平台需企业资质与审核，设计上属「实时/小时」频率。
    """

    key = "social"
    signal_type = "social"
    name = "社媒平台"
    key_env = "SOCIAL_API_KEY"
    signup_url = ""
    frequency = "hourly"

    def _fetch_keyed(self, ctx: CollectContext, api_key: str) -> list[RawSignal]:
        raise NotImplementedError(
            "社媒数据需开放平台资质。请在 SocialAdapter._fetch_keyed 中实现请求逻辑。"
        )


class SupplyChainAdapter(KeyRequiredAdapter):
    """供应链：企业公告 / 行业数据库。"""

    key = "supply"
    signal_type = "supply"
    name = "供应链·企业公告"
    key_env = "SUPPLY_API_KEY"
    signup_url = ""
    frequency = "weekly"

    def _fetch_keyed(self, ctx: CollectContext, api_key: str) -> list[RawSignal]:
        raise NotImplementedError(
            "供应链数据需行业数据库授权。请在 SupplyChainAdapter._fetch_keyed 中实现请求逻辑。"
        )


KEYED_ADAPTERS = [
    PatentsViewAdapter,
    CrunchbaseAdapter,
    RecruitAdapter,
    SocialAdapter,
    SupplyChainAdapter,
]

__all__ = [
    "KeyRequiredAdapter", "PatentsViewAdapter", "CrunchbaseAdapter",
    "RecruitAdapter", "SocialAdapter", "SupplyChainAdapter", "KEYED_ADAPTERS",
]
