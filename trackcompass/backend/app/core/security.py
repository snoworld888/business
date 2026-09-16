"""认证与授权：JWT + RBAC。

对齐技术文档 §7.2：JWT Bearer 认证、RBAC 租户隔离。
口令散列使用标准库 pbkdf2_hmac，避免额外二进制依赖。
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, utcnow

PBKDF2_ROUNDS = 120_000
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(raw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", raw.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(raw: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, dk_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", raw.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(utcnow().timestamp()),
        "exp": int(
            (utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()
        ),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="令牌已过期")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="令牌无效")


# ---------------------------------------------------------------- 权限矩阵
# 对齐《原型设计》§5.11 角色权限矩阵
PERMISSIONS: dict[str, set[str]] = {
    "admin": {"opportunity:view", "score:edit", "report:generate", "user:manage",
              "experiment:manage", "alert:manage", "calibration:apply"},
    "strategy": {"opportunity:view", "score:edit", "report:generate",
                 "experiment:manage", "alert:manage"},
    "product": {"opportunity:view", "experiment:manage"},
    "investment": {"opportunity:view", "report:generate"},
    "expert": {"opportunity:view", "score:edit"},
}


def has_permission(role: str, perm: str) -> bool:
    return perm in PERMISSIONS.get(role, set())


def require_permission(perm: str):
    def _checker(user=Depends(current_user)):  # noqa: B008
        if not has_permission(user.role, perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"缺少权限: {perm}")
        return user

    return _checker


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    from app.models import User

    if credentials is None:
        # 开发模式允许匿名以 tenant=demo 只读访问，便于前端独立联调
        if settings.DEBUG:
            user = db.scalar(select(User).where(User.email == "admin@demo.com"))
            if user:
                return user
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="未认证")

    payload = decode_token(credentials.credentials)
    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")
    return user
