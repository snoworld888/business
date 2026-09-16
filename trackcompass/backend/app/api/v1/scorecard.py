"""赛道评分卡接口（技术文档 §4.5 / 《原型设计》§5.7）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_default_scorecard, tenant_id
from app.core.db import get_db
from app.core.errors import ok
from app.models import AuditLog, Opportunity, Scorecard, ScorecardVersion
from app.schemas import CalibrationApplyIn, ScorecardIn, WeightsIn
from app.services.analysis import calibration_preview
from app.services.scoring import (
    DEFAULT_DIMENSIONS,
    rank_opportunities,
    risk_alerts_from,
    validate_weights,
)

router = APIRouter(tags=["赛道评分"])


@router.get("/scorecards", summary="评分卡列表")
def list_scorecards(db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    rows = db.scalars(select(Scorecard).where(Scorecard.tenant_id == tid)).all()
    return ok({
        "total": len(rows),
        "default_dimensions": DEFAULT_DIMENSIONS,
        "scorecards": [
            {"id": c.id, "name": c.name, "weights": c.weights,
             "dimensions": c.dimensions, "is_default": c.is_default,
             "version": c.version, "status": c.status,
             "validation": validate_weights(c.weights or {})}
            for c in rows
        ],
    })


@router.post("/scorecards", summary="创建评分卡")
def create_scorecard(payload: ScorecardIn, db: Session = Depends(get_db),  # noqa: B008
                     tid: str = Depends(tenant_id)):  # noqa: B008
    weights = payload.weights or {d["key"]: d["weight"] for d in DEFAULT_DIMENSIONS}
    dims = payload.dimensions or [dict(d) for d in DEFAULT_DIMENSIONS]
    if payload.is_default:
        for c in db.scalars(select(Scorecard).where(Scorecard.tenant_id == tid)).all():
            c.is_default = False
    card = Scorecard(tenant_id=tid, name=payload.name, weights=weights,
                     dimensions=dims, is_default=payload.is_default)
    db.add(card)
    db.flush()
    db.add(ScorecardVersion(scorecard_id=card.id, version=card.version,
                            weights=weights, change_reason="创建评分卡",
                            diff={k: {"from": None, "to": v} for k, v in weights.items()}))
    db.commit()
    db.refresh(card)
    return ok({"id": card.id, "version": card.version,
               "validation": validate_weights(weights)}, message="评分卡已创建")


@router.get("/scorecards/default", summary="默认评分卡 + 当前项目排序")
def default_scorecard(with_ranking: bool = True,
                      db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    card = get_default_scorecard(db, tid)
    result = {
        "id": card.id, "name": card.name, "weights": card.weights,
        "dimensions": card.dimensions, "version": card.version,
        "validation": validate_weights(card.weights or {}),
    }
    if with_ranking:
        opps = db.scalars(select(Opportunity).where(Opportunity.tenant_id == tid)).all()
        items = [
            {"id": o.id, "title": o.title, "status": o.status,
             "total_score": (o.score or {}).get("total_score", 0)}
            for o in opps
        ]
        result["ranking"] = rank_opportunities(items)
    return ok(result)


@router.put("/scorecards/{scorecard_id}/weights", summary="调整权重（生成新版本）")
def update_weights(scorecard_id: str, payload: WeightsIn,
                   db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    card = db.get(Scorecard, scorecard_id)
    if card is None or card.tenant_id != tid:
        raise HTTPException(404, detail="评分卡不存在")

    validation = validate_weights(payload.weights)
    before = dict(card.weights or {})
    diff = {
        k: {"from": before.get(k), "to": v,
            "delta": round(v - float(before.get(k, 0)), 4)}
        for k, v in payload.weights.items()
    }
    card.weights = payload.weights
    card.dimensions = [
        {**d, "weight": payload.weights.get(d["key"], d.get("weight"))}
        for d in (card.dimensions or DEFAULT_DIMENSIONS)
    ]
    version = payload.version or _bump(card.version)
    card.version = version
    db.add(ScorecardVersion(scorecard_id=card.id, version=version,
                            weights=payload.weights,
                            change_reason=payload.change_reason or "人工调整权重",
                            diff=diff))
    db.add(AuditLog(tenant_id=tid, action="scorecard.update_weights",
                    target_type="scorecard", target_id=card.id,
                    detail={"version": version, "diff": diff}))
    db.commit()

    # 权重变化后重算所有机会的综合分（排序实时更新）
    recalculated = _recalculate(db, tid, payload.weights)
    return ok({"id": card.id, "version": version, "diff": diff,
               "validation": validation, "recalculated_opportunities": recalculated},
              message="权重已更新")


@router.get("/scorecards/{scorecard_id}/versions", summary="版本对比")
def versions(scorecard_id: str, db: Session = Depends(get_db),  # noqa: B008
             tid: str = Depends(tenant_id)):  # noqa: B008
    card = db.get(Scorecard, scorecard_id)
    if card is None or card.tenant_id != tid:
        raise HTTPException(404, detail="评分卡不存在")
    rows = db.scalars(
        select(ScorecardVersion).where(ScorecardVersion.scorecard_id == scorecard_id)
        .order_by(ScorecardVersion.created_at)
    ).all()
    items = [
        {"version": v.version, "weights": v.weights, "diff": v.diff,
         "reason": v.change_reason,
         "created_at": v.created_at.isoformat() if v.created_at else None}
        for v in rows
    ]
    comparison = _compare(items)
    return ok({"current": card.version, "count": len(items),
               "versions": items, "comparison": comparison})


@router.get("/scorecards/calibration/preview", summary="校准建议预览（监控 Agent）")
def preview_calibration(db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    return ok(calibration_preview(db, tid))


@router.post("/scorecards/calibration/apply", summary="应用校准（落库 + 版本记录）")
def apply_calibration_api(payload: dict, db: Session = Depends(get_db),  # noqa: B008
                          tid: str = Depends(tenant_id)):  # noqa: B008
    card = get_default_scorecard(db, tid)
    preview = calibration_preview(db, tid)
    suggestions = payload.get("suggestions") or preview.get("suggestions", [])
    from app.agents.monitor import apply_calibration

    applied = apply_calibration(card.weights or {}, suggestions)
    version = _bump(card.version)
    card.weights = applied["weights"]
    card.dimensions = [
        {**d, "weight": applied["weights"].get(d["key"], d.get("weight"))}
        for d in (card.dimensions or DEFAULT_DIMENSIONS)
    ]
    card.version = version
    db.add(ScorecardVersion(
        scorecard_id=card.id, version=version, weights=applied["weights"],
        change_reason=payload.get("change_reason", "根据复盘结果校准"),
        diff=applied["diff"],
    ))
    db.add(AuditLog(tenant_id=tid, action="scorecard.calibration_apply",
                    target_type="scorecard", target_id=card.id,
                    detail={"version": version, "diff": applied["diff"]}))
    db.commit()
    recalculated = _recalculate(db, tid, applied["weights"])
    return ok({"version": version, **applied, "recalculated": recalculated},
              message="校准已应用")


# ---------------------------------------------------------------- 内部
def _bump(version: str) -> str:
    try:
        prefix, num = version.split("V")
        major, minor = num.split(".")
        return f"{prefix}V{major}.{int(minor) + 1}"
    except Exception:
        return "V1.1"


def _recalculate(db: Session, tid: str, weights: dict[str, float]) -> int:
    from app.services.scoring import total_score

    count = 0
    for opp in db.scalars(select(Opportunity).where(Opportunity.tenant_id == tid)).all():
        dims = (opp.score or {}).get("dimensions") or []
        if not dims:
            continue
        rescaled = [{**d, "weight": weights.get(d["key"], d.get("weight", 0))} for d in dims]
        opp.score = {**(opp.score or {}), "total_score": total_score(rescaled),
                     "dimensions": rescaled,
                     "risk_alerts": risk_alerts_from({d["key"]: d["score"] for d in rescaled})}
        count += 1
    db.commit()
    return count


def _compare(items: list[dict]) -> list[dict]:
    if len(items) < 2:
        return []
    out = []
    for prev, cur in zip(items, items[1:]):
        changes = []
        for key in set(prev["weights"]) | set(cur["weights"]):
            a = float(prev["weights"].get(key, 0))
            b = float(cur["weights"].get(key, 0))
            if abs(a - b) > 1e-6:
                changes.append({"dimension": key, "from": round(a, 4),
                                "to": round(b, 4), "delta": round(b - a, 4)})
        out.append({"from_version": prev["version"], "to_version": cur["version"],
                    "changes": changes, "reason": cur.get("reason")})
    return out
