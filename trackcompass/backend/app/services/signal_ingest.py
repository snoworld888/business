"""信号入库服务：去重、事务、审计 —— API 与采集器共用的唯一写入口。

为什么单独抽出来：
① **去重必须是一处逻辑**。此前 `api/v1/signals.py` 只计算 `content_hash`
   却从不据它判重，采集器每跑一轮就会把同一批文章重复灌进库，直接污染
   趋势雷达（同名趋势堆积的成因之一，本项目已多次踩到「只写入、不防重」）。
② **两条写入路径要行为一致**。手工上传（API）与采集器回传（runner）走
   同一个函数，才能保证去重、审计、embedding 的规则完全相同。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import extract_tech_keywords
from app.collector.normalize import content_hash
from app.core.db import utcnow
from app.models import AuditLog, Signal
from app.services.embeddings import embed

logger = logging.getLogger("trackcompass.collector.ingest")


@dataclass
class IngestReport:
    """一轮写入的结果（用于接口返回与日志）。"""

    received: int = 0
    inserted: int = 0
    duplicated: int = 0
    inserted_ids: list[str] = field(default_factory=list)
    duplicate_titles: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "received": self.received,
            "inserted": self.inserted,
            "duplicated": self.duplicated,
            "inserted_ids": self.inserted_ids,
            "duplicate_titles": self.duplicate_titles[:10],
        }


def _existing_keys(db: Session, tenant_id: str) -> tuple[set[str], set[str]]:
    """一次性取回本租户已有的 content_hash 与 url 集合，避免逐条查库。"""
    rows = db.execute(
        select(Signal.content_hash, Signal.url).where(Signal.tenant_id == tenant_id)
    ).all()
    hashes = {r[0] for r in rows if r[0]}
    urls = {r[1] for r in rows if r[1]}
    return hashes, urls


def persist_signals(
    db: Session,
    tenant_id: str,
    payloads: Iterable[dict[str, Any]],
    *,
    source_hint: str = "collector",
    dedupe: bool = True,
    audit: bool = True,
) -> IngestReport:
    """批量写入信号。

    - payload 需符合 `SignalIn` 的键名（`normalize.normalize()` 的输出即为该形态）。
    - `dedupe=True` 时按 content_hash + url 双重判重，已存在的条目计入 `duplicated`。
    - 单条异常不影响整批：逐条处理，异常记录并跳过。
    """
    report = IngestReport()
    items = list(payloads)
    report.received = len(items)
    if not items:
        return report

    seen_hashes, seen_urls = _existing_keys(db, tenant_id) if dedupe else (set(), set())

    for payload in items:
        title = (payload.get("title") or "").strip()
        if not title:
            continue
        # ⚠️ hash 用**采集原始标题**计算，与 api/v1/signals.py 的算法一致
        h = content_hash(payload.get("_raw_title") or title)
        url = (payload.get("url") or "").strip() or None

        if dedupe and (h in seen_hashes or (url and url in seen_urls)):
            report.duplicated += 1
            if len(report.duplicate_titles) < 10:
                report.duplicate_titles.append(title[:60])
            continue

        try:
            # ------- 实体抽取（《技术文档》§3.2 管道的必要一环）-------
            # 采集到的关键词往往为空（RSS 不带标签、Crossref 无 subject），
            # 而趋势雷达的「方向」与能力匹配都依赖 keywords。这里用既有能力
            # 从标题+正文抽技术词，与 `api/v1/signals.py` 的规则保持一致。
            content = payload.get("content") or ""
            auto_kw = extract_tech_keywords(f"{title} {content}", limit=8)
            merged_kw = list(dict.fromkeys([*(payload.get("keywords") or []), *auto_kw]))[:12]

            sig = Signal(
                tenant_id=tenant_id,
                signal_type=payload.get("signal_type") or "news",
                source=payload.get("source") or source_hint,
                title=title[:500],
                content=content,
                url=url,
                industry=payload.get("industry"),
                region=payload.get("region"),
                keywords=merged_kw,
                entities=payload.get("entities") or {},
                metrics=payload.get("metrics") or {},
                sentiment=float(payload.get("sentiment") or 0.0),
                published_at=_as_dt(payload.get("published_at")),
                content_hash=h,
            )
            sig.embedding = embed(f"{sig.title} {sig.content}")
            db.add(sig)
            db.flush()          # 拿 id，但不提交 —— 整批一个事务
        except Exception as exc:                      # noqa: BLE001
            logger.warning("信号写入失败，已跳过：%s（%s）", title[:40], exc)
            db.rollback()
            seen_hashes, seen_urls = _existing_keys(db, tenant_id)
            continue

        seen_hashes.add(h)
        if url:
            seen_urls.add(url)
        report.inserted += 1
        report.inserted_ids.append(sig.id)

    if audit and report.inserted:
        db.add(AuditLog(
            tenant_id=tenant_id, action="signal.ingest", target_type="signal",
            detail={"inserted": report.inserted, "duplicated": report.duplicated,
                    "source_hint": source_hint},
        ))
    db.commit()
    return report


def _as_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    return utcnow()


__all__ = ["IngestReport", "persist_signals"]
