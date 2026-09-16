"""趋势雷达接口（技术文档 §6.2.2）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.trend import match_capabilities
from app.api.deps import current_capability_terms, resolve_track, tenant_id
from app.core.db import get_db
from app.core.errors import ok
from app.models import AuditLog, Opportunity, Trend
from app.schemas import TrendSubscribeIn, TrendToPoolIn
from app.services.analysis import run_analysis

router = APIRouter(tags=["趋势雷达"])


@router.get("/trends", summary="趋势列表")
def list_trends(
    industry: str | None = None,
    region: str | None = None,
    direction: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    refresh: bool = Query(False, description="是否先重跑趋势侦察 Agent"),
    db: Session = Depends(get_db),  # noqa: B008
    tid: str = Depends(tenant_id),  # noqa: B008
):
    if refresh:
        _scan(db, tid, industry, region)

    stmt = select(Trend).where(Trend.tenant_id == tid)
    if industry:
        stmt = stmt.where(Trend.industry == industry)
    if region:
        stmt = stmt.where(Trend.region == region)
    if direction:
        stmt = stmt.where(Trend.direction == direction)
    stmt = stmt.order_by(Trend.score.desc()).limit(limit)
    rows = db.scalars(stmt).all()

    # 能力匹配**实时计算**：当前公司能力词 ∩ 趋势的客观标签 `factors._tags`。
    # 不读落库快照 —— 那样切换公司不会重算，也说不清是给哪家公司算的。
    caps = current_capability_terms(db, tid)

    def _row(t: Trend) -> dict:
        tags = list((t.factors or {}).get("_tags") or [])
        matched = match_capabilities(tags, caps)
        return {
            "id": t.id, "name": t.name, "description": t.description,
            "industry": t.industry, "confidence": t.confidence,
            "direction": t.direction, "score": t.score,
            # `_` 前缀是内部键（客观标签），不进公开的因子列表
            "factors": {k: v for k, v in (t.factors or {}).items()
                        if not str(k).startswith("_")},
            "capability_match": bool(matched),
            "matched_capabilities": matched,
            "evidence": t.evidence, "evidence_count": len(t.evidence or []),
            "subscribed": t.subscribed, "in_pool": t.in_pool,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }

    return ok({
        "total": len(rows),
        "algorithm": "0.3*signal + 0.3*growth + 0.2*policy + 0.2*capital",
        "trends": [_row(t) for t in rows],
    })


@router.post("/trends/scan", summary="触发趋势扫描（重跑趋势侦察 Agent）")
def scan(industry: str | None = None, region: str | None = None,
         db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    summary = _scan(db, tid, industry, region)
    return ok(summary, message="趋势扫描完成")


@router.get("/trends/{trend_id}", summary="趋势详情（含完整证据链）")
def trend_detail(trend_id: str, db: Session = Depends(get_db),  # noqa: B008
                 tid: str = Depends(tenant_id)):  # noqa: B008
    trend = db.get(Trend, trend_id)
    if trend is None or trend.tenant_id != tid:
        raise HTTPException(404, detail="趋势不存在")
    _matched = match_capabilities(
        list((trend.factors or {}).get("_tags") or []),
        current_capability_terms(db, tid),
    )
    return ok({
        "id": trend.id, "name": trend.name, "description": trend.description,
        "industry": trend.industry, "confidence": trend.confidence,
        "direction": trend.direction, "score": trend.score,
        "factors": {k: v for k, v in (trend.factors or {}).items()
                    if not str(k).startswith("_")},
        # 同样实时算（见列表接口的说明）
        "capability_match": bool(_matched),
        "matched_capabilities": _matched,
        "evidence": trend.evidence, "subscribed": trend.subscribed,
        "in_pool": trend.in_pool,
    })


@router.post("/trends/{trend_id}/subscribe", summary="订阅/取消订阅趋势预警")
def subscribe(trend_id: str, payload: TrendSubscribeIn,
              db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    trend = db.get(Trend, trend_id)
    if trend is None or trend.tenant_id != tid:
        raise HTTPException(404, detail="趋势不存在")
    trend.subscribed = payload.subscribed
    db.commit()
    return ok({"id": trend.id, "subscribed": trend.subscribed})


@router.post("/trends/{trend_id}/to-pool", summary="趋势加入机会池（生成机会卡片）")
def to_pool(trend_id: str, payload: TrendToPoolIn,
            db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    trend = db.get(Trend, trend_id)
    if trend is None or trend.tenant_id != tid:
        raise HTTPException(404, detail="趋势不存在")

    # ---- 幂等：同一趋势重复入池时返回**既有**机会卡，不重复创建
    # 实测缺陷（2026-09-14）：此前每次调用都 `db.add(opp)` 新建，
    # 只设了 `trend.in_pool = True` 却不用它防重 ⇒ 反复调用后同一趋势
    # 累积出 17 条同名机会，「赛道评分 · 排序对比」里出现大量重复条目。
    existing = db.scalar(
        select(Opportunity).where(Opportunity.tenant_id == tid,
                                  Opportunity.source_trend_id == trend.id)
        .order_by(Opportunity.created_at)
    )
    if existing is not None:
        if not trend.in_pool:
            trend.in_pool = True
            db.commit()
        return ok({
            "opportunity_id": existing.id, "title": existing.title,
            "status": existing.status, "reused": True,
        }, message="该趋势已在机会池中，返回既有卡片")

    # ⚠️ 不能用 `trend.industry or trend.name` 兜底（实测缺陷 2026-09-15）：
    # 趋势名**不是**赛道名，一旦 industry 为空，`resolve_track` 会**静默创建**
    # 一条名为趋势名的赛道记录 —— 实测 tracks 表混进
    # 「AI算力硬件高精度光学检测」，并在「空白点识别」里冒充赛道显示。
    # industry 为空时宁可不挂赛道（track_id 留空），也不造假数据。
    track = resolve_track(db, trend.industry)
    opp = Opportunity(
        tenant_id=tid, track_id=track.id if track else None,
        title=payload.title or trend.name,
        summary=payload.summary or trend.description,
        status="screening", source_trend_id=trend.id,
        evidence=trend.evidence or [],
    )
    db.add(opp)
    trend.in_pool = True
    db.add(AuditLog(tenant_id=tid, action="trend.to_pool", target_type="trend",
                    target_id=trend.id, detail={"opportunity_id": opp.id}))
    db.commit()
    db.refresh(opp)

    return ok({
        "opportunity_id": opp.id, "title": opp.title, "status": opp.status,
        "track": track.name if track else None,
    }, message="已加入机会池")


def _scan(db: Session, tid: str, industry: str | None, region: str | None) -> dict:
    from app.api.v1.opportunities import persist_scan_result

    result = run_analysis(db, tenant_id=tid, industry=industry, region=region,
                          selected_agents=["trend"])
    persist_scan_result(db, tid, result, industry=industry)
    trend_out = result["results"].get("trend") or {}
    trends = trend_out.get("trends", [])
    return {
        "scanned_signals": result["signal_count"],
        "generated": len(trends),
        "top": [{"name": t["name"], "score": t["score"], "direction": t["direction"],
                 "confidence": t["confidence"],
                 "capability_match": t.get("capability_match", False),
                 "matched_capabilities": t.get("matched_capabilities", [])}
                for t in trends[:5]],
        # 画像里写了、但外部信号零证据的方向（情报空白，不是趋势）
        "capability_gaps": trend_out.get("capability_gaps", []),
        "capability_matched": sorted({c for t in trends
                                      for c in (t.get("matched_capabilities") or [])}),
        "capability_terms": trend_out.get("capability_terms", []),
    }
