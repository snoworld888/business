"""数据库会话与基类。

技术文档 §3.3 使用 PostgreSQL + gen_random_uuid()，本地开发用 SQLite 等价实现，
主键统一由应用层生成 UUID 字符串，保证两种后端行为一致。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import DateTime, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.core.config import settings

connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
engine = create_engine(
    settings.sqlalchemy_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)

if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class UUIDMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表（开发期等价于 Flyway/Alembic 的 initial migration）。"""
    from app import models  # noqa: F401  确保所有模型已注册

    Base.metadata.create_all(bind=engine)
