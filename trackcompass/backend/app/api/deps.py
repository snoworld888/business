"""API 依赖：租户解析与通用查询助手。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import current_user
from app.core.visibility import visible, visible_clause
from app.models import CompanyProfile, Scorecard, Signal, Track, User


def get_tenant_user(user: User = Depends(current_user)) -> User:  # noqa: B008
    return user


def tenant_id(user: User = Depends(current_user)) -> str:  # noqa: B008
    return user.tenant_id


def get_default_scorecard(db: Session, tenant_id: str) -> Scorecard:
    card = db.scalar(
        select(Scorecard).where(Scorecard.tenant_id == tenant_id,
                                Scorecard.is_default.is_(True))
    )
    if card is None:
        card = db.scalar(select(Scorecard).where(Scorecard.tenant_id == tenant_id))
    if card is None:
        raise HTTPException(404, detail="未找到评分卡，请先初始化")
    return card


def get_profile(db: Session, tenant_id: str) -> CompanyProfile | None:
    """取「当前公司画像」。

    多公司语义：一个租户可建多份公司画像，「当前」= **最近保存的那一份**
    （按 updated_at 倒序；同秒时用 created_at 兜底）。
    分析 / 评分 / 报告均以当前画像为准，因此在画像页切换并保存即等于切换主体。

    ⚠️ 同一「当前画像」口径也在 `app/services/analysis.py:build_profile_input` 使用，
    两处 ORDER BY 必须保持一致（服务层不反向依赖 API 层，故此处未抽公共函数）。
    """
    return db.scalar(
        select(CompanyProfile).where(CompanyProfile.tenant_id == tenant_id)
        .order_by(CompanyProfile.updated_at.desc(), CompanyProfile.created_at.desc())
    )


def current_capability_terms(db: Session, tenant_id: str) -> list[str]:
    """**当前公司**的能力词（技术栈 + 专利抽词）。

    用途：趋势雷达的「能力匹配」标注统一改为**查询时计算** ——

        matched = current_capability_terms(db, tid) ∩ 趋势的客观标签 `factors._tags`

    为什么必须实时算（实测教训 2026-09-15）：原实现把 matched 在落库那一刻冻结进
    `factors._capability`，却**没记录是给哪家公司算的** ⇒
    ① 切换公司后标注失效（可能显示"匹配的词不属于当前公司"）；
    ② 每次修复匹配逻辑都要回算全库历史数据（实测有 5 条趋势的标注一直停在旧逻辑上）。
    现在趋势只存客观标签，匹配实时算 —— 切公司即时生效，永不需回算。
    """
    from app.agents.base import AgentState
    from app.agents.trend import capability_terms

    profile = get_profile(db, tenant_id)
    if profile is None:
        return []
    state = AgentState(
        query="",
        profile={"tech_stack": profile.tech_stack or [],
                 "patents": profile.patents or []},
    )
    return capability_terms(state)


def get_profile_by_id(db: Session, tenant_id: str,
                      profile_id: str) -> CompanyProfile | None:
    """按 id 取指定公司画像（限本租户，避免越权读取他人档案）。"""
    return db.scalar(
        select(CompanyProfile).where(
            CompanyProfile.tenant_id == tenant_id,
            CompanyProfile.id == profile_id,
        )
    )


def list_profiles(db: Session, tenant_id: str) -> list[CompanyProfile]:
    """列出本租户全部公司画像，最近保存的排在前面（第 0 条即「当前」）。"""
    return list(db.scalars(
        select(CompanyProfile).where(CompanyProfile.tenant_id == tenant_id)
        .order_by(CompanyProfile.updated_at.desc(), CompanyProfile.created_at.desc())
    ).all())


def signal_stats(db: Session, tenant_id: str) -> dict:
    """按类型统计信号条数（**公共 + 本租户**，见 `core.visibility`）。

    信号是外部客观情报 ⇒ 公共数据，采集一次全租户共享；
    只有调用方自己上传的私有信号才归属租户。
    """
    rows = db.execute(
        select(Signal.signal_type, func.count(Signal.id))
        .where(visible_clause(Signal, tenant_id))
        .group_by(Signal.signal_type)
    ).all()
    return {r[0]: r[1] for r in rows}


def resolve_track(db: Session, name: str | None) -> Track | None:
    if not name:
        return None
    track = db.scalar(select(Track).where(Track.name == name))
    if track is None:
        track = Track(name=name, industry=name)
        db.add(track)
        db.commit()
        db.refresh(track)
    return track


def paginate(query, db: Session, skip: int = Query(0, ge=0),
             limit: int = Query(50, ge=1, le=200)):
    return db.scalars(query.offset(skip).limit(limit)).all()
