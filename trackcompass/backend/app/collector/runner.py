"""采集运行器：编排一轮完整的采集 → 清洗 → 入库 → 索引。

对齐《技术文档》§3.2 数据管道的本地实现：

    数据源 → 采集器 → [清洗/标准化] → 实体抽取 → 写库 → 重建索引

    数据源  ← sources/*.py        （各站点适配器）
    采集器  ← base.HttpClient     （限流/重试/UA）
    清洗    ← normalize.py        （去噪/语言/行业/质量闸门）
    实体抽取 ← agents.base.extract_tech_keywords（既有能力，复用）
    写库    ← services.signal_ingest（去重/事务/审计）
    索引    ← services.analysis.reindex + refresh_graph

两条硬原则：
① **单源失败不阻断整轮** —— 每个源独立执行、独立入库、独立上报
   （ok / failed / skipped），绝不静默降级。
② **逐源入库** —— 而不是全部汇总后一次性写。这样每个源的
   `inserted` / `duplicated` 归因精确，一个源的异常也不会连带回滚其他源。
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.collector.base import CollectContext, HttpClient, RawSignal, SourceAdapter
from app.collector.normalize import normalize
from app.collector.registry import DEFAULT_ENABLED, build_adapters
from app.core.visibility import PUBLIC_TENANT_ID
from app.services.signal_ingest import persist_signals

logger = logging.getLogger("trackcompass.collector.runner")


@dataclass
class SourceResult:
    """单个数据源的采集结果。"""

    key: str
    name: str
    signal_type: str
    status: str                     # ok | failed | skipped
    fetched: int = 0
    normalized: int = 0
    dropped: int = 0
    inserted: int = 0
    duplicated: int = 0
    elapsed_ms: int = 0
    error: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CollectReport:
    """一轮采集的完整报告。"""

    started_at: str
    elapsed_ms: int = 0
    keywords: list[str] = field(default_factory=list)
    industry: str | None = None
    sources: list[SourceResult] = field(default_factory=list)
    total_fetched: int = 0
    total_inserted: int = 0
    total_duplicated: int = 0
    total_dropped: int = 0
    indexed_documents: int = 0
    graph: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sources"] = [s.as_dict() for s in self.sources]
        d["ok_sources"] = sum(1 for s in self.sources if s.status == "ok")
        d["failed_sources"] = sum(1 for s in self.sources if s.status == "failed")
        d["skipped_sources"] = sum(1 for s in self.sources if s.status == "skipped")
        return d


# ------------------------------------------------------------------ 关键词
def resolve_keywords(db: Session, tenant_id: str, extra: list[str] | None = None,
                     limit: int = 6) -> tuple[list[str], str | None]:
    """确定本轮采集的检索词：显式入参 > 公司画像技术栈 > 兜底。

    采集必须围绕**公司关注领域**，否则信号库会被无关内容淹没
    （设计 §7.2 要求趋势与公司能力相关，这里的检索词正是那个入口）。

    返回 `(检索词, 行业提示)`。⚠️ 行业提示**始终**取自当前公司画像，
    即使用户显式传了检索词 —— 否则落库信号的 industry 会为空，
    趋势雷达就无法把它归入任何赛道。
    """
    from app.models import CompanyProfile

    profile = db.query(CompanyProfile).order_by(
        CompanyProfile.updated_at.desc()
    ).first()
    industry = profile.industry if profile else None

    if extra:
        return [k.strip() for k in extra if k and k.strip()][:limit], industry

    if profile is None:
        return ["人工智能"], industry

    words: list[str] = []
    for item in (profile.tech_stack or []):
        name = item.get("name") if isinstance(item, dict) else item
        if name and str(name).strip():
            words.append(str(name).strip())
            if len(words) >= limit:
                break
    return (words or ["人工智能"]), industry


# ------------------------------------------------------------------ 主流程
def run_collection(
    db: Session,
    tenant_id: str,
    *,
    adapter_keys: list[str] | None = None,
    keywords: list[str] | None = None,
    limit_per_source: int = 20,
    industry: str | None = None,
    dry_run: bool = False,
    reindex: bool = True,
) -> CollectReport:
    """执行一轮采集。`dry_run=True` 只采集与清洗，不写库也不建索引。"""
    t_start = time.monotonic()
    started = datetime.now(timezone.utc)
    report = CollectReport(started_at=started.isoformat(), dry_run=dry_run)

    kws, profile_industry = resolve_keywords(db, tenant_id, keywords)
    report.keywords = kws
    report.industry = industry or profile_industry

    client = HttpClient()
    adapters = build_adapters(adapter_keys or DEFAULT_ENABLED, client)
    ctx = CollectContext(
        keywords=kws, limit=max(1, limit_per_source),
        industry=report.industry, since=None,
    )

    try:
        for adapter in adapters:
            result = _collect_one(adapter, ctx)
            if not dry_run and result.status == "ok" and result._pending:  # noqa: SLF001
                # 写入**公共层**：信号是外部客观情报，不属于触发采集的租户
                # （见 `core.visibility`）。`tenant_id` 仅用于解析检索词。
                _persist_one(db, PUBLIC_TENANT_ID, adapter, result)
            report.sources.append(result)
    finally:
        client.close()

    report.total_fetched = sum(s.fetched for s in report.sources)
    report.total_inserted = sum(s.inserted for s in report.sources)
    report.total_duplicated = sum(s.duplicated for s in report.sources)
    report.total_dropped = sum(s.dropped for s in report.sources)

    if reindex and not dry_run and report.total_inserted:
        report.indexed_documents, report.graph = _reindex(db)

    report.elapsed_ms = int((time.monotonic() - t_start) * 1000)
    return report


class _SourceResult(SourceResult):
    """带内部 pending 缓存的中间结果（不参与序列化）。"""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._pending: list[dict[str, Any]] = []

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if not k.startswith("_")}


def _collect_one(adapter: SourceAdapter, ctx: CollectContext) -> _SourceResult:
    """采集 + 清洗单个源。失败只记录，不抛出。"""
    res = _SourceResult(key=adapter.key, name=adapter.name,
                        signal_type=adapter.signal_type, status="ok")

    ok, reason = adapter.available()
    if not ok:
        res.status = "skipped"
        res.note = reason
        return res

    if adapter.requires_keywords and not [k for k in ctx.keywords if k and k.strip()]:
        res.status = "skipped"
        res.note = "该数据源需指定检索词（无检索词时会拉回全量无关内容）"
        return res

    t0 = time.monotonic()
    try:
        raws: list[RawSignal] = adapter.fetch(ctx)
    except NotImplementedError as exc:
        res.status = "skipped"
        res.note = str(exc)[:200]
        res.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return res
    except Exception as exc:                          # noqa: BLE001
        res.status = "failed"
        res.error = f"{type(exc).__name__}: {exc}"[:300]
        res.elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("数据源 %s 采集失败：%s", adapter.key, res.error)
        return res

    res.fetched = len(raws)
    for raw in raws:
        payload = normalize(raw, industry_hint=ctx.industry)
        if payload is None:
            res.dropped += 1
            continue
        payload["_raw_title"] = raw.title          # 供 content_hash 使用，入库后丢弃
        res._pending.append(payload)
        res.normalized += 1

    res.elapsed_ms = int((time.monotonic() - t0) * 1000)
    if res.fetched == 0:
        res.note = "该源本次未返回条目"
    return res


def _persist_one(db: Session, tenant_id: str, adapter: SourceAdapter,
                 res: _SourceResult) -> None:
    """把单个源的结果入库（独立事务，异常不影响其他源）。"""
    try:
        ingest = persist_signals(db, tenant_id, res._pending,
                                 source_hint=adapter.key)
        res.inserted = ingest.inserted
        res.duplicated = ingest.duplicated
    except Exception as exc:                          # noqa: BLE001
        db.rollback()
        res.status = "failed"
        res.error = f"入库失败 {type(exc).__name__}: {exc}"[:300]
        logger.warning("数据源 %s 入库失败：%s", adapter.key, res.error)


def _reindex(db: Session) -> tuple[int, dict[str, Any]]:
    from app.services.analysis import refresh_graph, reset_and_reindex

    count = reset_and_reindex(db)
    graph = refresh_graph(db)
    return count, graph


__all__ = ["CollectReport", "SourceResult", "run_collection", "resolve_keywords"]
