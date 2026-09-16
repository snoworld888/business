"""领域模型。

对齐《技术文档》§3.3 数据库设计：
  tenants / users / roles / company_profiles / tracks / opportunities /
  scorecards / experiments / reports / alerts
并在其基础上补齐 MVP 必需的四张表：
  signals（信号层落地）、trends（趋势雷达产物）、
  scorecard_versions（评分模型版本管理）、audit_logs（审计日志 §7.2）。

字段类型差异说明（可替换设计，非降级）：
  - PostgreSQL VECTOR(1536)  → SQLite 下为 JSON 数组，语义等价；
    生产切换时由 services/vector_store.py 的 Milvus/pgvector 适配器承载索引。
  - JSONB                    → JSON。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDMixin


# ============================================================ 用户与权限
class Tenant(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(50), default="free", nullable=False)
    # 企业能力画像的租户级配置（数据源、模型路由等）
    settings_json: Mapped[dict | None] = mapped_column(JSON, default=dict)

    users: Mapped[list["User"]] = relationship(back_populates="tenant")


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), default="member", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped["Tenant"] = relationship(back_populates="users")


class Role(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    permissions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


# ============================================================ 信号层
class Signal(Base, UUIDMixin, TimestampMixin):
    """原始信号数据：政策 / 论文专利 / 融资 / 招聘 / 社媒 / 招标 / 财报 / 供应链。"""

    __tablename__ = "signals"

    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    signal_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str | None] = mapped_column(String(1000))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    industry: Mapped[str | None] = mapped_column(String(100), index=True)
    region: Mapped[str | None] = mapped_column(String(100))
    # 行业关键词标签，供趋势聚合与需求探针复用
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    entities: Mapped[dict] = mapped_column(JSON, default=dict)  # NER 结果

    # 结构化字段：融资额、政策力度、招聘人数等
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)

    # 向量与去重
    embedding: Mapped[list | None] = mapped_column(JSON)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    __table_args__ = (Index("ix_signals_type_published", "signal_type", "published_at"),)


class Trend(Base, UUIDMixin, TimestampMixin):
    """趋势雷达产物：趋势侦察 Agent 输出。"""

    __tablename__ = "trends"

    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    industry: Mapped[str | None] = mapped_column(String(100), index=True)
    region: Mapped[str | None] = mapped_column(String(100))

    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    direction: Mapped[str] = mapped_column(String(20), default="tailwind")  # tailwind|headwind
    score: Mapped[float] = mapped_column(Float, default=0.0)

    # 算法中间量，保证评分可解释（技术文档 §4.2）
    factors: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[list] = mapped_column(JSON, default=list)  # [{signal_id, source, url, title}]

    subscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    in_pool: Mapped[bool] = mapped_column(Boolean, default=False)


# ============================================================ 能力画像
class CompanyProfile(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "company_profiles"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    industry: Mapped[str | None] = mapped_column(String(100))
    founded_at: Mapped[str | None] = mapped_column(String(20))
    scale: Mapped[str | None] = mapped_column(String(50))

    tech_stack: Mapped[list] = mapped_column(JSON, default=list)
    tech_description: Mapped[str] = mapped_column(Text, default="")
    patents: Mapped[list] = mapped_column(JSON, default=list)
    talents: Mapped[dict] = mapped_column(JSON, default=dict)
    funding: Mapped[dict] = mapped_column(JSON, default=dict)
    channels: Mapped[list] = mapped_column(JSON, default=list)
    customers: Mapped[list] = mapped_column(JSON, default=list)
    supply_chain: Mapped[list] = mapped_column(JSON, default=list)

    # 能力向量：PostgreSQL 为 VECTOR(1536)，SQLite 为 JSON 数组
    capability_vector: Mapped[list | None] = mapped_column(JSON)
    radar: Mapped[dict] = mapped_column(JSON, default=dict)
    recommended_tracks: Mapped[list] = mapped_column(JSON, default=list)
    boundary: Mapped[dict] = mapped_column(JSON, default=dict)


# ============================================================ 赛道与机会
class Track(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "tracks"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("tracks.id"))
    status: Mapped[str] = mapped_column(String(50), default="active")
    industry: Mapped[str | None] = mapped_column(String(100), index=True)

    market: Mapped[dict] = mapped_column(JSON, default=dict)  # TAM/SAM/SOM
    competition: Mapped[dict] = mapped_column(JSON, default=dict)

    parent: Mapped["Track | None"] = relationship(remote_side="Track.id")
    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="track")


class Opportunity(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "opportunities"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    track_id: Mapped[str | None] = mapped_column(ForeignKey("tracks.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")

    # status: draft | screening | verifying | validated | archived
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    score: Mapped[dict] = mapped_column(JSON, default=dict)      # 评分卡结果
    evidence: Mapped[list] = mapped_column(JSON, default=list)   # 证据链
    risk_alerts: Mapped[list] = mapped_column(JSON, default=list)
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)   # 七 Agent 汇总
    source_trend_id: Mapped[str | None] = mapped_column(String(36))

    track: Mapped["Track | None"] = relationship(back_populates="opportunities")


# ============================================================ 评分卡
class Scorecard(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "scorecards"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    weights: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    dimensions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[str] = mapped_column(String(20), default="V1.0")
    status: Mapped[str] = mapped_column(String(20), default="active")


class ScorecardVersion(Base, UUIDMixin, TimestampMixin):
    """模型校准记录：对齐《原型设计》§5.10 模型校准 / 版本对比。"""

    __tablename__ = "scorecard_versions"

    scorecard_id: Mapped[str] = mapped_column(ForeignKey("scorecards.id"), index=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    change_reason: Mapped[str] = mapped_column(Text, default="")
    diff: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(36))


# ============================================================ 验证实验
class Experiment(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "experiments"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(100), nullable=False)  # landing_page|interview|...
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    conclusion: Mapped[str] = mapped_column(Text, default="")
    # draft → running → completed → archived，可分支 cancelled（技术文档 §4.6 状态机）
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_to_score: Mapped[bool] = mapped_column(Boolean, default=False)


# ============================================================ 报告与预警
class Report(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "reports"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    template: Mapped[str] = mapped_column(String(100), default="investment_memo")
    version: Mapped[str] = mapped_column(String(20), default="V1.0")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|generated
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    format: Mapped[str] = mapped_column(String(50), default="markdown")
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    download_url: Mapped[str | None] = mapped_column(String(500))


class Alert(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "alerts"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    track_id: Mapped[str | None] = mapped_column(ForeignKey("tracks.id"), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(36))
    signal_type: Mapped[str] = mapped_column(String(100), default="")
    rule_name: Mapped[str] = mapped_column(String(100), default="")
    level: Mapped[str] = mapped_column(String(20), default="warning")  # info|warning|critical
    title: Mapped[str] = mapped_column(String(255), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)  # pending|handled|ignored
    handled_by: Mapped[str | None] = mapped_column(String(36))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base, UUIDMixin, TimestampMixin):
    """操作日志，保留 180 天（技术文档 §7.2）。"""

    __tablename__ = "audit_logs"

    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))


__all__ = [
    "Tenant", "User", "Role", "Signal", "Trend", "CompanyProfile", "Track",
    "Opportunity", "Scorecard", "ScorecardVersion", "Experiment", "Report",
    "Alert", "AuditLog",
]
