"""信号层接口：采集写入、查询、数据源状态、索引重建。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import signal_stats, tenant_id
from app.core.db import get_db
from app.core.errors import ok
from app.core.visibility import PUBLIC_TENANT_ID, visible
from app.models import AuditLog, Signal
from app.schemas import CollectIn, SignalIn

router = APIRouter(tags=["信号层"])

DATA_SOURCES = [
    ("policy", "政策网站"), ("patent", "专利数据库"), ("paper", "论文平台"),
    ("funding", "融资数据"), ("recruit", "招聘数据"), ("social", "社媒数据"),
    ("review", "应用商店评论"), ("forum", "行业论坛"), ("bidding", "招标信息"),
    ("news", "产业媒体"), ("supply", "供应链数据"),
]


@router.get("/signals", summary="信号列表")
def list_signals(
    signal_type: str | None = None,
    industry: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),  # noqa: B008
    tid: str = Depends(tenant_id),  # noqa: B008
):
    stmt = visible(select(Signal), Signal, tid)
    if signal_type:
        stmt = stmt.where(Signal.signal_type == signal_type)
    if industry:
        stmt = stmt.where(Signal.industry == industry)
    stmt = stmt.order_by(Signal.published_at.desc()).limit(limit)
    rows = db.scalars(stmt).all()
    return ok({
        "total": len(rows),
        "stats": signal_stats(db, tid),
        "signals": [
            {
                "id": s.id, "signal_type": s.signal_type, "source": s.source,
                "title": s.title, "content": s.content[:200], "url": s.url,
                "industry": s.industry, "keywords": s.keywords,
                "metrics": s.metrics, "sentiment": s.sentiment,
                "published_at": s.published_at.isoformat() if s.published_at else None,
            }
            for s in rows
        ],
    })


@router.post("/signals", summary="写入信号（人工上传 / 外部采集器回传）")
def create_signal(payload: SignalIn, db: Session = Depends(get_db),  # noqa: B008
                  tid: str = Depends(tenant_id)):  # noqa: B008
    """写入单条信号。

    与采集器走**同一个入库函数**（`services.signal_ingest`），因此：
    ① 实体抽取规则一致（关键词 = 入参 ∪ 从标题/正文自动抽取）；
    ② 同样按 content_hash + url 去重 —— 此前本接口只计算 hash 却不判重，
       反复调用会灌入重复信号，污染趋势雷达（本项目已多次踩到该模式）。
    """
    from app.services.signal_ingest import persist_signals

    body = payload.model_dump()
    body["_raw_title"] = payload.title          # 供 content_hash 使用

    # 信号默认是**公共数据**（外部情报，全租户共享）；显式 is_public=False 才归本租户。
    owner = PUBLIC_TENANT_ID if payload.is_public else tid
    report = persist_signals(db, owner, [body], source_hint=payload.source)
    inserted = report.inserted > 0
    return ok({
        "id": report.inserted_ids[0] if report.inserted_ids else None,
        "indexed": inserted,
        "duplicated": report.duplicated > 0,
        "is_public": payload.is_public,
    }, message="信号已写入" if inserted else "该信号已存在（按标题 / 链接去重）")


@router.get("/signals/sources", summary="数据源连接状态（《原型设计》§5.11）")
def data_sources(db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    stats = signal_stats(db, tid)
    last = {}
    for sig in db.scalars(visible(select(Signal), Signal, tid)).all():
        ts = sig.published_at.isoformat() if sig.published_at else None
        if ts and (sig.signal_type not in last or ts > last[sig.signal_type]):
            last[sig.signal_type] = ts
    sources = [
        {"key": key, "name": name, "connected": stats.get(key, 0) > 0,
         "signal_count": stats.get(key, 0), "last_sync": last.get(key)}
        for key, name in DATA_SOURCES
    ]
    return ok({
        "sources": sources,
        "connected": sum(1 for s in sources if s["connected"]),
        "mapped_sources": len(sources),
        "coverage_ok": sum(1 for s in sources if s["connected"]) >= 5,
    })


@router.post("/signals/reindex", summary="重建向量索引与知识图谱")
def reindex(db: Session = Depends(get_db), tid: str = Depends(tenant_id)):  # noqa: B008
    from app.services.analysis import refresh_graph, reset_and_reindex
    from app.services.rag import get_rag

    count = reset_and_reindex(db)
    graph_stats = refresh_graph(db)
    return ok({
        "indexed_documents": count,
        "graph": graph_stats,
        "rag_docs": get_rag().count_total(),
    }, message="索引已重建")


# ---------------------------------------------------------------- 采集器
@router.get("/signals/adapters", summary="数据源适配器清单（《技术文档》§3.1）")
def list_adapters():
    """列出全部数据源适配器及其可用状态。

    与 `/signals/sources` 的区别：本接口描述**采集能力**（能连哪些源、
    哪些因缺密钥而不可用），而 `/signals/sources` 描述**已有数据**
    （库里某个类型有多少条）。两者共同构成「数据源覆盖」的完整视图。
    """
    from app.collector import describe_adapters

    rows = describe_adapters()
    return ok({
        "adapters": rows,
        "total": len(rows),
        "available": sum(1 for r in rows if r["available"]),
        "default_enabled": sum(1 for r in rows if r["enabled_by_default"]),
        # 《设计文档》§7.2 验收标准：覆盖至少 5 个数据源
        "coverage_ok": sum(1 for r in rows if r["available"]) >= 5,
    })


@router.post("/signals/collect", summary="触发信号采集（对齐《技术文档》§3.1）")
def collect_signals(payload: CollectIn, db: Session = Depends(get_db),  # noqa: B008
                    tid: str = Depends(tenant_id)):  # noqa: B008
    """执行一轮真实采集：数据源 → 采集器 → 清洗 → 实体抽取 → 写库 → 重建索引。

    `dry_run=true` 时只采集与清洗，不写库 —— 首次接入新数据源时建议先干跑，
    确认抓取质量后再正式落库。
    """
    from app.collector import run_collection

    report = run_collection(
        db, tid,
        adapter_keys=payload.sources or None,
        keywords=payload.keywords or None,
        limit_per_source=payload.limit_per_source,
        industry=payload.industry,
        dry_run=payload.dry_run,
        reindex=payload.reindex,
    )
    data = report.as_dict()

    db.add(AuditLog(tenant_id=tid, action="signal.collect", target_type="signal",
                    detail={"inserted": data["total_inserted"],
                            "sources": [s["key"] for s in data["sources"]],
                            "dry_run": payload.dry_run}))
    db.commit()

    message = (
        f"干跑完成：抓取 {data['total_fetched']} 条（未写库）"
        if payload.dry_run else
        f"采集完成：新增 {data['total_inserted']} 条，去重 {data['total_duplicated']} 条"
    )
    return ok(data, message=message)


@router.get("/signals/collect/preview", summary="数据源连通性预检")
def collect_preview():
    """只探测各适配器的可用性，不发起真实请求 —— 供前端「数据源」页快速展示。"""
    from app.collector import describe_adapters

    rows = describe_adapters()
    by_type: dict[str, int] = {}
    for r in rows:
        if r["available"]:
            by_type[r["signal_type"]] = by_type.get(r["signal_type"], 0) + 1
    return ok({
        "adapters": rows,
        "available_by_type": by_type,
        "coverage_ok": sum(1 for r in rows if r["available"]) >= 5,
    })
