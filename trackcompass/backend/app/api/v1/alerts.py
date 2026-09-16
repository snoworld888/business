"""监控与复盘接口（技术文档 §6.2.8 / §4.8，《原型设计》§5.10）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import tenant_id
from app.core.db import get_db, utcnow
from app.core.errors import ok
from app.core.visibility import visible
from app.models import Alert, AuditLog, Opportunity, Track
from app.schemas import AlertActionIn, ReplayIn
from app.agents.monitor import (
    ALERT_LEVELS,
    apply_calibration,
    level_variants,
    normalize_alert_level,
)

router = APIRouter(tags=["监控复盘"])

# 复盘库（生产环境落到 replays 表）
_REPLAYS: list[dict] = []


@router.get("/alerts", summary="预警列表")
def list_alerts(status: str | None = None, level: str | None = None,
                limit: int = Query(50, ge=1, le=200),
                db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    stmt = select(Alert).where(Alert.tenant_id == tid)
    if status:
        stmt = stmt.where(Alert.status == status)
    if level:
        # 展开为该级别的**全部历史写法**再过滤：库里存在 `high/medium/low`、
        # `High/Medium/Low` 等非规范值，直接等值比较会漏掉它们。
        stmt = stmt.where(Alert.level.in_(level_variants(level)))
    rows = db.scalars(stmt.order_by(Alert.created_at.desc()).limit(limit)).all()

    track_names = {t.id: t.name for t in db.scalars(select(Track)).all()}
    grouped: dict[str, list] = {"pending": [], "handled": [], "ignored": []}
    items = []
    for a in rows:
        item = {
            "id": a.id, "level": normalize_alert_level(a.level), "rule_name": a.rule_name,
            "title": a.title, "summary": a.summary, "status": a.status,
            "signal_type": a.signal_type,
            "track": track_names.get(a.track_id) if a.track_id else None,
            "opportunity_id": a.opportunity_id,
            "payload": a.payload,
            "advice": (a.payload or {}).get("advice"),
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "handled_at": a.handled_at.isoformat() if a.handled_at else None,
        }
        items.append(item)
        grouped.setdefault(a.status, []).append(item)

    # 级别计数必须**归一后统计**，否则非规范值（历史数据）会被静默丢弃，
    # 表现为「总数 > 0 但严重/警告/提示全是 0」—— 实测就是这样。
    counts = {lv: 0 for lv in ALERT_LEVELS}
    for a in rows:
        counts[normalize_alert_level(a.level)] += 1

    return ok({
        "total": len(items),
        "severity": counts,
        "grouped": grouped,
        "alerts": items,
    })


@router.post("/alerts/{alert_id}/action", summary="处理 / 忽略 / 重开预警")
def alert_action(alert_id: str, payload: AlertActionIn,
                 db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    alert = db.get(Alert, alert_id)
    if alert is None or alert.tenant_id != tid:
        raise HTTPException(404, detail="预警不存在")

    mapping = {"handle": "handled", "ignore": "ignored", "reopen": "pending"}
    alert.status = mapping[payload.action]
    alert.handled_at = utcnow() if alert.status != "pending" else None
    alert.payload = {**(alert.payload or {}), "note": payload.note}
    db.add(AuditLog(tenant_id=tid, action=f"alert.{payload.action}",
                    target_type="alert", target_id=alert.id,
                    detail={"note": payload.note}))
    db.commit()
    return ok({"id": alert.id, "status": alert.status,
               "handled_at": alert.handled_at.isoformat() if alert.handled_at else None})


@router.post("/alerts/scan", summary="重跑监控预警 Agent（对全部或指定机会）")
def scan_alerts(opportunity_id: str | None = None,
                db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    from app.agents.base import AgentState, SignalView
    from app.agents.monitor import MonitorAlertAgent
    from app.models import Signal

    signals = db.scalars(visible(select(Signal), Signal, tid)).all()
    state = AgentState(query="监控扫描",
                       signals=[SignalView.from_orm(s) for s in signals], results={})
    output = MonitorAlertAgent().rule(state)

    created = 0
    opp = db.get(Opportunity, opportunity_id) if opportunity_id else None
    for a in output.get("alerts", []):
        exists = db.scalar(
            select(Alert).where(Alert.tenant_id == tid, Alert.rule_name == a["type"],
                                Alert.title == a["title"])
        )
        if exists:
            continue
        db.add(Alert(
            tenant_id=tid, opportunity_id=opportunity_id,
            track_id=opp.track_id if opp else None,
            signal_type=a.get("signal_type", ""), rule_name=a.get("type", ""),
            # 写入即归一 —— 保证库里只存规范级别，不再产生新的脏数据
            level=normalize_alert_level(a.get("level", "warning")),
            title=a["title"], summary=a["summary"],
            payload={**(a.get("payload") or {}), "advice": a.get("advice"),
                     "source": a.get("source"), "url": a.get("url")},
        ))
        created += 1
    db.commit()
    return ok({"scanned_signals": len(signals), "generated": len(output.get("alerts", [])),
               "new_alerts": created, "severity": output.get("severity", {}),
               "calibration": output.get("calibration", [])}, message="监控扫描完成")


@router.post("/alerts/replay", summary="填写复盘记录并生成校准建议")
def create_replay(payload: ReplayIn, db: Session = Depends(get_db),  # noqa: B008
                  tid: str = Depends(tenant_id)):  # noqa: B008
    opp = db.get(Opportunity, payload.decision_id)
    if opp is None or opp.tenant_id != tid:
        raise HTTPException(404, detail="决策/机会不存在")

    from app.api.deps import get_default_scorecard
    from app.services.analysis import calibration_preview

    card = get_default_scorecard(db, tid)
    preview = calibration_preview(db, tid)
    suggestions = preview.get("suggestions", [])

    record = {
        "id": f"RP{len(_REPLAYS) + 1:04d}",
        "decision_id": payload.decision_id,
        "title": opp.title,
        "decision_at": payload.decision_at,
        "basis": payload.basis or {
            "total_score": (opp.score or {}).get("total_score"),
            "dimensions": [
                {"key": d["key"], "score": d["score"]}
                for d in (opp.score or {}).get("dimensions", [])
            ],
        },
        "outcome": payload.outcome,
        "review": payload.review,
        "calibration_suggestions": suggestions,
        "created_at": utcnow().isoformat(),
    }
    _REPLAYS.append(record)

    db.add(AuditLog(tenant_id=tid, action="alert.replay", target_type="opportunity",
                    target_id=opp.id, detail={"replay_id": record["id"]}))
    db.commit()
    return ok({"replay": record, "scorecard_version": card.version,
               "can_apply": bool(suggestions)}, message="复盘已记录")


@router.get("/replays", summary="复盘看板")
def list_replays(db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    return ok({"total": len(_REPLAYS), "replays": list(reversed(_REPLAYS))})


@router.post("/replays/{replay_id}/apply", summary="应用复盘校准建议")
def apply_replay(replay_id: str, db: Session = Depends(get_db),  # noqa: B008
                 tid: str = Depends(tenant_id)):  # noqa: B008
    record = next((r for r in _REPLAYS if r["id"] == replay_id), None)
    if record is None:
        raise HTTPException(404, detail="复盘记录不存在")

    from app.api.deps import get_default_scorecard
    from app.models import ScorecardVersion
    from app.services.scoring import DEFAULT_DIMENSIONS, risk_alerts_from, total_score

    card = get_default_scorecard(db, tid)
    applied = apply_calibration(card.weights or {}, record["calibration_suggestions"])
    version = _bump(card.version)
    card.weights = applied["weights"]
    card.version = version
    card.dimensions = [
        {**d, "weight": applied["weights"].get(d["key"], d.get("weight"))}
        for d in (card.dimensions or DEFAULT_DIMENSIONS)
    ]
    db.add(ScorecardVersion(scorecard_id=card.id, version=version,
                            weights=applied["weights"],
                            change_reason=f"复盘 {replay_id} 校准",
                            diff=applied["diff"]))

    recalculated = 0
    for opp in db.scalars(select(Opportunity).where(Opportunity.tenant_id == tid)).all():
        dims = (opp.score or {}).get("dimensions") or []
        if not dims:
            continue
        rescaled = [{**d, "weight": applied["weights"].get(d["key"], d.get("weight", 0))}
                    for d in dims]
        opp.score = {**(opp.score or {}), "total_score": total_score(rescaled),
                     "dimensions": rescaled,
                     "risk_alerts": risk_alerts_from({d["key"]: d["score"] for d in rescaled})}
        recalculated += 1

    record["applied"] = True
    record["applied_version"] = version
    db.add(AuditLog(tenant_id=tid, action="replay.apply_calibration",
                    target_type="scorecard", target_id=card.id,
                    detail={"replay_id": replay_id, "version": version}))
    db.commit()
    return ok({"replay_id": replay_id, "version": version, **applied,
               "recalculated": recalculated}, message="校准已应用")


def _bump(version: str) -> str:
    try:
        prefix, num = version.split("V")
        major, minor = num.split(".")
        return f"{prefix}V{major}.{int(minor) + 1}"
    except Exception:
        return "V1.1"
