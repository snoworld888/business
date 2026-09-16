"""认证接口：登录 / 注册 / 当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.errors import ok
from app.core.security import (
    PERMISSIONS,
    create_access_token,
    current_user,
    hash_password,
    verify_password,
)
from app.models import AuditLog, Tenant, User
from app.schemas import LoginRequest, RegisterRequest

router = APIRouter(tags=["认证"])


@router.post("/auth/login", summary="登录")
def login(payload: LoginRequest, db: Session = Depends(get_db)):  # noqa: B008
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, detail="邮箱或密码错误")
    if not user.is_active:
        raise HTTPException(403, detail="账号已禁用")

    user.last_login_at = utcnow()
    db.add(AuditLog(tenant_id=user.tenant_id, user_id=user.id, action="auth.login",
                    target_type="user", target_id=user.id))
    db.commit()

    token = create_access_token(user.id, {"role": user.role, "tenant_id": user.tenant_id})
    return ok({
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": _user_dict(user),
    }, message="登录成功")


@router.post("/auth/register", summary="注册（创建租户 + 管理员）")
def register(payload: RegisterRequest, db: Session = Depends(get_db)):  # noqa: B008
    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(400, detail="邮箱已被注册")

    tenant = Tenant(name=payload.tenant_name, plan="free", settings_json={})
    db.add(tenant)
    db.flush()

    user = User(tenant_id=tenant.id, email=payload.email, name=payload.name or payload.email,
                role="admin", password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id, {"role": user.role, "tenant_id": tenant.id})
    return ok({
        "access_token": token, "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": _user_dict(user),
    }, message="注册成功")


@router.get("/auth/me", summary="当前用户与权限")
def me(user: User = Depends(current_user)):  # noqa: B008
    return ok({**_user_dict(user), "permissions": sorted(PERMISSIONS.get(user.role, set()))})


@router.get("/auth/permissions", summary="角色权限矩阵")
def permission_matrix():
    return ok({
        "roles": [
            {"role": role, "permissions": sorted(perms)}
            for role, perms in PERMISSIONS.items()
        ],
        "all_permissions": sorted({p for perms in PERMISSIONS.values() for p in perms}),
    })


def _user_dict(user: User) -> dict:
    return {
        "id": user.id, "email": user.email, "name": user.name,
        "role": user.role, "tenant_id": user.tenant_id, "is_active": user.is_active,
    }
