"""系统管理接口（《原型设计》§5.11 / 《设计文档》§7.9）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import signal_stats, tenant_id
from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.errors import ok
from app.core.security import PERMISSIONS, hash_password, require_permission
from app.models import AuditLog, Role, Signal, User
from app.schemas import UserIn
from app.services.embeddings import active_backend as embedding_backend
from app.services.embeddings import active_dim as embedding_dim
from app.services.embeddings import get_embedder
from app.services.llm import llm

router = APIRouter(tags=["系统管理"])


@router.get("/admin/users", summary="用户列表")
def list_users(db: Session = Depends(get_db), tid: str = Depends(tenant_id),  # noqa: B008
               _=Depends(require_permission("user:manage"))):  # noqa: B008
    rows = db.scalars(select(User).where(User.tenant_id == tid)).all()
    return ok({
        "total": len(rows),
        "users": [
            {"id": u.id, "email": u.email, "name": u.name, "role": u.role,
             "is_active": u.is_active,
             "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
             "created_at": u.created_at.isoformat() if u.created_at else None}
            for u in rows
        ],
    })


@router.post("/admin/users", summary="邀请 / 新建用户")
def create_user(payload: UserIn, db: Session = Depends(get_db), tid: str = Depends(tenant_id),  # noqa: B008
                _=Depends(require_permission("user:manage"))):  # noqa: B008
    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(400, detail="邮箱已存在")
    user = User(tenant_id=tid, email=payload.email, name=payload.name or payload.email,
                role=payload.role,
                password_hash=hash_password(payload.password or "TrackCompass@2026"))
    db.add(user)
    db.add(AuditLog(tenant_id=tid, action="admin.user_create",
                    target_type="user", detail={"email": payload.email, "role": payload.role}))
    db.commit()
    db.refresh(user)
    return ok({"id": user.id, "email": user.email, "role": user.role,
               "initial_password": None if payload.password else "TrackCompass@2026"},
              message="用户已创建")


@router.patch("/admin/users/{user_id}", summary="修改用户角色 / 状态")
def update_user(user_id: str, role: str | None = None, is_active: bool | None = None,
                db: Session = Depends(get_db), tid: str = Depends(tenant_id),  # noqa: B008
                _=Depends(require_permission("user:manage"))):  # noqa: B008
    user = db.get(User, user_id)
    if user is None or user.tenant_id != tid:
        raise HTTPException(404, detail="用户不存在")
    if role:
        if role not in PERMISSIONS:
            raise HTTPException(400, detail=f"未知角色 {role}")
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    db.add(AuditLog(tenant_id=tid, action="admin.user_update", target_type="user",
                    target_id=user.id, detail={"role": user.role, "active": user.is_active}))
    db.commit()
    return ok({"id": user.id, "role": user.role, "is_active": user.is_active})


@router.get("/admin/roles", summary="角色与权限矩阵")
def list_roles(db: Session = Depends(get_db)):  # noqa: B008
    rows = db.scalars(select(Role)).all()
    return ok({
        "roles": [{"name": r.name, "display_name": r.display_name,
                   "permissions": r.permissions} for r in rows],
        "matrix": [{"role": role, "permissions": sorted(p)}
                   for role, p in PERMISSIONS.items()],
        "all_permissions": sorted({p for ps in PERMISSIONS.values() for p in ps}),
    })


@router.get("/admin/audit-logs", summary="审计日志（保留 180 天）")
def audit_logs(limit: int = Query(100, ge=1, le=500), action: str | None = None,
               db: Session = Depends(get_db), tid: str = Depends(tenant_id),  # noqa: B008
               _=Depends(require_permission("user:manage"))):  # noqa: B008
    stmt = select(AuditLog).where(AuditLog.tenant_id == tid)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    rows = db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return ok({
        "total": len(rows), "retention_days": 180,
        "logs": [
            {"id": a.id, "action": a.action, "target_type": a.target_type,
             "target_id": a.target_id, "detail": a.detail,
             "created_at": a.created_at.isoformat() if a.created_at else None}
            for a in rows
        ],
    })


@router.get("/admin/audit-logs/stats", summary="审计日志统计（按动作）")
def audit_stats(db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    rows = db.execute(
        select(AuditLog.action, func.count(AuditLog.id))
        .where(AuditLog.tenant_id == tid).group_by(AuditLog.action)
    ).all()
    return ok({"by_action": {r[0]: r[1] for r in rows}, "total": sum(r[1] for r in rows)})


@router.get("/admin/model-config", summary="模型配置（多模型路由）")
def model_config(db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    return ok({
        "llm": llm.health(),
        "embedding": {
            # 如实上报实际生效值（配置里的 EMBEDDING_MODEL 是 HF 名，
            #  Ollama 后端用的是 EMBEDDING_OLLAMA_MODEL，二者不可混用）
            "backend": embedding_backend(),
            "model": get_embedder().model if hasattr(get_embedder(), "model") else None,
            "dim": embedding_dim(),
            "configured": {
                "backend": settings.EMBEDDING_BACKEND,
                "model_hf": settings.EMBEDDING_MODEL,
                "model_ollama": settings.EMBEDDING_OLLAMA_MODEL,
                "dim": settings.EMBEDDING_DIM,
            },
        },
        "vector_backend": settings.VECTOR_BACKEND,
        "graph_backend": settings.GRAPH_BACKEND,
        "database": "sqlite" if settings.is_sqlite else "postgresql",
        "cache_ttl_hours": settings.LLM_CACHE_TTL_HOURS,
        "routes": [
            {"task": "简单任务", "model": settings.LLM_MODEL_SIMPLE},
            {"task": "复杂分析", "model": settings.LLM_MODEL_COMPLEX},
            {"task": "私有化", "model": settings.LLM_MODEL_PRIVATE},
        ],
    })


@router.get("/admin/overview", summary="系统概览（工作台统计卡）")
def overview(db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    from app.models import Alert, Experiment, Opportunity, Report, Trend

    def count(model, **filters):
        stmt = select(func.count()).select_from(model)
        for key, value in filters.items():
            stmt = stmt.where(getattr(model, key) == value)
        return db.scalar(stmt) or 0

    stats = signal_stats(db, tid)
    return ok({
        "opportunities": count(Opportunity, tenant_id=tid),
        "opportunities_verifying": count(Opportunity, tenant_id=tid, status="verifying"),
        "alerts_pending": count(Alert, tenant_id=tid, status="pending"),
        "experiments_running": count(Experiment, tenant_id=tid, status="running"),
        "reports": count(Report, tenant_id=tid),
        "trends": count(Trend, tenant_id=tid),
        "signals": sum(stats.values()),
        "signals_by_type": stats,
        "generated_at": utcnow().isoformat(),
    })
