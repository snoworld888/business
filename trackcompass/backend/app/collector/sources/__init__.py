"""数据源适配器子包。

每个模块对应《技术文档》§3.1 的一类数据源：

| 模块 | 数据源类型 | 实现形态 |
| --- | --- | --- |
| `gov_policy.py` | 政策 | 中国政府网政策文件库（JSON API） |
| `academic.py` | 技术·论文 | OpenAlex + Crossref（JSON API） |
| `tech_news.py` | 市场·产业媒体 | 36氪 / IT之家 / 界面（RSS） |
| `ccgp_bidding.py` | 市场·招标 | 中国政府采购网（HTML 解析） |
| `keyed_sources.py` | 专利/资本/人才/社媒/供应链 | 需密钥，已备好启用位 |
"""

from app.collector.sources.academic import CrossrefAdapter, OpenAlexAdapter
from app.collector.sources.ccgp_bidding import CCGPBiddingAdapter
from app.collector.sources.gov_policy import GovPolicyAdapter, RecentPolicyAdapter
from app.collector.sources.keyed_sources import KEYED_ADAPTERS
from app.collector.sources.tech_news import (
    ITHomeAdapter, JiemianAdapter, RSSAdapter, TechNews36KrAdapter,
)

PUBLIC_ADAPTERS = [
    GovPolicyAdapter,
    RecentPolicyAdapter,
    OpenAlexAdapter,
    CrossrefAdapter,
    TechNews36KrAdapter,
    ITHomeAdapter,
    JiemianAdapter,
    CCGPBiddingAdapter,
]

__all__ = [
    "RSSAdapter", "GovPolicyAdapter", "RecentPolicyAdapter",
    "OpenAlexAdapter", "CrossrefAdapter", "TechNews36KrAdapter",
    "ITHomeAdapter", "JiemianAdapter", "CCGPBiddingAdapter",
    "KEYED_ADAPTERS", "PUBLIC_ADAPTERS",
]
