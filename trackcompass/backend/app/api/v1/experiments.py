"""验证实验接口（技术文档 §6.2.6 / §4.6 状态机）。

状态机：draft → running → completed → archived，running 可分支 cancelled。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import tenant_id
from app.core.db import get_db, utcnow
from app.core.errors import ok
from app.models import AuditLog, Experiment, Opportunity
from app.schemas import ExperimentIn, ExperimentUpdateIn

router = APIRouter(tags=["验证实验"])

TRANSITIONS: dict[str, set[str]] = {
    "draft": {"running", "cancelled"},
    "running": {"completed", "cancelled"},
    "completed": {"archived", "running"},
    "archived": set(),
    "cancelled": {"draft"},
}

METHOD_TEMPLATES = {
    "landing_page": {
        "name": "落地页测试",
        "metrics": {"signup_rate": {"target": 0.05, "unit": "%"},
                    "dwell_time": {"target": 120, "unit": "s"},
                    "conversion_rate": {"target": 0.02, "unit": "%"}},
        "duration_days": 21,
        "hypothesis_hint": "目标客户愿意留下联系方式以获取解决方案",
    },
    "interview": {
        "name": "客户访谈",
        "metrics": {"premium_acceptance": {"target": 0.5, "unit": "比例"},
                    "pain_intensity": {"target": 4.0, "unit": "1-5"}},
        "duration_days": 30,
        "hypothesis_hint": "客户愿意为效果提升支付溢价",
    },
    "survey": {
        "name": "问卷调研",
        "metrics": {"response_rate": {"target": 0.15, "unit": "%"},
                    "nps": {"target": 30, "unit": "分"}},
        "duration_days": 14,
        "hypothesis_hint": "目标客户群对现有方案的不满具有普遍性",
    },
    "poc": {
        "name": "POC 概念验证",
        "metrics": {"accuracy_gain": {"target": 0.2, "unit": "%"},
                    "deploy_days": {"target": 30, "unit": "天"}},
        "duration_days": 60,
        "hypothesis_hint": "方案可在客户真实产线上达到承诺指标",
    },
    "presale": {
        "name": "预售测试",
        "metrics": {"preorder_count": {"target": 10, "unit": "家"},
                    "deposit_rate": {"target": 0.3, "unit": "%"}},
        "duration_days": 30,
        "hypothesis_hint": "客户愿意为未来交付预付定金",
    },
    "channel_test": {
        "name": "渠道测试",
        "metrics": {"lead_count": {"target": 50, "unit": "条"},
                    "channel_cac": {"target": 5000, "unit": "元"}},
        "duration_days": 45,
        "hypothesis_hint": "通过渠道伙伴可低成本触达目标客户",
    },
}


@router.get("/experiments/templates", summary="实验模板（《原型设计》§5.8）")
def templates():
    return ok({"templates": [{"key": k, **v} for k, v in METHOD_TEMPLATES.items()],
               "count": len(METHOD_TEMPLATES), "required": 5,
               "requirement_met": len(METHOD_TEMPLATES) >= 5})


@router.get("/experiments", summary="实验列表")
def list_experiments(status: str | None = None,
                     opportunity_id: str | None = None,
                     limit: int = Query(50, ge=1, le=200),
                     db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    stmt = select(Experiment).where(Experiment.tenant_id == tid)
    if status:
        stmt = stmt.where(Experiment.status == status)
    if opportunity_id:
        stmt = stmt.where(Experiment.opportunity_id == opportunity_id)
    rows = db.scalars(stmt.order_by(Experiment.created_at.desc()).limit(limit)).all()

    grouped: dict[str, list] = {"running": [], "completed": [], "draft": [],
                                "archived": [], "cancelled": []}
    for e in rows:
        grouped.setdefault(e.status, []).append({
            "id": e.id, "title": e.title or METHOD_TEMPLATES.get(e.method, {}).get("name", e.method),
            "hypothesis": e.hypothesis, "method": e.method,
            "method_name": METHOD_TEMPLATES.get(e.method, {}).get("name", e.method),
            "metrics": e.metrics, "result": e.result, "conclusion": e.conclusion,
            "status": e.status, "progress": e.progress,
            "applied_to_score": e.applied_to_score,
            "opportunity_id": e.opportunity_id,
        })
    return ok({"total": len(rows), "grouped": grouped, "experiments": grouped_body(rows)})


def grouped_body(rows) -> list[dict]:
    return [
        {"id": e.id, "title": e.title or METHOD_TEMPLATES.get(e.method, {}).get("name", e.method),
         "hypothesis": e.hypothesis, "method": e.method,
         "method_name": METHOD_TEMPLATES.get(e.method, {}).get("name", e.method),
         "metrics": e.metrics, "result": e.result, "conclusion": e.conclusion,
         "status": e.status, "progress": e.progress,
         "applied_to_score": e.applied_to_score, "opportunity_id": e.opportunity_id,
         "started_at": e.started_at.isoformat() if e.started_at else None,
         "ended_at": e.ended_at.isoformat() if e.ended_at else None}
        for e in rows
    ]


@router.post("/experiments", summary="创建实验")
def create_experiment(payload: ExperimentIn, db: Session = Depends(get_db),  # noqa: B008
                      tid: str = Depends(tenant_id)):  # noqa: B008
    template = METHOD_TEMPLATES.get(payload.method, {})
    metrics = payload.metrics or {
        k: {**v, "current": 0} for k, v in (template.get("metrics") or {}).items()
    }
    exp = Experiment(
        tenant_id=tid, opportunity_id=payload.opportunity_id,
        title=payload.title or template.get("name", payload.method),
        hypothesis=payload.hypothesis, method=payload.method, metrics=metrics,
        status="running", started_at=utcnow(), progress=0.0,
    )
    db.add(exp)
    db.add(AuditLog(tenant_id=tid, action="experiment.create", target_type="experiment",
                    detail={"method": payload.method, "hypothesis": payload.hypothesis}))
    db.commit()
    db.refresh(exp)
    return ok({"id": exp.id, "status": exp.status,
               "method_name": template.get("name"),
               "metrics": metrics,
               "duration_days": template.get("duration_days")}, message="实验已创建")


@router.get("/experiments/{experiment_id}", summary="实验详情")
def experiment_detail(experiment_id: str, db: Session = Depends(get_db),  # noqa: B008
                      tid: str = Depends(tenant_id)):  # noqa: B008
    exp = db.get(Experiment, experiment_id)
    if exp is None or exp.tenant_id != tid:
        raise HTTPException(404, detail="实验不存在")
    return ok({
        "id": exp.id, "title": exp.title, "hypothesis": exp.hypothesis,
        "method": exp.method,
        "method_name": METHOD_TEMPLATES.get(exp.method, {}).get("name", exp.method),
        "metrics": exp.metrics, "result": exp.result, "conclusion": exp.conclusion,
        "status": exp.status, "progress": exp.progress,
        "applied_to_score": exp.applied_to_score,
        "allowed_transitions": sorted(TRANSITIONS.get(exp.status, set())),
        "progress_bars": _progress_bars(exp.metrics or {}),
        "started_at": exp.started_at.isoformat() if exp.started_at else None,
        "ended_at": exp.ended_at.isoformat() if exp.ended_at else None,
    })


@router.patch("/experiments/{experiment_id}", summary="更新实验（数据/状态/结论）")
def update_experiment(experiment_id: str, payload: ExperimentUpdateIn,
                      db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    exp = db.get(Experiment, experiment_id)
    if exp is None or exp.tenant_id != tid:
        raise HTTPException(404, detail="实验不存在")

    if payload.status and payload.status != exp.status:
        allowed = TRANSITIONS.get(exp.status, set())
        if payload.status not in allowed:
            raise HTTPException(400, detail=f"非法状态流转 {exp.status} → {payload.status}，"
                                            f"允许：{sorted(allowed) or '无'}")
        exp.status = payload.status
        if payload.status == "completed":
            exp.ended_at = utcnow()
            exp.progress = 100.0
        if payload.status == "running" and exp.started_at is None:
            exp.started_at = utcnow()

    if payload.metrics:
        merged = {**(exp.metrics or {})}
        for key, value in payload.metrics.items():
            base = merged.get(key)
            if isinstance(base, dict) and isinstance(value, dict):
                merged[key] = {**base, **value}
            elif isinstance(base, dict):
                merged[key] = {**base, "current": value}
            else:
                merged[key] = value
        exp.metrics = merged
    if payload.result is not None:
        exp.result = payload.result
    if payload.conclusion is not None:
        exp.conclusion = payload.conclusion
    if payload.progress is not None:
        exp.progress = payload.progress
    elif exp.metrics:
        exp.progress = _auto_progress(exp.metrics)

    db.add(AuditLog(tenant_id=tid, action="experiment.update", target_type="experiment",
                    target_id=exp.id, detail={"status": exp.status,
                                              "progress": exp.progress}))
    db.commit()
    db.refresh(exp)
    return ok({"id": exp.id, "status": exp.status, "progress": exp.progress,
               "progress_bars": _progress_bars(exp.metrics or {}),
               "allowed_transitions": sorted(TRANSITIONS.get(exp.status, set()))})


@router.post("/experiments/{experiment_id}/writeback", summary="结论回写赛道评分")
def writeback(experiment_id: str, db: Session = Depends(get_db),  # noqa: B008
              tid: str = Depends(tenant_id)):  # noqa: B008
    exp = db.get(Experiment, experiment_id)
    if exp is None or exp.tenant_id != tid:
        raise HTTPException(404, detail="实验不存在")
    if exp.opportunity_id is None:
        raise HTTPException(400, detail="实验未关联机会卡片，无法回写")

    opp = db.get(Opportunity, exp.opportunity_id)
    if opp is None:
        raise HTTPException(404, detail="关联机会不存在")

    achieved = _achievement(exp.metrics or {}, exp.result or {})
    score = dict(opp.score or {})
    dims = list(score.get("dimensions") or [])
    adjusted = []
    for d in dims:
        item = dict(d)
        if item["key"] == "demand":
            delta = round((achieved - 0.5) * 0.8, 2)  # 达成率 50% 为中性
            item["score"] = round(max(1.0, min(5.0, item["score"] + delta)), 2)
            item["rationale"] = (item.get("rationale", "") +
                                 f"；实验「{exp.title}」达成率 {achieved:.0%}，"
                                 f"需求维度调整 {delta:+.2f}")
        adjusted.append(item)

    from app.services.scoring import explain_score, risk_alerts_from, total_score

    score.update({
        "dimensions": adjusted,
        "total_score": total_score(adjusted),
        "explained": explain_score(adjusted),
        "risk_alerts": risk_alerts_from({d["key"]: d["score"] for d in adjusted}),
    })
    opp.score = score
    opp.status = "validated" if achieved >= 0.7 else "verifying"
    exp.applied_to_score = True

    db.add(AuditLog(tenant_id=tid, action="experiment.writeback", target_type="experiment",
                    target_id=exp.id,
                    detail={"opportunity_id": opp.id, "achievement": achieved,
                            "new_total": score["total_score"]}))
    db.commit()
    return ok({
        "experiment_id": exp.id, "opportunity_id": opp.id,
        "achievement_rate": achieved,
        "opportunity_status": opp.status,
        "new_total_score": score["total_score"],
        "dimensions": adjusted,
    }, message="已回写赛道评分")


@router.get("/experiments/{experiment_id}/export", summary="导出实验数据")
def export_experiment(experiment_id: str, db: Session = Depends(get_db),  # noqa: B008
                      tid: str = Depends(tenant_id)):  # noqa: B008
    exp = db.get(Experiment, experiment_id)
    if exp is None or exp.tenant_id != tid:
        raise HTTPException(404, detail="实验不存在")
    rows = ["metric,current,target,unit,achievement"]
    for key, value in (exp.metrics or {}).items():
        if not isinstance(value, dict):
            rows.append(f"{key},{value},,,")
            continue
        cur, tgt = value.get("current"), value.get("target")
        ach = ""
        if isinstance(cur, (int, float)) and isinstance(tgt, (int, float)) and tgt:
            ach = f"{cur / tgt:.2f}"
        rows.append(f"{key},{cur},{tgt},{value.get('unit', '')},{ach}")
    return ok({"experiment_id": exp.id, "format": "csv", "content": "\n".join(rows),
               "filename": f"experiment_{exp.id[:8]}.csv"})


# ---------------------------------------------------------------- 工具
def _progress_bars(metrics: dict) -> list[dict]:
    out = []
    for key, value in metrics.items():
        if not isinstance(value, dict):
            continue
        cur = value.get("current")
        tgt = value.get("target")
        ratio = None
        if isinstance(cur, (int, float)) and isinstance(tgt, (int, float)) and tgt:
            ratio = round(cur / tgt, 4)
        out.append({"metric": key, "current": cur, "target": tgt,
                    "unit": value.get("unit", ""), "achievement": ratio})
    return out


def _auto_progress(metrics: dict) -> float:
    ratios = [b["achievement"] for b in _progress_bars(metrics) if b["achievement"] is not None]
    if not ratios:
        return 0.0
    return round(min(100.0, sum(ratios) / len(ratios) * 100), 1)


def _achievement(metrics: dict, result: dict) -> float:
    ratios = [b["achievement"] for b in _progress_bars(metrics) if b["achievement"] is not None]
    if ratios:
        return round(min(1.5, sum(ratios) / len(ratios)), 4)
    if result:
        numeric = [v for v in result.values() if isinstance(v, (int, float))]
        if numeric:
            return round(min(1.5, sum(numeric) / len(numeric)), 4)
    return 0.5
