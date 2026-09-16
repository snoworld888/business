"""数据源注册表：适配器清单、默认启用集、按需构建。

对齐《技术文档》§3.1 的七类数据源与 <《设计文档》§7.2 的「覆盖至少 5 个数据源」验收项。

启用策略：
● `DEFAULT_ENABLED` —— 无需密钥、已验证可用的公开源，默认参与采集。
● `KEYED_ADAPTERS` —— 需授权，`available()` 返回 False 时被标记为 `skipped`，
  在采集报告里如实呈现「为什么没采」，而不是静默缺席。
"""

from __future__ import annotations

from typing import Any

from app.collector.base import HttpClient, SourceAdapter
from app.collector.sources.academic import CrossrefAdapter, OpenAlexAdapter
from app.collector.sources.ccgp_bidding import CCGPBiddingAdapter
from app.collector.sources.gov_policy import GovPolicyAdapter, RecentPolicyAdapter
from app.collector.sources.keyed_sources import KEYED_ADAPTERS
from app.collector.sources.tech_news import (
    ITHomeAdapter, JiemianAdapter, TechNews36KrAdapter,
)

# key → 适配器类。插入顺序即默认执行顺序。
ADAPTERS: dict[str, type[SourceAdapter]] = {
    "gov_policy": GovPolicyAdapter,
    "openalex": OpenAlexAdapter,
    "crossref": CrossrefAdapter,
    "news_36kr": TechNews36KrAdapter,
    "news_ithome": ITHomeAdapter,
    "news_jiemian": JiemianAdapter,
    "ccgp_bidding": CCGPBiddingAdapter,
    "gov_policy_recent": RecentPolicyAdapter,
    **{cls.key: cls for cls in KEYED_ADAPTERS},
}

# 默认启用：公开可用且已实测通过的源
DEFAULT_ENABLED: list[str] = [
    "gov_policy",     # 政策（设计 §3.1「政策」）
    "openalex",       # 技术·论文（设计 §3.1「技术」）
    "crossref",       # 技术·论文（冗余源，符合「多源冗余」风控要求）
    "news_36kr",      # 市场·产业媒体
    "news_ithome",    # 市场·产业媒体
    "ccgp_bidding",   # 市场·招标（设计 §3.1「市场」）
]


def build_adapters(keys: list[str] | None = None,
                   client: HttpClient | None = None) -> list[SourceAdapter]:
    """按 key 列表构建适配器实例；未指定时用 `DEFAULT_ENABLED`。"""
    names = keys if keys else DEFAULT_ENABLED
    shared = client or HttpClient()
    out: list[SourceAdapter] = []
    for name in names:
        cls = ADAPTERS.get(name)
        if cls is None:
            continue
        out.append(cls(shared))
    return out


def describe_adapters(include_keyed: bool = True) -> list[dict[str, Any]]:
    """适配器清单（供接口/前端展示「数据源覆盖」）。"""
    rows: list[dict[str, Any]] = []
    for key, cls in ADAPTERS.items():
        if cls.requires_key and not include_keyed:
            continue
        inst = cls.__new__(cls)          # 只为读取类属性，不建立 HTTP 连接
        ok, reason = inst.available()
        rows.append({
            "key": key,
            "name": cls.name,
            "signal_type": cls.signal_type,
            "homepage": cls.homepage,
            "frequency": cls.frequency,
            "requires_key": cls.requires_key,
            "key_env": cls.key_env or None,
            "enabled_by_default": key in DEFAULT_ENABLED,
            "available": ok,
            "unavailable_reason": None if ok else reason,
        })
    return rows


def keys_by_frequency(frequency: str,
                      only_default: bool = True) -> list[str]:
    """按《技术文档》§3.1 的采集频率筛选适配器 key。

    设计表格给出的频率是：政策/技术/资本/人才/市场 = 每日，社媒 = 实时（按小时），
    供应链 = 每周。调度侧据此分组调用，避免「每小时都去抓政策文件」这类浪费。

    `only_default=True` 时只回默认启用集里的源（即公开可用的）。
    """
    out: list[str] = []
    for key, cls in ADAPTERS.items():
        if only_default and key not in DEFAULT_ENABLED:
            continue
        if cls.frequency == frequency:
            out.append(key)
    return out


def frequencies() -> list[str]:
    """全部适配器出现过的频率取值（去重、按执行密度排序）。"""
    order = ["hourly", "daily", "weekly", "monthly"]
    seen = {cls.frequency for cls in ADAPTERS.values()}
    known = [f for f in order if f in seen]
    return known + sorted(seen - set(known))


__all__ = [
    "ADAPTERS", "DEFAULT_ENABLED", "build_adapters", "describe_adapters",
    "keys_by_frequency", "frequencies",
]
