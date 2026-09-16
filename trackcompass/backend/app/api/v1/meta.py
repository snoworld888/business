"""元信息接口：健康检查、Agent 目录、编排图、系统配置。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import AGENT_CATALOG, default_workflow
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import ok
from app.services.embeddings import active_backend as embedding_backend
from app.services.embeddings import active_dim as embedding_dim
from app.services.embeddings import get_embedder
from app.services.llm import llm
from app.services.rag import get_rag
from app.services.vector_store import get_vector_store

router = APIRouter(tags=["系统元信息"])


@router.get("/health", summary="健康检查")
def health(db: Session = Depends(get_db)):  # noqa: B008
    db_ok = True
    try:
        db.execute(select(1))
    except Exception:  # pragma: no cover
        db_ok = False
    return ok({
        "status": "ok" if db_ok else "degraded",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "database": {"ok": db_ok, "backend": "sqlite" if settings.is_sqlite else "postgresql"},
        "llm": llm.health(),
        "embedding": {
            # 如实上报**实际生效**的后端与维度，而非配置文件里的期望值
            # （二者可能不一致：例如 EMBEDDING_BACKEND=ollama 时 bge-m3 是 1024 维，
            #  而 EMBEDDING_DIM 默认 1536 是给 OpenAI 用的）
            "backend": embedding_backend(),
            "model": get_embedder().model if hasattr(get_embedder(), "model") else None,
            "dim": embedding_dim(),
            "configured_dim": settings.EMBEDDING_DIM,
        },
        "vector_store": {"backend": settings.VECTOR_BACKEND,
                         "documents": get_vector_store().count()},
        "rag_documents": get_rag().count_total(),
    })


@router.get("/agents", summary="七 Agent 目录（《设计文档》§8）")
def agents():
    return ok({"agents": AGENT_CATALOG, "count": len(AGENT_CATALOG)})


@router.get("/workflow", summary="编排图结构（技术文档 §5.1）")
def workflow():
    wf = default_workflow()
    return ok({
        "name": wf.name,
        "max_loops": wf.max_loops,
        "parallel": wf.parallel,
        "layers": [
            {"layer": i, "parallel": len(layer) > 1,
             "agents": [{"key": a.key, "name": a.name} for a in layer]}
            for i, layer in enumerate(wf.layers, start=1)
        ],
        "feedback_loop": "决策综合判定为 verify/watch 且存在冲突点时，回灌冲突点重跑第一层",
        "edges": [
            {"from": "trend", "to": "market"}, {"from": "trend", "to": "competition"},
            {"from": "trend", "to": "tech"}, {"from": "demand", "to": "market"},
            {"from": "demand", "to": "decision"}, {"from": "market", "to": "decision"},
            {"from": "competition", "to": "decision"}, {"from": "tech", "to": "decision"},
            {"from": "decision", "to": "monitor"}, {"from": "monitor", "to": "trend"},
        ],
    })


@router.get("/modules", summary="八大功能模块与页面清单（《原型设计》§11.1）")
def modules():
    return ok({
        "modules": [
            {"key": "dashboard", "name": "工作台", "page": "P02", "priority": "P0"},
            {"key": "profile", "name": "能力画像", "page": "P03", "priority": "P0"},
            {"key": "trends", "name": "趋势雷达", "page": "P04", "priority": "P0"},
            {"key": "graph", "name": "机会图谱", "page": "P05", "priority": "P1"},
            {"key": "demand", "name": "需求探针", "page": "P06", "priority": "P0"},
            {"key": "scorecard", "name": "赛道评分", "page": "P07", "priority": "P0"},
            {"key": "experiments", "name": "验证实验", "page": "P08", "priority": "P1"},
            {"key": "reports", "name": "决策报告", "page": "P09", "priority": "P0"},
            {"key": "monitor", "name": "监控复盘", "page": "P10", "priority": "P1"},
            {"key": "settings", "name": "设置", "page": "P11", "priority": "P1"},
        ],
        "scorecard_dimensions": [
            "market_size", "demand", "tech_maturity", "competition",
            "business_model", "timing", "fit",
        ],
        "track_events": [
            "page_view", "opportunity_create", "trend_add_to_pool", "score_update",
            "experiment_create", "report_generate", "alert_click", "calibration_apply",
        ],
    })
