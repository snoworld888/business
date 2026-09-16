"""请求/响应模型（Pydantic）。

字段命名与《技术文档》§6.2 核心 API 的请求体保持一致（snake_case），
响应统一由 core/errors.ok() 包成 {code, message, data} 信封。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- 认证
class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=64)
    name: str | None = None
    tenant_name: str = "我的公司"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict[str, Any]


# ---------------------------------------------------------------- 能力画像
class ProfileIn(BaseModel):
    # 多公司支持：传 id 表示更新该画像；不传则按 name 匹配同租户已有公司（视为同一家），
    # 仍无匹配才新建。这样「选择已有公司后保存」不会产生重复档案。
    id: str | None = None
    name: str
    industry: str | None = None
    founded_at: str | None = None
    scale: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    tech_description: str = ""
    patents: list[str] = Field(default_factory=list)
    talents: dict[str, int] = Field(default_factory=dict)
    funding: dict[str, Any] = Field(default_factory=dict)
    channels: list[str] = Field(default_factory=list)
    customers: list[str] = Field(default_factory=list)
    supply_chain: list[str] = Field(default_factory=list)


class ProfileOut(ORMBase):
    id: str
    name: str
    industry: str | None = None
    scale: str | None = None
    tech_stack: list = Field(default_factory=list)
    tech_description: str = ""
    patents: list = Field(default_factory=list)
    talents: dict = Field(default_factory=dict)
    funding: dict = Field(default_factory=dict)
    channels: list = Field(default_factory=list)
    customers: list = Field(default_factory=list)
    supply_chain: list = Field(default_factory=list)
    radar: dict = Field(default_factory=dict)
    recommended_tracks: list = Field(default_factory=list)
    boundary: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- 信号
class SignalIn(BaseModel):
    signal_type: Literal["policy", "patent", "paper", "funding", "recruit",
                         "social", "review", "forum", "bidding", "news", "supply"]
    source: str
    title: str
    content: str = ""
    url: str | None = None
    industry: str | None = None
    region: str | None = None
    keywords: list[str] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    sentiment: float = 0.0
    published_at: datetime | None = None
    # 公共数据标记：信号是**外部客观情报**（政策/论文/融资/招标…），
    # 不属于任何租户 ⇒ 默认写入公共层（`tenant_id IS NULL`），全租户共享。
    # 置 False 则仅本租户可见，适用于企业内部材料（如尽调记录）。
    # 见 `app/core/visibility.py`。
    is_public: bool = True


class SignalOut(ORMBase):
    id: str
    signal_type: str
    source: str
    title: str
    content: str
    url: str | None = None
    industry: str | None = None
    region: str | None = None
    keywords: list = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    sentiment: float
    published_at: datetime | None = None
    created_at: datetime


# ---------------------------------------------------------------- 趋势
class TrendOut(ORMBase):
    id: str
    name: str
    description: str
    industry: str | None = None
    confidence: float
    direction: str
    score: float
    factors: dict = Field(default_factory=dict)
    evidence: list = Field(default_factory=list)
    subscribed: bool
    in_pool: bool
    created_at: datetime


class TrendSubscribeIn(BaseModel):
    subscribed: bool = True


class TrendToPoolIn(BaseModel):
    track_id: str | None = None
    title: str | None = None
    summary: str | None = None


# ---------------------------------------------------------------- 机会
class OpportunityIn(BaseModel):
    title: str
    track_id: str | None = None
    summary: str = ""
    source_trend_id: str | None = None


class OpportunityOut(ORMBase):
    id: str
    title: str
    track_id: str | None = None
    summary: str
    status: str
    score: dict = Field(default_factory=dict)
    evidence: list = Field(default_factory=list)
    risk_alerts: list = Field(default_factory=list)
    source_trend_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AnalyzeIn(BaseModel):
    industry: str | None = None
    region: str | None = None
    days: int = 3650
    max_loops: int = 1
    agents: list[str] | None = None
    persist: bool = True


# ---------------------------------------------------------------- 评分卡
class ScorecardIn(BaseModel):
    name: str = "默认赛道评分卡"
    weights: dict[str, float] | None = None
    dimensions: list[dict[str, Any]] | None = None
    is_default: bool = True


class ScorecardOut(ORMBase):
    id: str
    name: str
    weights: dict = Field(default_factory=dict)
    dimensions: list = Field(default_factory=list)
    is_default: bool
    version: str
    status: str
    created_at: datetime
    updated_at: datetime


class WeightsIn(BaseModel):
    weights: dict[str, float]
    change_reason: str = ""
    version: str | None = None


class ScorePreviewIn(BaseModel):
    opportunity_id: str
    weights: dict[str, float]


# ---------------------------------------------------------------- 需求探针
class DemandProbeIn(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    time_range: str = "3650d"
    industry: str | None = None


class DemandProbeOut(BaseModel):
    task_id: str
    status: str
    pain_points: list[dict[str, Any]] = Field(default_factory=list)
    suggested_experiments: list[str] = Field(default_factory=list)
    demand_score_5: float = 0.0
    coverage: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- 实验
class ExperimentIn(BaseModel):
    opportunity_id: str | None = None
    title: str = ""
    hypothesis: str
    method: Literal["landing_page", "interview", "survey", "poc", "presale", "channel_test"]
    metrics: dict[str, Any] = Field(default_factory=dict)


class ExperimentUpdateIn(BaseModel):
    metrics: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    conclusion: str | None = None
    status: Literal["draft", "running", "completed", "archived", "cancelled"] | None = None
    progress: float | None = None


class ExperimentOut(ORMBase):
    id: str
    opportunity_id: str | None = None
    title: str
    hypothesis: str
    method: str
    metrics: dict = Field(default_factory=dict)
    result: dict = Field(default_factory=dict)
    conclusion: str
    status: str
    progress: float
    applied_to_score: bool
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime


# ---------------------------------------------------------------- 报告
class ReportIn(BaseModel):
    opportunity_id: str
    template: Literal["investment_memo", "project_proposal", "trend_brief"] = "investment_memo"
    format: Literal["markdown", "html", "json", "pptx", "docx"] = "markdown"
    title: str | None = None


class ReportOut(ORMBase):
    id: str
    opportunity_id: str | None = None
    title: str
    template: str
    version: str
    status: str
    format: str
    evidence_count: int
    page_count: int
    download_url: str | None = None
    created_at: datetime


# ---------------------------------------------------------------- 预警
class AlertOut(ORMBase):
    id: str
    track_id: str | None = None
    opportunity_id: str | None = None
    signal_type: str
    rule_name: str
    level: str
    title: str
    summary: str
    payload: dict = Field(default_factory=dict)
    status: str
    handled_at: datetime | None = None
    created_at: datetime


class AlertActionIn(BaseModel):
    action: Literal["handle", "ignore", "reopen"] = "handle"
    note: str = ""


class ReplayIn(BaseModel):
    decision_id: str
    decision_at: str | None = None
    basis: dict[str, Any] = Field(default_factory=dict)
    outcome: dict[str, Any] = Field(default_factory=dict)
    review: str = ""


# ---------------------------------------------------------------- 管理
class UserIn(BaseModel):
    email: str
    name: str | None = None
    role: Literal["admin", "strategy", "product", "investment", "expert"] = "product"
    password: str | None = None


class UserOut(ORMBase):
    id: str
    email: str
    name: str | None = None
    role: str
    is_active: bool
    created_at: datetime


class DataSourceOut(BaseModel):
    key: str
    name: str
    connected: bool
    last_sync: str | None = None
    signal_count: int = 0


class CalibrationApplyIn(BaseModel):
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    change_reason: str = "根据复盘结果校准"


# ---------------------------------------------------------------- 图谱
class GraphQueryIn(BaseModel):
    label: str
    key: str
    rel: str | None = None
    depth: int = 1


# ---------------------------------------------------------------- 信号采集
class CollectIn(BaseModel):
    """触发一轮信号采集（《技术文档》§3.1 数据源接入）。"""

    sources: list[str] | None = None          # 适配器 key；缺省用默认启用集
    keywords: list[str] | None = None         # 检索词；缺省取当前公司画像技术栈
    limit_per_source: int = Field(default=10, ge=1, le=50)
    industry: str | None = None
    dry_run: bool = False                     # 只采集清洗，不写库
    reindex: bool = True                      # 写库后重建向量索引与图谱
