"""机会图谱与赛道评分接口（技术文档 §6.2.3 / §6.2.4）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_default_scorecard, resolve_track, tenant_id
from app.core.db import get_db
from app.core.errors import ok
from app.models import AuditLog, Opportunity, Track
from app.schemas import AnalyzeIn, OpportunityIn, ScorePreviewIn
from app.services.analysis import (
    persist_analysis,
    profile_used,
    run_analysis,
    upsert_trends,
)
from app.services.graph import build_from_signals, get_graph_store
from app.services.rag import get_rag
from app.services.scoring import explain_score, rank_opportunities, risk_alerts_from, total_score

logger = logging.getLogger(__name__)
router = APIRouter(tags=["机会图谱"])


# ---------------------------------------------------------------- 查询
@router.get("/opportunities", summary="机会列表")
def list_opportunities(
    track_id: str | None = None,
    status: str | None = None,
    sort: str = Query("score", pattern="^(score|created)$"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),  # noqa: B008
    tid: str = Depends(tenant_id),  # noqa: B008
):
    stmt = select(Opportunity).where(Opportunity.tenant_id == tid)
    if track_id:
        stmt = stmt.where(Opportunity.track_id == track_id)
    if status:
        stmt = stmt.where(Opportunity.status == status)
    rows = db.scalars(stmt.limit(limit)).all()

    items = [
        {
            "id": o.id, "title": o.title, "summary": o.summary, "status": o.status,
            "track_id": o.track_id, "total_score": (o.score or {}).get("total_score", 0),
            "verdict": (o.score or {}).get("verdict", {}),
            "evidence_count": len(o.evidence or []),
            "risk_count": len(o.risk_alerts or []),
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in rows
    ]
    if sort == "score":
        items = rank_opportunities(items)
    else:
        items.sort(key=lambda x: x["created_at"] or "", reverse=True)

    return ok({"total": len(items), "opportunities": items})


@router.get("/opportunities/{opportunity_id}", summary="机会详情（含七 Agent 分析）")
def opportunity_detail(opportunity_id: str, db: Session = Depends(get_db),  # noqa: B008
                       tid: str = Depends(tenant_id)):  # noqa: B008
    opp = db.get(Opportunity, opportunity_id)
    if opp is None or opp.tenant_id != tid:
        raise HTTPException(404, detail="机会不存在")
    track = db.get(Track, opp.track_id) if opp.track_id else None
    return ok({
        "id": opp.id, "title": opp.title, "summary": opp.summary,
        "status": opp.status, "track": track.name if track else None,
        "score": opp.score, "evidence": opp.evidence,
        "risk_alerts": opp.risk_alerts, "analysis": opp.analysis,
        "created_at": opp.created_at.isoformat() if opp.created_at else None,
        "updated_at": opp.updated_at.isoformat() if opp.updated_at else None,
    })


@router.post("/opportunities", summary="手工创建机会卡片")
def create_opportunity(payload: OpportunityIn, db: Session = Depends(get_db),  # noqa: B008
                       tid: str = Depends(tenant_id)):  # noqa: B008
    track = db.get(Track, payload.track_id) if payload.track_id else None
    opp = Opportunity(
        tenant_id=tid, title=payload.title, summary=payload.summary,
        track_id=payload.track_id, status="draft",
        source_trend_id=payload.source_trend_id,
    )
    db.add(opp)
    db.commit()
    db.refresh(opp)
    return ok({"id": opp.id, "title": opp.title, "track": track.name if track else None,
               "status": opp.status}, message="机会已创建")


# ---------------------------------------------------------------- 分析
@router.post("/opportunities/{opportunity_id}/analyze", summary="执行七 Agent 全链路分析")
def analyze_opportunity(opportunity_id: str, payload: AnalyzeIn,
                        db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    opp = db.get(Opportunity, opportunity_id)
    if opp is None or opp.tenant_id != tid:
        raise HTTPException(404, detail="机会不存在")

    industry = payload.industry
    if industry is None and opp.track_id:
        from app.models import Track

        track = db.get(Track, opp.track_id)
        industry = track.name if track else None

    analysis = run_analysis(
        db, tenant_id=tid, query=opp.title, industry=industry, region=payload.region,
        days=payload.days, max_loops=payload.max_loops, selected_agents=payload.agents,
    )
    if payload.persist:
        persist_analysis(db, tenant_id=tid, opportunity=opp, analysis=analysis)
        db.add(AuditLog(tenant_id=tid, action="opportunity.analyze",
                        target_type="opportunity", target_id=opp.id,
                        detail={"total_score": (opp.score or {}).get("total_score")}))

    return ok({
        "opportunity_id": opp.id,
        "status": opp.status if payload.persist else "preview",
        "total_score": (opp.score or {}).get("total_score", 0) if payload.persist
        else (analysis["results"].get("decision") or {}).get("total_score", 0),
        "score": opp.score if payload.persist else analysis["results"].get("decision"),
        "evidence": analysis["evidence"],
        "trace": analysis["trace"],
        "loops": analysis["loops"],
        "errors": analysis["errors"],
        "signal_count": analysis["signal_count"],
        # 本次分析**依据了哪些公司能力**（技术栈 + 专利 → 抽词 → 命中的趋势）。
        # 趋势雷达的口号是"外部信号生成趋势、公司画像只做标注"，
        # 这个字段让"标注"这一步变得可核查 —— 否则用户只能看到一个 ⚑ 结果。
        "profile_used": profile_used(db, tid, analysis),
    }, message="分析完成")


@router.get("/opportunities/{opportunity_id}/score", summary="赛道评分与可解释性")
def opportunity_score(opportunity_id: str, db: Session = Depends(get_db),  # noqa: B008
                      tid: str = Depends(tenant_id)):  # noqa: B008
    opp = db.get(Opportunity, opportunity_id)
    if opp is None or opp.tenant_id != tid:
        raise HTTPException(404, detail="机会不存在")
    score = opp.score or {}
    dims = score.get("dimensions") or []
    return ok({
        "opportunity_id": opp.id,
        "title": opp.title,
        "total_score": score.get("total_score", 0),
        "dimensions": dims,
        "explained": score.get("explained") or explain_score(dims),
        "risk_alerts": score.get("risk_alerts") or risk_alerts_from(
            {d["key"]: d["score"] for d in dims}),
        "verdict": score.get("verdict", {}),
        "conflicts": score.get("conflicts", []),
        "evidence": opp.evidence,
    })


@router.post("/opportunities/{opportunity_id}/score/preview", summary="权重调整实时预览")
def preview_score(opportunity_id: str, payload: ScorePreviewIn,
                  db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    opp = db.get(Opportunity, opportunity_id)
    if opp is None or opp.tenant_id != tid:
        raise HTTPException(404, detail="机会不存在")
    dims = (opp.score or {}).get("dimensions") or []
    if not dims:
        raise HTTPException(400, detail="该机会尚未分析，无法预览评分")

    weights = payload.weights
    rescaled = []
    for d in dims:
        item = dict(d)
        item["weight"] = float(weights.get(d["key"], d.get("weight", 0)))
        rescaled.append(item)

    return ok({
        "total_score": total_score(rescaled),
        "explained": explain_score(rescaled),
        "risk_alerts": risk_alerts_from({d["key"]: d["score"] for d in rescaled}),
        "weights_used": weights,
    })


# ---------------------------------------------------------------- 图谱
@router.get("/graph", summary="知识图谱（Cytoscape 格式）")
def graph(db: Session = Depends(get_db), tid: str = Depends(tenant_id),  # noqa: B008
          rebuild: bool = Query(False)):
    if rebuild:
        build_from_signals(db)
    store = get_graph_store()
    data = store.to_cytoscape()
    return ok({
        "nodes": data["nodes"], "edges": data["edges"], "legend": data["legend"],
        "node_count": len(data["nodes"]), "edge_count": len(data["edges"]),
    })


@router.get("/graph/insights", summary="空白点与中心性（图算法）")
def graph_insights(db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    store = get_graph_store()
    insights = {
        "blank_spots": store.blank_spots(),
        "centrality": store.centrality() if hasattr(store, "centrality") else [],
        "communities": store.communities() if hasattr(store, "communities") else [],
    }
    # 空白点自动转机会卡片建议
    insights["suggestions"] = [
        {"track": b["name"], "reason": f"连接度仅 {b['degree']}，属图谱稀疏区域",
         "action": "建议作为机会卡片进入初筛"}
        for b in insights["blank_spots"][:5]
    ]
    return ok(insights)


# ---------------------------------------------------------------- 供 trends 复用
def persist_scan_result(db: Session, tid: str, analysis: dict,
                        industry: str | None = None) -> None:
    """把趋势扫描结果落库。

    落库逻辑委托给 `upsert_trends` —— 与「机会分析」入口**共用同一份实现**，
    否则两个入口的匹配键不一致（这里曾是 `tenant_id + name`，
    那边曾是 `name + industry`），同一主题会各自建一条，
    实测导致雷达页堆积 38 条近重复趋势。
    """
    trends = (analysis["results"].get("trend") or {}).get("trends", [])
    upsert_trends(db, tid, trends, industry=industry)
    db.commit()


@router.get("/rag/search", summary="RAG 混合检索（调试/下钻用）")
def rag_search(q: str = Query(..., min_length=1), top_k: int = Query(8, ge=1, le=50),
               signal_type: str | None = None,
               db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    rag = get_rag()
    if rag.count_total() == 0:
        from app.services.analysis import reindex

        reindex(db, rag)
    docs = rag.retrieve(q, top_k=top_k, signal_type=signal_type)
    return ok({
        "query": q,
        "hits": [
            {**d.to_evidence(i), "vector_score": d.vector_score,
             "keyword_score": d.keyword_score}
            for i, d in enumerate(docs, start=1)
        ],
        "citation_ratio": rag.citation_ratio(docs),
    })
