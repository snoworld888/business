"""编排服务：把数据库、RAG、Agent 流水线串起来。

这一层是「决策层」的落地，API 只调用这里，不直接碰 Agent。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import AGENT_REGISTRY, Orchestrator, SignalView, default_workflow
from app.agents.base import AgentState
from app.agents.monitor import apply_calibration
from app.core.visibility import visible
from app.models import Alert, CompanyProfile, Opportunity, Scorecard, Signal, Trend
from app.services.graph import build_from_signals
from app.services.rag import RetrievedDoc, RAGEngine, get_rag, reset_rag
from app.services.report import ReportContext, build_content

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- 趋势落库
def _industry_from_evidence(db: Session, evidence: Any) -> str | None:
    """从趋势的证据信号推断所属行业（取众数）。

    为什么需要：LLM 路径不保证输出 `industry`（prompt 里没要求），
    实测扫出来的趋势 `industry` 全为 None ⇒ 无法归属赛道。
    证据信号的 `industry` 是采集/入库时按内容判定的，是最可靠的事实来源。
    """
    from collections import Counter

    from app.models import Signal

    ids = [e.get("signal_id") for e in (evidence or []) if isinstance(e, dict)]
    ids = [i for i in ids if i]
    if not ids:
        return None
    rows = db.scalars(select(Signal.industry).where(Signal.id.in_(ids))).all()
    counts = Counter(i for i in rows if i)
    return counts.most_common(1)[0][0] if counts else None


def upsert_trends(db: Session, tenant_id: str, trends: Any,
                  industry: str | None = None, limit: int | None = None) -> int:
    """趋势落库（幂等 upsert），**所有入口必须共用这一份实现**。

    为什么必须统一（实测教训 2026-09-14）
    ------------------------------------
    此前两个入口各写各的：

    | 入口 | 匹配键 |
    | --- | --- |
    | `sync_analysis`（机会分析） | `name + industry` |
    | `persist_scan_result`（雷达扫描） | `tenant_id + name` |

    同一主题从两个入口进来会各自建一条 ⇒ 雷达页堆积出 38 条近重复趋势
    （「低成本快速部署的工业视觉质检解决方案」/「…方案」/「工业视觉与智能质检」
    /「工业视觉与智能质检规模化落地」…），因为 LLM 每次生成的趋势名都不同，
    而精确名匹配永远命中不了。

    统一为 `tenant_id + name`：同一租户内同名趋势只应存在一条。

    另外 `_capability`（能力画像匹配）此前**只有扫描入口写**，机会分析入口不写
    ⇒ 列表页的 `⚑` 标签时有时无。这里一并统一。
    """
    rows = [t for t in (trends or []) if isinstance(t, dict) and t.get("name")]
    if limit is not None:
        rows = rows[:limit]
    for t in rows:
        existing = db.scalar(
            select(Trend).where(Trend.tenant_id == tenant_id, Trend.name == t["name"])
        )
        if existing is None:
            existing = Trend(tenant_id=tenant_id, name=t["name"])
            db.add(existing)
        existing.description = t.get("description", "")
        # industry 兜底链（实测缺陷 2026-09-15）：
        # LLM 路径的 prompt 不要求输出 `industry`，而 `sync_analysis` 入口
        # 也没传 `industry` 参数 ⇒ 扫出来的趋势 `industry` **全是 None**。
        # 后果有二：① 趋势无法归属赛道；② 入池时 `trend.industry or trend.name`
        # 把**趋势名**当成赛道名建出脏 Track 记录（实测 tracks 表混进
        # 「AI算力硬件高精度光学检测」）。
        # 最可靠的事实来源是**证据信号自身**——它的 industry 是入库时按内容判定的。
        existing.industry = (t.get("industry") or industry
                             or _industry_from_evidence(db, t.get("evidence")))
        existing.confidence = float(t.get("confidence", 0) or 0)
        existing.direction = t.get("direction", "tailwind")
        existing.score = float(t.get("score", 0) or 0)
        # 只落**客观标签** `_tags` = 趋势自身的技术标签（与任何公司无关）。
        # 匹配结果（match / matched）**不再落库**，改由接口层用
        # 「当前公司能力词 ∩ 趋势标签」实时计算 ——
        # 原先把 matched 冻结在这里，既没记录是给哪家公司算的（切公司即失效），
        # 又导致每次修复匹配逻辑都必须回算历史数据。
        # 内部键前缀 `_` 会被接口层从公开的 factors 里剔除。
        raw_factors = t.get("factors") or {}
        factors = {k: v for k, v in raw_factors.items()
                   if not str(k).startswith("_")}
        factors["_tags"] = list(raw_factors.get("_tags") or [])
        existing.factors = factors
        existing.evidence = t.get("evidence", [])
    return len(rows)


# ---------------------------------------------------------------- 索引
def reindex(db: Session, rag: RAGEngine | None = None) -> int:
    """把**公共信号**全量灌入向量库与 BM25 索引。

    ⚠️ 只索引公共层（`tenant_id IS NULL`）：RAGFlow 是**单一 dataset**，
    索引是全局共享的。若把租户私有信号也灌进去，会造成跨租户检索泄露。
    私有信号的检索需要按租户分区索引（待办，见 `core.visibility` 文档）。
    """
    rag = rag or get_rag()
    signals = db.scalars(select(Signal).where(Signal.tenant_id.is_(None))).all()
    docs = [
        RetrievedDoc(
            signal_id=s.id, source=s.source, signal_type=s.signal_type,
            title=s.title, text=s.content or s.title, url=s.url,
            metadata={"industry": s.industry, "keywords": s.keywords or []},
        )
        for s in signals
    ]
    return rag.index(docs)


def reset_and_reindex(db: Session) -> int:
    from app.services.vector_store import reset_vector_store

    reset_rag()
    reset_vector_store()
    return reindex(db)


# ---------------------------------------------------------------- 画像
def build_profile_input(db: Session, tenant_id: str) -> dict[str, Any]:
    # 「当前公司画像」= 最近保存的那一份（updated_at 倒序）。
    # ⚠️ 该口径与 `app/api/deps.py:get_profile` 必须保持一致 ——
    #    画像页切换公司并保存后，七 Agent 分析会立刻以新公司为主体。
    profile = db.scalar(
        select(CompanyProfile).where(CompanyProfile.tenant_id == tenant_id)
        .order_by(CompanyProfile.updated_at.desc(), CompanyProfile.created_at.desc())
    )
    if profile is None:
        return {}
    scorecard = db.scalar(
        select(Scorecard).where(Scorecard.tenant_id == tenant_id,
                                Scorecard.is_default.is_(True))
    )
    return {
        "name": profile.name,
        "industry": profile.industry,
        "scale": profile.scale,
        "tech_stack": profile.tech_stack or [],
        # ⚠️ `patents` 必须传（2026-09-16 修复）：`trend.capability_terms` 明确遍历
        # `("tech_stack", "patents")` 两个字段，而这里此前只给了 `tech_stack`
        # ⇒ **分析路径的专利能力恒为空**，与接口层 `deps.current_capability_terms`
        # （含 patents）口径不一致 —— 同一趋势在两处会得到不同的能力匹配。
        "patents": profile.patents or [],
        "tech_description": profile.tech_description,
        "talents": profile.talents or {},
        "funding": profile.funding or {},
        "channels": profile.channels or [],
        "customers": profile.customers or [],
        "scorecard_weights": (scorecard.weights if scorecard else None),
    }


# ---------------------------------------------------------------- 分析依据
def profile_used(db: Session, tenant_id: str,
                 analysis: dict[str, Any]) -> dict[str, Any]:
    """本次分析**依据了哪些公司能力**（供 `/opportunities/{id}/analyze` 的透明性展示）。

    背景：趋势雷达的「能力匹配」是**画像标注**而非趋势来源，但结果里只给出结论
    （`⚑ 计算机视觉`），看不到「这个词是从技术栈还是专利抽出来的」。
    用户实测反馈：「会使用公司的标签、专利这些吗？现在的分析看不到这些内容」。

    返回::

        {
          "company": "智造未来科技",
          "terms": ["计算机视觉", ..., "表面缺陷检测"],
          "by_source": [{"field": "patents",
                         "raw": "小样本工业缺陷识别模型训练方法",
                         "terms": ["工业缺陷识别", "模型训练"]}],
          "matched": [{"term": "计算机视觉", "trends": ["工业视觉与…"]}]
        }
    """
    from app.agents.base import AgentState
    from app.agents.trend import capability_terms_by_source

    profile = build_profile_input(db, tenant_id)
    if not profile:
        return {"company": None, "terms": [], "by_source": [], "matched": [],
                "note": "当前租户没有能力画像 —— 本次分析未使用任何公司能力"}
    state = AgentState(query="", profile=profile)
    by_field = capability_terms_by_source(state)

    rows: list[dict[str, Any]] = []
    terms: list[str] = []
    for field, items in by_field.items():
        for item in items:
            rows.append({"field": field, "raw": item["raw"], "terms": item["terms"]})
            for t in item["terms"]:
                if t not in terms:
                    terms.append(t)

    trends = (((analysis.get("results") or {}).get("trend") or {}) or {}).get("trends") or []
    hit: dict[str, list[str]] = {}
    for tr in trends:
        for c in (tr.get("matched_capabilities") or []):
            hit.setdefault(str(c), [])
            name = str(tr.get("name") or "")
            if name and name not in hit[str(c)]:
                hit[str(c)].append(name)

    return {
        "company": profile.get("name"),
        "terms": terms,
        "by_source": rows,
        "matched": [{"term": k, "trends": v} for k, v in hit.items()],
    }


# ---------------------------------------------------------------- 主流程
def run_analysis(
    db: Session,
    *,
    tenant_id: str,
    query: str = "",
    industry: str | None = None,
    region: str | None = None,
    days: int = 3650,
    max_loops: int = 1,
    selected_agents: list[str] | None = None,
) -> dict[str, Any]:
    """执行七 Agent 流水线，返回结果 dict（不落库，供纯分析接口使用）。"""
    rag = get_rag()
    if rag.count_total() == 0:
        reindex(db, rag)

    profile = build_profile_input(db, tenant_id)

    stmt = visible(select(Signal), Signal, tenant_id)
    if industry:
        stmt = stmt.where(Signal.industry == industry)
    signals = db.scalars(stmt).all()

    state = AgentState(
        query=query or industry or "综合赛道分析",
        industry=industry, region=region, days=days,
        profile=profile, signals=[SignalView.from_orm(s) for s in signals], rag=rag,
    )

    workflow = default_workflow(max_loops=max_loops)
    if selected_agents:
        wanted = [AGENT_REGISTRY[k]() for k in selected_agents if k in AGENT_REGISTRY]
        if wanted:
            from app.agents.orchestrator import Workflow

            workflow = Workflow(name="selected", layers=[[a] for a in wanted],
                                max_loops=max_loops)
    result = Orchestrator(workflow).run(state)
    return {
        "results": result.results,
        "evidence": state.evidence,
        "trace": result.trace,
        "loops": result.loops,
        "errors": result.errors,
        "signal_count": len(signals),
    }


def persist_analysis(
    db: Session,
    *,
    tenant_id: str,
    opportunity: Opportunity,
    analysis: dict[str, Any],
) -> Opportunity:
    """把一次分析结果写回机会卡片，并落库趋势与预警。"""
    results = analysis.get("results", {})
    evidence = analysis.get("evidence", [])
    decision = results.get("decision", {}) or {}

    opportunity.analysis = results
    opportunity.evidence = evidence
    opportunity.score = {
        "total_score": decision.get("total_score", 0),
        "dimensions": decision.get("dimensions", []),
        "explained": decision.get("explained", {}),
        "risk_alerts": decision.get("risk_alerts", []),
        "verdict": decision.get("verdict", {}),
        "conflicts": decision.get("conflicts", []),
    }
    opportunity.risk_alerts = decision.get("risk_alerts", [])
    if opportunity.summary == "":
        market = results.get("market", {}) or {}
        opportunity.summary = (
            f"AI 综合评分 {decision.get('total_score', 0)}，"
            f"TAM 约 {float(market.get('TAM', 0) or 0) / 1e8:.1f} 亿元，"
            f"竞争威胁等级 {(results.get('competition') or {}).get('threat_level', '—')}。"
        )

    # ---- 趋势落库（幂等 upsert，与扫描入口共用 `upsert_trends`）
    upsert_trends(db, tenant_id, (results.get("trend", {}) or {}).get("trends", []), limit=10)

    # ---- 预警落库（按 rule + signal_id 去重）
    track_id = opportunity.track_id
    for a in (results.get("monitor", {}) or {}).get("alerts", []):
        sid = (a.get("payload") or {}).get("signal_id")
        exists = db.scalar(
            select(Alert).where(Alert.rule_name == a.get("type"),
                                Alert.tenant_id == tenant_id,
                                Alert.opportunity_id == opportunity.id)
        ) if sid else None
        if exists:
            continue
        db.add(Alert(
            tenant_id=tenant_id, track_id=track_id, opportunity_id=opportunity.id,
            signal_type=a.get("signal_type", ""), rule_name=a.get("type", ""),
            level=a.get("level", "warning"), title=a.get("title", ""),
            summary=a.get("summary", ""),
            payload={**(a.get("payload") or {}), "advice": a.get("advice"),
                     "source": a.get("source"), "url": a.get("url")},
        ))

    db.commit()
    db.refresh(opportunity)
    return opportunity


def generate_report_content(
    db: Session, *, tenant_id: str, opportunity: Opportunity,
    template: str = "investment_memo",
) -> dict[str, Any]:
    from app.models import Experiment

    profile = build_profile_input(db, tenant_id)
    experiments = db.scalars(
        select(Experiment).where(Experiment.opportunity_id == opportunity.id)
    ).all()
    ctx = ReportContext(
        title=f"{opportunity.title} 决策报告",
        opportunity={"summary": opportunity.summary, "title": opportunity.title},
        score=opportunity.score or {},
        analysis=opportunity.analysis or {},
        evidence=opportunity.evidence or [],
        profile=profile,
        experiments=[
            {"title": e.title, "method": e.method, "conclusion": e.conclusion}
            for e in experiments
        ],
    )
    content = build_content(ctx, template)
    content["generated_at"] = datetime.now(timezone.utc).isoformat()
    return content


def refresh_graph(db: Session) -> dict[str, Any]:
    store = build_from_signals(db)
    stats = {"nodes": 0, "edges": 0}
    if hasattr(store, "g"):
        stats = {"nodes": store.g.number_of_nodes(), "edges": store.g.number_of_edges()}
    return stats


def calibration_preview(db: Session, tenant_id: str) -> dict[str, Any]:
    """监控 Agent 的校准建议 → 预览新权重（不落库）。"""
    scorecard = db.scalar(
        select(Scorecard).where(Scorecard.tenant_id == tenant_id,
                                Scorecard.is_default.is_(True))
    )
    if scorecard is None:
        return {"error": "未找到默认评分卡"}
    from app.agents.monitor import MonitorAlertAgent
    from app.models import Signal as SignalModel

    signals = db.scalars(select(SignalModel)).all()
    state = AgentState(
        query="校准", signals=[SignalView.from_orm(s) for s in signals],
        results={}, profile=build_profile_input(db, tenant_id),
    )
    output = MonitorAlertAgent().rule(state)
    applied = apply_calibration(scorecard.weights or {}, output.get("calibration", []))
    return {
        "suggestions": output.get("calibration", []),
        "current_weights": scorecard.weights,
        **applied,
    }
