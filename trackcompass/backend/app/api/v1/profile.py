"""公司能力画像接口（技术文档 §6.2.1）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_profile, get_profile_by_id, list_profiles, tenant_id
from app.core.db import get_db
from app.core.errors import ApiError, ok
from app.core.visibility import visible
from app.models import AuditLog, CompanyProfile, Signal, Track, Trend
from app.schemas import ProfileIn
from app.services.embeddings import cosine, embed
from app.services.scoring import normalize

router = APIRouter(tags=["能力画像"])


def _build_radar(payload: ProfileIn) -> dict[str, float]:
    """能力五维雷达：技术/人才/资金/渠道/客户，各映射到 1-5。"""
    tech = min(1.0, len(payload.tech_stack) / 6 + len(payload.patents) / 12)
    talents = payload.talents or {}
    total_people = sum(v for v in talents.values() if isinstance(v, (int, float))) or 0
    tech_people = sum(v for k, v in talents.items()
                      if any(h in k for h in ("算法", "研发", "工程", "技术")))
    talent = min(1.0, (total_people / 200) * 0.4 + (tech_people / max(1, total_people)) * 0.6)
    funding = normalize(float((payload.funding or {}).get("amount", 0) or 0))
    channel = min(1.0, len(payload.channels) / 5)
    customer = min(1.0, len(payload.customers) / 6)
    to5 = lambda v: round(1 + 4 * max(0.0, min(1.0, v)), 1)  # noqa: E731
    return {"技术": to5(tech), "人才": to5(talent), "资金": to5(funding),
            "渠道": to5(channel), "客户": to5(customer)}


def _recommend_tracks(db: Session, profile: CompanyProfile) -> list[str]:
    """能力向量与赛道描述向量做余弦相似，取 Top-3。"""
    tracks = db.scalars(select(Track)).all()
    if not tracks:
        return []
    pv = profile.capability_vector or embed(
        f"{profile.tech_stack} {profile.tech_description} {profile.patents}"
    )
    scored = []
    for t in tracks:
        tv = embed(f"{t.name} {t.description} {t.industry or ''}")
        scored.append((t.name, cosine(pv, tv)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [name for name, score in scored[:3] if score > 0]


@router.get("/company/profiles", summary="公司画像列表（多公司切换）")
def list_company_profiles(db: Session = Depends(get_db),  # noqa: B008
                          tid: str = Depends(tenant_id)):  # noqa: B008
    """列出本租户全部公司画像，最近保存的排在最前（第 0 条即当前生效主体）。"""
    rows = list_profiles(db, tid)
    return ok({
        "profiles": [
            {
                "id": p.id,
                "name": p.name,
                "industry": p.industry,
                "scale": p.scale,
                "vector_dim": len(p.capability_vector or []),
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                "is_current": idx == 0,
            }
            for idx, p in enumerate(rows)
        ],
        "current_id": rows[0].id if rows else None,
        "count": len(rows),
    })


@router.post("/company/profile", summary="创建/更新能力画像")
def upsert_profile(payload: ProfileIn, db: Session = Depends(get_db),  # noqa: B008
                   tid: str = Depends(tenant_id)):  # noqa: B008
    # ---- 目标解析：显式 id > 同名（视为同一家）> 新建 ----
    created = False
    profile: CompanyProfile | None = None
    if payload.id:
        profile = get_profile_by_id(db, tid, payload.id)
        if profile is None:
            raise ApiError(404, "指定的公司画像不存在或不属于当前租户")
    if profile is None:
        profile = db.scalar(
            select(CompanyProfile).where(
                CompanyProfile.tenant_id == tid,
                CompanyProfile.name == payload.name,
            )
        )
    if profile is None:
        profile = CompanyProfile(tenant_id=tid, name=payload.name)
        db.add(profile)
        created = True

    for field in ("name", "industry", "founded_at", "scale", "tech_stack",
                  "tech_description", "patents", "talents", "funding",
                  "channels", "customers", "supply_chain"):
        setattr(profile, field, getattr(payload, field))

    text = (f"技术栈: {profile.tech_stack}\n专利: {profile.patents}\n"
            f"人才: {profile.talents}\n资金: {profile.funding}\n"
            f"渠道: {profile.channels}\n客户: {profile.customers}\n"
            f"描述: {profile.tech_description}")
    profile.capability_vector = embed(text)
    profile.radar = _build_radar(payload)
    db.flush()
    profile.recommended_tracks = _recommend_tracks(db, profile)
    profile.boundary = {
        "industries": [profile.industry] if profile.industry else [],
        "regions": ["中国"],
        "radar_weakest": min(profile.radar, key=profile.radar.get) if profile.radar else None,
        "note": "赛道推荐基于能力向量与赛道描述的余弦相似度，需专家复核",
    }

    db.add(AuditLog(tenant_id=tid, action="profile.create" if created else "profile.upsert",
                    target_type="company_profile", target_id=profile.id,
                    detail={"name": profile.name, "created": created}))
    db.commit()
    db.refresh(profile)

    return ok({
        "id": profile.id,
        "name": profile.name,
        "created": created,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        "capability_vector": profile.capability_vector,
        "vector_dim": len(profile.capability_vector or []),
        "radar": profile.radar,
        "recommended_tracks": profile.recommended_tracks,
        "boundary": profile.boundary,
    }, message="能力画像已新建" if created else "能力画像已更新")


@router.get("/company/profile", summary="获取能力画像（默认取当前公司，可指定 profile_id）")
def read_profile(profile_id: str | None = Query(None, description="指定公司画像 id"),
                 db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    if profile_id:
        profile = get_profile_by_id(db, tid, profile_id)
        if profile is None:
            raise ApiError(404, "指定的公司画像不存在或不属于当前租户")
    else:
        profile = get_profile(db, tid)
    if profile is None:
        return ok(None, message="尚未建立能力画像")
    return ok({
        "id": profile.id, "name": profile.name, "industry": profile.industry,
        "founded_at": profile.founded_at, "scale": profile.scale,
        "tech_stack": profile.tech_stack, "tech_description": profile.tech_description,
        "patents": profile.patents, "talents": profile.talents,
        "funding": profile.funding, "channels": profile.channels,
        "customers": profile.customers, "supply_chain": profile.supply_chain,
        "radar": profile.radar, "recommended_tracks": profile.recommended_tracks,
        "boundary": profile.boundary,
        "vector_dim": len(profile.capability_vector or []),
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    })


@router.post("/company/profile/parse", summary="上传文档自动解析画像（PDF/Word/文本）")
async def parse_profile_document(file: UploadFile = File(...)):  # noqa: B008
    raw = await file.read()
    text = _decode_text(raw)

    if not text.strip():
        return ok({
            "parsed": False,
            "reason": f"暂不支持解析 {file.content_type or file.filename}，"
                      "请提交纯文本或手工填写表单",
            "suggestion": {"tech_stack": [], "patents": []},
        })

    from app.agents.base import extract_tech_keywords

    keywords = extract_tech_keywords(text, limit=12)
    return ok({
        "parsed": True,
        "filename": file.filename,
        "chars": len(text),
        "suggestion": {
            "tech_stack": keywords,
            "tech_description": text[:500],
            "patents": [line.strip() for line in text.splitlines()
                        if "专利" in line][:10],
            "talents": {},
        },
        "note": "自动解析结果需人工确认后再提交",
    })


@router.get("/company/tracks", summary="赛道列表（含空白点）")
def list_tracks(db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    from app.services.graph import get_graph_store

    tracks = db.scalars(select(Track)).all()
    signal_by_industry: dict[str, int] = {}
    for sig in db.scalars(visible(select(Signal), Signal, tid)).all():
        if sig.industry:
            signal_by_industry[sig.industry] = signal_by_industry.get(sig.industry, 0) + 1
    trend_by_industry: dict[str, list] = {}
    for tr in db.scalars(select(Trend)).all():
        trend_by_industry.setdefault(tr.industry or "综合", []).append(
            {"name": tr.name, "score": tr.score, "confidence": tr.confidence})

    store = get_graph_store()
    blanks = store.blank_spots() if hasattr(store, "blank_spots") else []

    return ok({
        "tracks": [
            {
                "id": t.id, "name": t.name, "description": t.description,
                "industry": t.industry, "status": t.status,
                "signal_count": signal_by_industry.get(t.name, 0),
                "trends": trend_by_industry.get(t.name, [])[:5],
            }
            for t in tracks
        ],
        "blank_spots": blanks[:10],
    })


def _decode_text(raw: bytes) -> str:
    """尽力从上传内容中提取可读文本；二进制/加密文件返回空串。

    判定逻辑：先按 UTF-8 解码，若出现空字节或不可打印字符占比过高，
    则视为二进制（PDF/Word/加密 doc）不予解析，由前端提示手工填写。
    生产环境可替换为 pdfplumber / python-docx 的真实解析器。
    """
    if not raw:
        return ""
    if b"\x00" in raw[:4096]:
        return ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
        except UnicodeDecodeError:
            return ""
    if not text:
        return ""
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    if printable / len(text) < 0.85:
        return ""
    return text
