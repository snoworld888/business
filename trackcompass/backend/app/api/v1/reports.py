"""决策报告接口（技术文档 §6.2.7）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import tenant_id
from app.core.config import BASE_DIR
from app.core.db import get_db
from app.core.errors import ok
from app.models import AuditLog, Opportunity, Report
from app.schemas import ReportIn
from app.services.analysis import generate_report_content
from app.services.report import available_templates, export

router = APIRouter(tags=["决策报告"])

REPORT_DIR = Path(BASE_DIR) / "generated_reports"


@router.get("/reports/templates", summary="报告模板列表")
def templates():
    return ok({"templates": available_templates(), "count": len(available_templates()),
               "formats": ["markdown", "html", "json", "pptx", "docx"]})


@router.get("/reports", summary="报告列表")
def list_reports(opportunity_id: str | None = None,
                 db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    stmt = select(Report).where(Report.tenant_id == tid)
    if opportunity_id:
        stmt = stmt.where(Report.opportunity_id == opportunity_id)
    rows = db.scalars(stmt.order_by(Report.created_at.desc())).all()
    return ok({
        "total": len(rows),
        "reports": [
            {"id": r.id, "title": r.title, "template": r.template, "version": r.version,
             "status": r.status, "format": r.format, "evidence_count": r.evidence_count,
             "page_count": r.page_count, "download_url": r.download_url,
             "opportunity_id": r.opportunity_id,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ],
    })


@router.post("/reports", summary="生成决策报告")
def create_report(payload: ReportIn, db: Session = Depends(get_db),  # noqa: B008
                  tid: str = Depends(tenant_id)):  # noqa: B008
    opp = db.get(Opportunity, payload.opportunity_id)
    if opp is None or opp.tenant_id != tid:
        raise HTTPException(404, detail="机会不存在")
    if not (opp.score or {}).get("dimensions"):
        raise HTTPException(400, detail="该机会尚未完成分析，请先执行七 Agent 分析")

    content = generate_report_content(db, tenant_id=tid, opportunity=opp,
                                      template=payload.template)
    if payload.title:
        content["title"] = payload.title
    generated_at = content["generated_at"]

    version = _next_version(db, tid, opp.id)
    exported = export(content, payload.format, generated_at, REPORT_DIR)

    meta = content.get("meta", {})
    report = Report(
        tenant_id=tid, opportunity_id=opp.id, title=content["title"],
        template=payload.template, version=version, status="generated",
        content=content, format=exported.get("format", payload.format),
        evidence_count=meta.get("evidence_count", 0),
        page_count=meta.get("page_count", 0),
        download_url=exported.get("url"),
    )
    db.add(report)
    db.add(AuditLog(tenant_id=tid, action="report.generate", target_type="report",
                    detail={"opportunity_id": opp.id, "template": payload.template,
                            "format": payload.format, "version": version}))
    db.commit()
    db.refresh(report)

    return ok({
        "id": report.id, "version": version, "title": report.title,
        "template": report.template, "format": report.format,
        "evidence_count": report.evidence_count, "page_count": report.page_count,
        "download_url": report.download_url,
        "file_path": exported.get("path"),
        "sections": [s["title"] for s in content["sections"]],
        "sections_detail": content["sections"],
    }, message="报告已生成")


@router.get("/reports/{report_id}", summary="报告详情")
def report_detail(report_id: str, db: Session = Depends(get_db),  # noqa: B008
                  tid: str = Depends(tenant_id)):  # noqa: B008
    report = db.get(Report, report_id)
    if report is None or report.tenant_id != tid:
        raise HTTPException(404, detail="报告不存在")
    return ok({
        "id": report.id, "title": report.title, "template": report.template,
        "version": report.version, "format": report.format,
        "evidence_count": report.evidence_count, "page_count": report.page_count,
        "download_url": report.download_url, "content": report.content,
    })


@router.post("/reports/{report_id}/export", summary="导出为指定格式")
def export_report(report_id: str, fmt: str = "pptx",
                  db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    report = db.get(Report, report_id)
    if report is None or report.tenant_id != tid:
        raise HTTPException(404, detail="报告不存在")
    content = report.content or {}
    exported = export(content, fmt, content.get("generated_at", ""), REPORT_DIR)
    report.format = exported.get("format", fmt)
    report.download_url = exported.get("url")
    db.commit()
    return ok({"id": report.id, **exported},
              message=f"已导出 {exported.get('format')}")


def _next_version(db: Session, tid: str, opportunity_id: str) -> str:
    count = len(db.scalars(
        select(Report).where(Report.tenant_id == tid, Report.opportunity_id == opportunity_id)
    ).all())
    return f"V1.{count}"
