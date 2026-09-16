"""需求探针接口（技术文档 §6.2.5）。

POST /demand/probe  → 提交任务（同步执行，返回 task_id）
GET  /demand/probe/{task_id} → 查询结果

生产环境可改为 Celery + Redis 异步任务，接口契约不变（技术文档 §5.6 异步任务）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import tenant_id
from app.agents.base import AgentState, SignalView
from app.agents.demand import DemandDiscoveryAgent
from app.core.db import get_db
from app.core.errors import ok
from app.core.visibility import visible
from app.models import AuditLog, Signal
from app.schemas import DemandProbeIn
from app.services.rag import get_rag

router = APIRouter(tags=["需求探针"])

# 任务缓存：生产环境替换为 Redis / DB 表
_TASKS: dict[str, dict] = {}


@router.post("/demand/probe", summary="提交需求探针任务")
def submit_probe(payload: DemandProbeIn, db: Session = Depends(get_db),  # noqa: B008
                 tid: str = Depends(tenant_id)):  # noqa: B008
    task_id = str(uuid.uuid4())
    stmt = visible(select(Signal), Signal, tid)
    if payload.industry:
        stmt = stmt.where(Signal.industry == payload.industry)
    if payload.sources:
        stmt = stmt.where(Signal.signal_type.in_(payload.sources))
    signals = db.scalars(stmt).all()

    if payload.keywords:
        wanted = [k.lower() for k in payload.keywords]
        signals = [
            s for s in signals
            if any(k in s.title.lower() or k in (s.content or "").lower() for k in wanted)
        ]

    agent = DemandDiscoveryAgent()
    state = AgentState(
        query=" ".join(payload.keywords) or (payload.industry or "需求探针"),
        industry=payload.industry, rag=get_rag(),
        signals=[SignalView.from_orm(s) for s in signals],
    )
    result = agent.run(state)

    _TASKS[task_id] = {
        "task_id": task_id, "status": "completed", "tenant_id": tid,
        **{k: v for k, v in result.items()},
        "signal_count": len(signals),
        "keywords": payload.keywords,
    }
    db.add(AuditLog(tenant_id=tid, action="demand.probe", target_type="demand_probe",
                    detail={"task_id": task_id, "keywords": payload.keywords,
                            "matched": len(signals)}))
    db.commit()

    return ok({
        "task_id": task_id,
        "status": "completed",
        "matched_signals": len(signals),
        "pain_points": result.get("pain_points", []),
        "suggested_experiments": result.get("suggested_experiments", []),
        "demand_score_5": result.get("demand_score_5", 0),
        "coverage": result.get("coverage", {}),
    }, message="分析完成")


@router.get("/demand/probe/{task_id}", summary="查询需求探针结果")
def get_probe(task_id: str, tid: str = Depends(tenant_id)):  # noqa: B008
    task = _TASKS.get(task_id)
    if task is None or task.get("tenant_id") != tid:
        raise HTTPException(404, detail="任务不存在")
    task = {k: v for k, v in task.items() if k != "tenant_id"}
    return ok(task)


@router.get("/demand/pain-points", summary="痛点聚类聚合视图（跨机会）")
def pain_points(industry: str | None = None, db: Session = Depends(get_db),  # noqa: B008
                tid: str = Depends(tenant_id)):  # noqa: B008
    stmt = visible(select(Signal), Signal, tid)
    if industry:
        stmt = stmt.where(Signal.industry == industry)
    signals = db.scalars(stmt).all()
    agent = DemandDiscoveryAgent()
    state = AgentState(query=industry or "全量", industry=industry,
                       signals=[SignalView.from_orm(s) for s in signals], rag=None)
    result = agent.rule(state)
    return ok({
        "industry": industry,
        "pain_points": result.get("pain_points", []),
        "demand_score_5": result.get("demand_score_5", 0),
        "coverage": result.get("coverage", {}),
        "sample_size": result.get("sample_size", 0),
    })
