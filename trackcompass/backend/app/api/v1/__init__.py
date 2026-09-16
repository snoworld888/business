"""API v1 路由聚合。"""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    alerts,
    auth,
    demand,
    experiments,
    meta,
    opportunities,
    profile,
    reports,
    scorecard,
    signals,
    trends,
)

api_router = APIRouter()
for module in (meta, auth, profile, signals, trends, opportunities, scorecard,
               demand, experiments, reports, alerts, admin):
    api_router.include_router(module.router)

__all__ = ["api_router"]
