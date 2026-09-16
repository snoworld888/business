"""TrackCompass 后端入口。

启动：
    python -m app.main            # 开发
    uvicorn app.main:app --reload --port 8010
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api import api_router
from app.core.config import BASE_DIR, settings
from app.core.db import SessionLocal, init_db
from app.core.errors import register_exception_handlers
from app.core.logging import TraceMiddleware, setup_logging

logger = logging.getLogger(__name__)

DESCRIPTION = """
**赛道罗盘 TrackCompass** —— AI 驱动的科技公司商机发现与赛道选择决策系统。

把「凭感觉选方向」变成 **信号扫描 → 机会生成 → 需求验证 → 技术匹配 → 决策报告 → 持续监控**
的可追溯流水线。

* 四层架构：信号层 / 认知层 / 决策层 / 行动层
* 七个 AI Agent：趋势侦察 · 需求挖掘 · 市场测算 · 竞争情报 · 技术评估 · 决策综合 · 监控预警
* 八大功能模块，每个结论均可下钻到原始信号
"""


def create_app() -> FastAPI:
    setup_logging(logging.DEBUG if settings.DEBUG else logging.INFO)

    app = FastAPI(
        title=settings.APP_NAME,
        description=DESCRIPTION,
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url=f"{settings.API_PREFIX}/openapi.json",
    )

    app.add_middleware(TraceMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.DEBUG else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-trace-id"],
    )
    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.API_PREFIX)

    report_dir = BASE_DIR / "generated_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/reports", StaticFiles(directory=str(report_dir)), name="reports")

    ui_enabled = settings.SERVE_FRONTEND and settings.frontend_dist.is_dir()

    # ---------- 单端口托管前端构建产物 ----------
    # ⚠️ 顺序敏感：显式路由（/ 与 /favicon.ico）必须注册在 mount("/") 之前，
    # 否则会被 StaticFiles(html=True) 抢先匹配，导致 favicon 请求 404。
    # 前端使用 HashRouter，因此无需服务端做 history fallback。
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        """避免浏览器默认请求 /favicon.ico 时产生 404 噪声。"""
        icon = settings.frontend_dist / "favicon.svg"
        if icon.is_file():
            return FileResponse(icon, media_type="image/svg+xml")
        return Response(status_code=204)

    @app.get("/", include_in_schema=False)
    def index():
        if ui_enabled:
            return FileResponse(settings.frontend_dist / "index.html")
        return JSONResponse({
            "app": settings.APP_NAME,
            "version": "1.0.0",
            "docs": "/docs",
            "api_prefix": settings.API_PREFIX,
            "health": f"{settings.API_PREFIX}/health",
            "hint": "前端未构建，执行 cd frontend && npm run build 后即由本端口托管 UI",
        })

    if ui_enabled:
        app.mount("/", StaticFiles(directory=str(settings.frontend_dist), html=True),
                  name="frontend")
        logger.info("已托管前端构建产物: %s", settings.frontend_dist)
    elif settings.SERVE_FRONTEND:
        logger.warning("SERVE_FRONTEND=true 但未找到 %s，请先执行 npm run build",
                       settings.frontend_dist)

    @app.on_event("startup")
    def _startup() -> None:
        init_db()
        logger.info("TrackCompass 启动完成 env=%s db=%s ui=%s", settings.APP_ENV,
                    "sqlite" if settings.is_sqlite else "postgresql",
                    "served" if ui_enabled else "external")

    return app


app = create_app()


def bootstrap(seed: bool = True, reset: bool = False,
              with_signals: bool = False) -> dict:
    """初始化数据库并写种子数据，返回摘要。

    `with_signals=False`（默认）：**不写 40 条演示信号**。
    信号层的正确来源是采集器（`app/collector/`，见《技术文档》§3.1），
    演示信号只有 `example.com` 占位 URL，写进库会污染趋势雷达。
    需要离线演示数据时显式传 `with_signals=True`。

    已存在的库不受影响 —— 本函数只在空库时生效（或有信号/赛道时直接跳过）。
    """
    init_db()
    if not seed:
        return {"seeded": False}
    from app.seed import seed_all

    db = SessionLocal()
    try:
        summary = seed_all(db, reset=reset, with_signals=with_signals)
        if not summary.get("skipped"):
            from app.services.analysis import refresh_graph, reset_and_reindex

            count = reset_and_reindex(db)
            graph = refresh_graph(db)
            summary["indexed"] = count
            summary["graph"] = graph
        return summary
    finally:
        db.close()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8010, reload=False)
