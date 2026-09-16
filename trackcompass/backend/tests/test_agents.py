"""七 Agent 与编排器测试：覆盖正向与异常路径。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.agents import AGENT_CATALOG, AGENT_REGISTRY, Orchestrator, default_workflow
from app.agents.base import (
    AgentState,
    SignalView,
    build_retrieval_query,
    extract_tech_keywords,
    signal_weight,
)
from app.agents.competition import CompetitionIntelAgent
from app.agents.decision import DecisionSynthesisAgent
from app.agents.demand import DemandDiscoveryAgent, demand_pool
from app.agents.market import MarketSizingAgent
from app.agents.monitor import (
    ALERT_LEVELS,
    MonitorAlertAgent,
    apply_calibration,
    level_rank,
    level_variants,
    normalize_alert_level,
)
from app.agents.tech import TechAssessmentAgent
from app.agents.base import AgentState, SignalView, extract_patent_terms
from app.agents.trend import TrendReconAgent, capability_terms, capability_terms_by_source
from app.services.rag import RAGEngine
from app.services.scoring import DEFAULT_DIMENSIONS


def _sig(idx: int, stype: str, title: str, content: str = "", industry: str = "AI+制造",
         keywords=None, metrics=None, days_ago: int = 1, sentiment: float = -0.6,
         entities=None) -> SignalView:
    return SignalView(
        id=f"sig-{idx}", signal_type=stype, source=f"src-{stype}", title=title,
        content=content or title, industry=industry, keywords=keywords or [],
        metrics=metrics or {}, sentiment=sentiment,
        published_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        entities=entities or {},
    )


@pytest.fixture()
def signals() -> list[SignalView]:
    data = [
        _sig(1, "policy", "支持智能质检规模化应用的指导意见", "鼓励试点，给予补贴支持",
             keywords=["智能质检"], days_ago=3),
        _sig(2, "policy", "医疗人工智能规范化应用指导意见", "支持多中心验证",
             industry="AI+医疗", keywords=["医疗器械"], days_ago=5),
        _sig(3, "funding", "某视觉科技有限公司完成 B 轮 3.2 亿元融资",
             "头部产业资本领投，加速规模化落地", keywords=["智能质检"],
             metrics={"amount": 320_000_000, "companies": ["某视觉科技有限公司"]},
             days_ago=4, sentiment=0.8, entities={"company": ["某视觉科技有限公司"]}),
        _sig(4, "funding", "某产业集团设立 20 亿元智能制造基金",
             "计划部署 50 个示范工厂", keywords=["智能排产"],
             metrics={"amount": 2_000_000_000, "companies": ["某产业集团"]},
             days_ago=10, sentiment=0.7, entities={"company": ["某产业集团"]}),
        _sig(5, "patent", "基于深度学习的表面缺陷检测方法",
             "相关环境验证，已小批量量产试用", keywords=["智能质检", "深度学习"],
             metrics={"trl_hint": 6}, days_ago=6, sentiment=0.2,
             entities={"company": ["某视觉科技有限公司"]}),
        _sig(6, "paper", "面向工业质检的自监督表征学习新架构",
             "刷新 SOTA，实验室验证阶段", keywords=["智能质检"], days_ago=2, sentiment=0.3),
        _sig(7, "social", "质检漏检问题一直是产线最大痛点",
             "漏检多、精度不够、还得人工复检，急需低成本方案，预算有限但愿意付费",
             keywords=["智能质检"], days_ago=1, sentiment=-0.85),
        _sig(8, "social", "一套视觉检测系统太贵，中小企业用不起",
             "报价太高，预算不够，性价比低，非常迫切", keywords=["智能质检"],
             days_ago=3, sentiment=-0.8),
        _sig(9, "review", "智能质检软件使用反馈：准确率不足",
             "误检率高，良率误判，客服响应慢", keywords=["智能质检"],
             days_ago=8, sentiment=-0.75),
        _sig(10, "bidding", "某集团智能质检系统公开招标",
             "预算 1800 万元，6 个月完成 8 条产线部署", keywords=["智能质检"],
             metrics={"amount": 18_000_000}, days_ago=5, sentiment=0.4),
        _sig(11, "recruit", "急招工业视觉算法工程师 30 人",
             "熟悉缺陷检测与模型轻量化部署", keywords=["智能质检", "深度学习"],
             days_ago=6, sentiment=0.1),
        _sig(12, "news", "头部集团加速布局工业视觉",
             "投入 50 亿元通过生态合作锁定渠道", keywords=["智能质检"],
             metrics={"companies": ["某产业集团"]}, days_ago=7, sentiment=0.3,
             entities={"company": ["某产业集团"]}),
    ]
    return data


@pytest.fixture()
def state(signals) -> AgentState:
    return AgentState(
        query="AI 质检", industry="AI+制造", days=90, signals=signals,
        profile={
            "industry": "AI+制造",
            "tech_stack": ["计算机视觉", "深度学习"],
            "customers": ["汽车零部件"], "talents": {"算法": 18, "工程": 26},
            "funding": {"amount": 100_000_000},
        },
    )


# ------------------------------------------------------------------ 注册表
def test_agent_registry_has_seven_agents():
    assert len(AGENT_CATALOG) == 7
    assert set(AGENT_REGISTRY) == {"trend", "demand", "market", "competition",
                                   "tech", "decision", "monitor"}
    for cls in AGENT_REGISTRY.values():
        assert issubclass(cls, __import__("app.agents.base", fromlist=["BaseAgent"]).BaseAgent)


# ------------------------------------------------------------------ 趋势侦察
class TestTrendAgent:
    def test_produces_trends_with_evidence(self, state):
        out = TrendReconAgent().run(state)
        assert out["trends"], "应至少产出一条趋势"
        top = out["trends"][0]
        assert top["name"]
        assert 0 <= top["score"] <= 1
        assert 0 <= top["confidence"] <= 1
        assert top["direction"] in ("tailwind", "headwind")
        assert top["evidence"], "每条趋势至少应有 1 条证据"
        assert {"signal_id", "source", "title"} <= set(top["evidence"][0])

    def test_trends_sorted_desc(self, state):
        trends = TrendReconAgent().run(state)["trends"]
        scores = [t["score"] for t in trends]
        assert scores == sorted(scores, reverse=True)

    def test_policy_raises_score(self):
        a = AgentState(query="x", signals=[_sig(1, "news", "某技术相关新闻")])
        b = AgentState(query="x", signals=[_sig(1, "policy", "支持某技术推广的指导意见")])
        assert (TrendReconAgent().rule(b)["trends"][0]["score"]
                >= TrendReconAgent().rule(a)["trends"][0]["score"])

    def test_empty_signals_returns_empty(self):
        out = TrendReconAgent().rule(AgentState(query="空"))
        assert out["trends"] == []


# ------------------------------------------------------------------ 需求挖掘
class TestDemandAgent:
    def test_clusters_pain_points(self, state):
        out = DemandDiscoveryAgent().run(state)
        names = {p["name"] for p in out["pain_points"]}
        assert "检测精度低" in names
        assert "部署成本高" in names

    def test_demand_score_in_range_and_sorted(self, state):
        pts = DemandDiscoveryAgent().rule(state)["pain_points"]
        scores = [p["demand_score"] for p in pts]
        assert all(1.0 <= s <= 5.0 for s in scores)
        assert scores == sorted(scores, reverse=True)

    def test_suggested_experiments_not_empty(self, state):
        out = DemandDiscoveryAgent().rule(state)
        assert out["suggested_experiments"]
        assert len(out["suggested_experiments"]) <= 5

    def test_samples_and_evidence_ref(self, state):
        pts = DemandDiscoveryAgent().rule(state)["pain_points"]
        assert pts[0]["samples"]
        assert pts[0]["evidence_ref"]

    def test_no_relevant_signals(self):
        out = DemandDiscoveryAgent().rule(
            AgentState(query="x", signals=[_sig(1, "policy", "无关政策")]))
        assert out["pain_points"] == []
        assert out["demand_score_5"] == 0.0

    # ---------------------------------------------------------- 口径放宽（2026-09-16，用户裁决 ③）
    # 背景：真实信号库长期只有 news/paper/policy（需求侧一条都没有），
    # 只认需求侧的写法会让需求强度恒为 0 —— 见 docs/遗留项台账.md L-15。

    def test_context_types_accepted_as_evidence(self):
        """只有补充类型（论文/产业媒体）时也必须能出痛点，不能归零。"""
        sigs = [
            _sig(1, "paper", "工业质检小样本标注成本高", "标注成本高、周期长，尚未解决"),
            _sig(2, "news", "产线仍靠人工复检", "人工复检多，严重依赖老师傅"),
        ]
        out = DemandDiscoveryAgent().rule(AgentState(query="质检", signals=sigs))
        assert out["pain_points"], "补充类型必须能聚类出痛点（否则需求探针恒空）"
        assert out["sample_size"] == 2
        assert {p["name"] for p in out["pain_points"]} >= {"部署成本高", "人工复检多"}

    def test_demand_side_ranks_before_context(self):
        """组池顺序：需求侧（用户直接发声）在前，补充类型在后。"""
        state = AgentState(query="质检", signals=[
            _sig(1, "paper", "一篇论文"), _sig(2, "social", "一条用户发声")])
        assert [s.signal_type for s in demand_pool(state)] == ["social", "paper"]

    def test_prompt_count_matches_pool(self):
        """prompt 自述的条数必须等于真实取数。

        踩过的坑：`build_prompt` 曾自己写 `by_type(需求侧五类)`，
        而真正送进 prompt 的 context 来自 `retrieve()`（全量信号）⇒
        prompt 自称「以下是 0 条信号」却塞满一屏证据，模型可信度无从判断。
        """
        agent = DemandDiscoveryAgent()
        state = AgentState(query="质检", signals=[_sig(1, "paper", "质检论文")])
        prompt = agent.build_prompt(state, "[E1] 某论文")
        assert "以下是 1 条需求相关信号" in prompt
        assert "paper" in prompt


# ------------------------------------------------------------------ 市场测算
class TestMarketAgent:
    def test_produces_tam_sam_som(self, state):
        out = MarketSizingAgent().run(state)
        assert out["TAM"] > out["SAM"] > out["SOM"] > 0
        assert out["growth_rate"] > 0
        assert out["assumptions"], "必须给出测算假设"

    def test_falls_back_to_default_baseline(self):
        out = MarketSizingAgent().rule(AgentState(query="x", industry="未知行业"))
        assert out["TAM"] > 0


# ------------------------------------------------------------------ 竞争情报
class TestCompetitionAgent:
    def test_discovers_players_from_entities(self, state):
        out = CompetitionIntelAgent().run(state)
        assert "某视觉科技有限公司" in out["players"]
        assert out["threat_level"] in ("low", "medium", "high")
        assert 0 <= out["threat_score"] <= 1

    def test_gaps_and_barriers_present(self, state):
        out = CompetitionIntelAgent().rule(state)
        assert out["gaps"]
        assert out["barriers"]

    def test_major_player_detection(self, state):
        out = CompetitionIntelAgent().rule(state)
        assert out["major_player_count"] >= 1


# ------------------------------------------------------------------ 技术评估
class TestTechAgent:
    def test_six_dimensions(self, state):
        out = TechAssessmentAgent().run(state)
        assert len(out["dimensions"]) == 6
        assert 1 <= float(out["overall_9"]) <= 9
        assert out["weakest"] in {"TRL", "CRL", "BRL", "IPRL", "TMRL", "FRL"}

    def test_trl_reflects_maturity_hints(self):
        mature = AgentState(query="x", signals=[
            _sig(1, "patent", "已量产并规模化认证的技术方案")])
        early = AgentState(query="x", signals=[
            _sig(1, "paper", "实验室原型探索性原理研究")])
        a = {d["key"]: d["score"] for d in TechAssessmentAgent().rule(mature)["dimensions"]}
        b = {d["key"]: d["score"] for d in TechAssessmentAgent().rule(early)["dimensions"]}
        assert a["TRL"] > b["TRL"]

    def test_to_scorecard_dimension(self, state):
        out = TechAssessmentAgent().rule(state)
        dim = TechAssessmentAgent.to_scorecard_dimension(out)
        assert dim["key"] == "tech_maturity"
        assert 1 <= dim["score"] <= 5
        assert dim["rationale"]


# ------------------------------------------------------------------ 决策综合
class TestDecisionAgent:
    def test_produces_seven_dimensions(self, state):
        out = DecisionSynthesisAgent().run(state)
        keys = [d["key"] for d in out["dimensions"]]
        assert keys == ["market_size", "demand", "tech_maturity", "competition",
                        "business_model", "timing", "fit"]
        assert 1.0 <= out["total_score"] <= 5.0

    def test_every_dimension_has_rationale(self, state):
        out = DecisionSynthesisAgent().rule(state)
        assert all(d["rationale"] for d in out["dimensions"])

    def test_verdict_thresholds(self):
        for total, code in ((4.5, "go"), (3.8, "verify"), (3.0, "watch"), (2.0, "hold")):
            assert DecisionSynthesisAgent._verdict(total)["code"] == code

    def test_conflict_detection_high_demand_low_barrier(self, state):
        state.results = {
            "market": {"TAM": 1e10, "growth_rate": 0.3, "assumptions": [],
                       "unit_economics": {"arpu": 300000}},
            "demand": {"demand_score_5": 4.8, "pain_points": [], "coverage": {}},
            "tech": {"overall_5": 4.0, "overall_9": 7.0, "dimensions": [], "weakest": "TRL"},
            "competition": {"barrier_score_5": 2.0, "threat_score": 0.8,
                            "threat_level": "high", "player_count": 9, "gaps": []},
            "trend": {"trends": [{"name": "AI 质检", "score": 0.8,
                                  "confidence": 0.9, "direction": "tailwind",
                                  "policy_support": 0.9, "evidence": []}]},
        }
        out = DecisionSynthesisAgent().rule(state)
        assert any("大厂复制风险" in c for c in out["conflicts"])


# ------------------------------------------------------------------ 监控预警
class TestMonitorAgent:
    def test_detects_major_player_entry(self, state):
        out = MonitorAlertAgent().run(state)
        types = {a["type"] for a in out["alerts"]}
        assert "大厂入场" in types
        assert out["severity"]["critical"] >= 1

    def test_alerts_sorted_by_severity(self, state):
        alerts = MonitorAlertAgent().rule(state)["alerts"]
        order = {"critical": 0, "warning": 1, "info": 2}
        levels = [order[a["level"]] for a in alerts]
        assert levels == sorted(levels)

    def test_amount_threshold_enforced(self):
        small = AgentState(query="x", signals=[
            _sig(1, "funding", "某集团小额融资 500 万元",
                 metrics={"amount": 5_000_000}, entities={"company": ["某集团"]})])
        assert MonitorAlertAgent().rule(small)["alert_count"] == 0

    def test_calibration_normalizes_weights(self, state):
        state.results = {
            "demand": {"demand_score_5": 4.6},
            "competition": {"threat_score": 0.75},
            "tech": {"overall_5": 2.8},
        }
        suggestions = MonitorAlertAgent().rule(state)["calibration"]
        assert suggestions, "应产出校准建议"
        result = apply_calibration(
            {"market_size": 0.2, "demand": 0.2, "tech_maturity": 0.15,
             "competition": 0.15, "business_model": 0.1, "timing": 0.1, "fit": 0.1},
            suggestions)
        assert result["validation"]["ok"] is True

    def test_calibration_empty_when_no_trigger(self):
        out = MonitorAlertAgent().rule(AgentState(query="x", results={}))
        assert out["calibration"] == []


class TestAlertLevelVocabulary:
    """预警级别词表的归一（实测缺陷：严重度汇总恒为 0/0/0）。

    根因：库里混有 `high/medium/low` 与 `High/Medium/Low` 两套非规范写法
    （词表统一前落库、未做迁移），而接口只统计 `critical/warning/info`
    ⇒ 绝大部分预警被静默丢弃。
    """

    def test_alias_and_case_normalized(self):
        for raw, want in [("high", "critical"), ("High", "critical"),
                          ("HIGH", "critical"), ("medium", "warning"),
                          ("Medium", "warning"), ("low", "info"),
                          (" critical ", "critical")]:
            assert normalize_alert_level(raw) == want, raw

    def test_canonical_values_pass_through(self):
        for lv in ALERT_LEVELS:
            assert normalize_alert_level(lv) == lv
            assert normalize_alert_level(lv.capitalize()) == lv

    def test_unknown_falls_back_to_info_not_dropped(self):
        """未知级别归到 info 而不是丢弃 —— 丢条目会让总数与分档对不上。"""
        assert normalize_alert_level("bogus") == "info"
        assert normalize_alert_level(None) == "info"
        assert normalize_alert_level("") == "info"

    def test_level_rank_never_raises(self):
        """原实现是硬查表 `{'critical':0,...}[level]`，遇非规范值会 KeyError。"""
        assert level_rank("critical") == 0
        assert level_rank("high") == 0
        assert level_rank("bogus") == 2

    def test_level_variants_cover_legacy_spellings(self):
        """按规范化级别过滤时，必须同时匹配历史写法。"""
        crit = level_variants("critical")
        assert "critical" in crit and "high" in crit and "High" in crit
        assert level_variants("warning").count("medium") == 1
        assert not set(level_variants("info")) & {"high", "critical"}

    def test_severity_counts_sum_to_alert_count(self):
        """分档计数之和必须等于预警总数（回归：曾出现 total>0 而三档全 0）。"""
        out = MonitorAlertAgent().rule(AgentState(query="x", results={}))
        assert sum(out["severity"].values()) == len(out["alerts"])


# ------------------------------------------------------------------ 编排器
class TestOrchestrator:
    def test_runs_all_seven_agents(self, state):
        result = Orchestrator(default_workflow(max_loops=0)).run(state)
        assert set(result.results) >= {"trend", "demand", "market", "competition",
                                       "tech", "decision", "monitor"}
        assert result.errors == []

    def test_trace_records_every_agent(self, state):
        result = Orchestrator(default_workflow(max_loops=0)).run(state)
        assert len(result.trace) >= 7
        assert all("latency_ms" in t for t in result.trace)

    def test_evidence_deduplicated_and_ref_numbered(self, state):
        result = Orchestrator(default_workflow(max_loops=0)).run(state)
        refs = [e["ref"] for e in result.state.evidence]
        assert refs == [f"E{i}" for i in range(1, len(refs) + 1)]
        assert len(set(refs)) == len(refs)

    def test_feedback_loop_when_conflicts(self, state):
        result = Orchestrator(default_workflow(max_loops=1)).run(state)
        assert result.loops >= 1

    def test_agent_failure_is_isolated(self, state, monkeypatch):
        class Boom(TrendReconAgent):
            def rule(self, s):
                raise RuntimeError("模拟 Agent 崩溃")

        wf = default_workflow(max_loops=0)
        wf.layers[0] = [Boom(), DemandDiscoveryAgent()]
        result = Orchestrator(wf).run(state)
        assert result.errors and result.errors[0]["agent"] == "trend"
        # 其他 Agent 仍应完成
        assert "decision" in result.results

    def test_selected_agents_subset(self, state):
        from app.agents.orchestrator import Workflow

        wf = Workflow(name="sub", layers=[[TrendReconAgent()]], max_loops=0)
        result = Orchestrator(wf).run(state)
        assert set(result.results) == {"trend"}


# ------------------------------------------------------------------ 工具
class TestAgentUtils:
    def test_extract_tech_keywords(self):
        kws = extract_tech_keywords("我们使用计算机视觉与深度学习做智能质检，基于 AI 大模型")
        assert "计算机视觉" in kws
        assert len(kws) <= 8

    def test_signal_view_from_orm_like(self):
        class Dummy:
            id = "d1"
            signal_type = "policy"
            source = "s"
            title = "t"
            content = "c"
            url = None
            industry = None
            region = None
            keywords = None
            entities = None
            metrics = None
            sentiment = 0.5
            published_at = None

        view = SignalView.from_orm(Dummy())
        assert view.keywords == [] and view.metrics == {}
        assert view.text == "t c"

    def test_since_filters_by_window(self):
        s = AgentState(signals=[_sig(1, "news", "新", days_ago=1),
                                _sig(2, "news", "旧", days_ago=100)], days=30)
        assert [x.id for x in s.since()] == ["sig-1"]

    def test_since_keeps_undated(self):
        s = AgentState(signals=[SignalView(id="n", signal_type="news",
                                           source="s", title="t")], days=1)
        assert len(s.since()) == 1


# ------------------------------------------------------------------ 检索串构造
class TestRetrievalQuery:
    """`state.query` 退化时不能把行业标签当检索词。

    实测教训：行业标签（如 `"AI+制造"`）在 RAGFlow / Elasticsearch 上会被
    解析成「必须同时包含 AI 与 制造」，命中数直接归零 ⇒ Agent 零证据
    ⇒ 真实大模型凭空编造。
    """

    def test_explicit_query_kept(self):
        st = AgentState(query="智能质检 缺陷检测", industry="AI+制造")
        assert build_retrieval_query(st) == "智能质检 缺陷检测"

    def test_industry_label_replaced_by_keywords(self):
        st = AgentState(
            query="AI+制造", industry="AI+制造",
            signals=[_sig(1, "policy", "方案", keywords=["智能质检", "工业互联网"]),
                     _sig(2, "funding", "融资", keywords=["计算机视觉"])],
        )
        q = build_retrieval_query(st)
        assert q == "智能质检 工业互联网 计算机视觉"
        assert "AI+制造" not in q          # 关键：行业标签不得进入检索串

    def test_empty_query_uses_keywords(self):
        st = AgentState(query="", industry="AI+制造",
                        signals=[_sig(1, "policy", "方案", keywords=["储能"])])
        assert build_retrieval_query(st) == "储能"

    def test_no_keywords_falls_back_to_sanitized_label(self):
        st = AgentState(query="AI+制造", industry="AI+制造", signals=[])
        assert build_retrieval_query(st) == "AI 制造"

    def test_last_resort_default(self):
        st = AgentState(query="", industry="", signals=[])
        assert build_retrieval_query(st) == "综合赛道分析"

    def test_signal_weight_prefers_policy(self):
        policy = _sig(1, "policy", "p")
        social = _sig(2, "social", "s")
        assert signal_weight(policy) > signal_weight(social)


# ------------------------------------------------------------------ 证据兜底
class _EmptyRAG(RAGEngine):
    """RAG 检索返回空（继承真实引擎，保证 build_context 行为与生产一致）。"""

    def retrieve(self, query, top_k=8, signal_type=None):
        return []


class TestRetrieveFallback:
    """RAG 无命中时必须回退本地信号 —— 否则大模型会因为零证据而编造事实。"""

    def test_falls_back_to_local_signals_when_rag_empty(self):
        agent = TrendReconAgent()
        st = AgentState(query="AI+制造", industry="AI+制造", rag=_EmptyRAG(),
                        signals=[_sig(1, "policy", "政策信号", keywords=["智能质检"]),
                                 _sig(2, "social", "社区吐槽", keywords=["智能质检"])])
        ctx = agent.retrieve(st)
        assert "政策信号" in ctx
        assert len(st.evidence) == 2          # 证据被登记，不再是空

    def test_fallback_ranks_by_signal_weight(self):
        """兜底证据按权重取 Top-K，policy 应排在 social 之前。"""
        agent = TrendReconAgent()
        st = AgentState(query="x", rag=_EmptyRAG(),
                        signals=[_sig(1, "social", "社区吐槽", keywords=["a"]),
                                 _sig(2, "policy", "政策文件", keywords=["a"])])
        ctx = agent.retrieve(st)
        assert ctx.index("政策文件") < ctx.index("社区吐槽")

    def test_no_signals_and_no_rag_returns_empty(self):
        agent = TrendReconAgent()
        st = AgentState(query="x", rag=_EmptyRAG(), signals=[])
        assert agent.retrieve(st) == ""
        assert st.evidence == []

    def test_rag_exception_does_not_break_analysis(self):
        class _Boom(RAGEngine):
            def retrieve(self, *a, **kw):
                raise RuntimeError("ragflow down")

        agent = TrendReconAgent()
        st = AgentState(query="x", rag=_Boom(),
                        signals=[_sig(1, "policy", "兜底信号", keywords=["储能"])])
        ctx = agent.retrieve(st)
        assert "兜底信号" in ctx


# ------------------------------------------------------------------ 契约回填
class TestContractBackfill:
    """换真实大模型后，模型不保证输出全部契约字段，必须由规则基线兜住。"""

    def test_missing_verdict_backfilled(self):
        agent = DecisionSynthesisAgent()
        out = {"total_score": 3.9}
        filled = agent.backfill_contract(out, AgentState(industry="AI+制造"))
        assert filled.get("verdict"), "verdict 缺失时必须回填"
        assert filled["_meta"]["baseline_backfill"]

    def test_explicit_empty_list_is_not_overwritten(self):
        """显式 `[]` 是模型给出的有效空结果，覆盖它等于替模型编内容。"""
        agent = DecisionSynthesisAgent()
        out = {"risk_alerts": [], "total_score": 3.9,
               "verdict": {"code": "go", "label": "建议推进", "color": "green"}}
        filled = agent.backfill_contract(out, AgentState(industry="AI+制造"))
        assert filled["risk_alerts"] == []

    def test_empty_dict_on_required_key_is_backfilled(self):
        """契约必需字段给了空对象也必须兜住。

        实测：模型对 `verdict` 返回 `{}`，而 `/opportunities/{id}/score` 会读
        `verdict.label` ⇒ 直接 KeyError 让 E2E 挂掉。
        """
        agent = DecisionSynthesisAgent()
        out = {"verdict": {}, "total_score": 3.9, "dimensions": []}
        filled = agent.backfill_contract(out, AgentState(industry="AI+制造"))
        assert filled["verdict"].get("label"), "空 verdict 必须被回填"
        assert filled["dimensions"], "空 dimensions 必须被回填"

    def test_required_keys_declared_per_agent(self):
        from app.agents.competition import CompetitionIntelAgent
        from app.agents.market import MarketSizingAgent
        assert "verdict" in DecisionSynthesisAgent.required_keys
        assert "trends" in TrendReconAgent.required_keys
        assert "TAM" in MarketSizingAgent.required_keys
        assert "threat_score" in CompetitionIntelAgent.required_keys

    def test_trend_score_derived_from_confidence(self):
        """LLM 只给 confidence；score 必须按 rule() 公式的反函数还原。"""
        agent = TrendReconAgent()
        st = AgentState(industry="AI+制造",
                        signals=[_sig(1, "policy", "p", keywords=["智能质检"])])
        out = {"trends": [{"name": "X", "description": "d", "confidence": 0.92}]}
        r = agent.postprocess(out, st)
        assert r["trends"][0]["score"] == pytest.approx((0.92 - 0.35) / 0.65, abs=1e-3)
        assert r["trends"][0]["score_source"] == "confidence_inverse"
        assert r["trends"][0]["direction"] in ("tailwind", "headwind")

    def test_trend_score_accepts_string_confidence(self):
        agent = TrendReconAgent()
        st = AgentState(industry="AI+制造")
        out = {"trends": [{"name": "Y", "confidence": "0.50"}]}
        r = agent.postprocess(out, st)
        assert isinstance(r["trends"][0]["score"], float)


# ------------------------------------------------------------------ 能力画像交叉核对
class TestTrendCapabilityCrossCheck:
    """画像必须真正参与雷达 —— 且只做「标注」，不得伪造趋势。"""

    @staticmethod
    def _state(**profile):
        return AgentState(
            industry="AI+制造", profile=profile,
            signals=[_sig(1, "policy", "数字化转型方案", content="支持智能质检与工业互联网",
                          keywords=["智能质检", "工业互联网"]),
                     _sig(2, "review", "质检软件反馈", content="识别准确率不足",
                          keywords=["智能质检"])],
        )

    def test_short_tech_stack_terms_used_verbatim(self):
        st = self._state(tech_stack=["智能体", "大模型"])
        assert capability_terms(st) == ["智能体", "大模型"]

    def test_long_patent_title_extracted_to_keywords(self):
        st = self._state(patents=["基于深度学习的表面缺陷检测方法及系统"])
        terms = capability_terms(st)
        assert "深度学习" in terms and "智能质检" not in terms

    def test_matched_capabilities_marked_on_trend(self):
        agent = TrendReconAgent()
        st = self._state(tech_stack=["智能质检"])
        out = agent.rule(st)
        hit = [t for t in out["trends"] if t["name"] == "智能质检"]
        assert hit and hit[0]["capability_match"] is True
        assert "智能质检" in hit[0]["matched_capabilities"]

    def test_unmatched_capability_becomes_gap(self):
        """画像写了「智能体」但信号里零证据 ⇒ 必须是「情报空白」而非趋势。"""
        agent = TrendReconAgent()
        st = self._state(tech_stack=["智能体", "智能质检"])
        out = agent.rule(st)
        names = [t["name"] for t in out["trends"]]
        assert "智能体" not in names, "画像不得伪造趋势"
        gaps = [g["name"] for g in out["capability_gaps"]]
        assert "智能体" in gaps
        assert all(g["evidence_count"] == 0 for g in out["capability_gaps"])

    def test_gaps_and_matches_are_disjoint(self):
        """同一能力不能既匹配某趋势、又出现在缺口里（实测踩过的自相矛盾）。"""
        agent = TrendReconAgent()
        st = self._state(tech_stack=["智能质检", "智能体", "大模型"])
        out = agent.postprocess(agent.rule(st), st)
        matched = {c for t in out["trends"] for c in (t.get("matched_capabilities") or [])}
        gaps = {g["name"] for g in out["capability_gaps"]}
        assert not (matched & gaps), f"匹配与缺口重叠: {matched & gaps}"

    def test_no_profile_no_gaps(self):
        agent = TrendReconAgent()
        out = agent.rule(AgentState(industry="AI+制造", signals=[]))
        assert out["capability_gaps"] == []


class TestPatentTermExtraction:
    """专利标题抽词（2026-09-16）。

    实测缺陷：专利标题在 `TECH_VOCAB` 上命中率极低 ——
    「小样本工业缺陷识别模型训练方法」命中 **0** 个词，原实现退化成
    「整句当能力词」⇒ 与全库 36 个趋势标签匹配 **0** 次，**专利实际贡献恒为 0**。

    修法：按中文技术名词的构词法抽「技术对象 + 动作后缀」，且**不丢弃**专利
    （抽不到时仍退回原文，保证它出现在「本次分析依据」里可被核查）。
    """

    def test_extracts_object_plus_action(self):
        terms = extract_patent_terms("小样本工业缺陷识别模型训练方法")
        assert "工业缺陷识别" in terms and "模型训练" in terms
        assert not any(len(t) > 12 for t in terms), "不应把整句当能力词"

    def test_vocab_hits_take_precedence(self):
        terms = extract_patent_terms("基于深度学习的表面缺陷检测方法")
        assert "深度学习" in terms and "表面缺陷检测" in terms

    def test_does_not_span_word_boundary(self):
        """「视觉引导机器人抓取定位」不得抽出跨词的「器人抓取定位」。"""
        terms = extract_patent_terms("面向工业场景的视觉引导机器人抓取定位方法")
        assert "抓取定位" in terms
        assert not any(t.startswith("器人") for t in terms)
        assert not any("别模型" in t for t in terms)

    def test_strips_boilerplate(self):
        """套话（一种/基于/面向/方法/系统/装置）不该留在词里。"""
        for term in extract_patent_terms("一种面向多设备协同的工厂物流调度系统"):
            assert not term.startswith(("一种", "基于", "面向"))
            assert not term.endswith(("的方法", "的系统", "装置"))

    def test_empty_and_noise_only_inputs(self):
        assert extract_patent_terms("") == []
        assert extract_patent_terms("一种方法") == []

    def test_capability_terms_reports_source(self):
        """按来源展开：要能回答「这个能力词是从哪条专利抽出来的」。"""
        st = AgentState(query="", profile={
            "tech_stack": ["计算机视觉"],
            "patents": ["小样本工业缺陷识别模型训练方法"],
        })
        by = capability_terms_by_source(st)
        assert [r["raw"] for r in by["tech_stack"]] == ["计算机视觉"]
        patent_rows = by["patents"]
        assert len(patent_rows) == 1
        assert patent_rows[0]["raw"] == "小样本工业缺陷识别模型训练方法"
        assert "工业缺陷识别" in patent_rows[0]["terms"]

    def test_patent_terms_flow_into_flat_terms(self):
        """专利贡献的词必须进入扁平能力词列表（供匹配使用）。"""
        st = AgentState(query="", profile={
            "tech_stack": ["计算机视觉"],
            "patents": ["小样本工业缺陷识别模型训练方法"],
        })
        assert capability_terms(st) == ["计算机视觉", "工业缺陷识别", "模型训练"]

    def test_falls_back_to_raw_when_nothing_extracted(self):
        """抽不到词也不丢弃 —— 退回原文，保证专利在分析依据里可见。"""
        st = AgentState(query="", profile={
            "patents": ["一种完全不含动作后缀的技术方案描述用语"],
        })
        terms = capability_terms(st)
        assert terms, "专利不应被静默丢弃"


# ------------------------------------------------------------------ 上游类型护栏
class TestUpstreamTypeGuards:
    """上游是大模型的输出，**不保证类型** —— 类型错了不能让规则基线崩掉。

    实测：模型把 `coverage` / `unit_economics` 返回成字符串、把 `pain_points`
    返回成字符串数组，`decision.rule()` 抛 `'str' object has no attribute 'get'`，
    使契约回填整条失效 ⇒ `verdict` 丢失 ⇒ `/score` 的 `verdict.label` KeyError。
    """

    @staticmethod
    def _state(**results):
        return AgentState(industry="AI+制造", results=results)

    def test_rule_survives_string_where_dict_expected(self):
        agent = DecisionSynthesisAgent()
        out = agent.rule(self._state(
            market={"TAM": 5e8, "growth_rate": 0.3,
                    "unit_economics": "没算出来"},      # ← 本应是对象
            demand={"demand_score_5": 4, "coverage": "高",   # ← 本应是对象
                    "pain_points": ["痛点A", "痛点B"]},       # ← 本应是对象数组
        ))
        assert out["verdict"]["label"]
        assert len(out["dimensions"]) == 7

    def test_rule_survives_string_dimensions(self):
        agent = DecisionSynthesisAgent()
        out = agent.rule(self._state(
            tech={"overall_5": 4, "dimensions": "六维"},        # ← 本应是数组
            trend={"trends": ["趋势A"]},                        # ← 本应是对象数组
            competition={"barrier_score_5": 3, "evidence_refs": "E1"},
        ))
        assert out["verdict"]["label"]

    def test_backfill_runs_even_when_upstream_is_garbage(self):
        """上游全垃圾时，回填仍必须成功 —— 这是 E2E 不挂的关键。"""
        agent = DecisionSynthesisAgent()
        st = self._state(market="不是对象", demand=[], tech=None, trend=0,
                         competition="x")
        out = {"dimensions": [{"name": "市场规模", "score": 4.0}]}   # 缺 verdict
        filled = agent.backfill_contract(out, st)
        assert filled["verdict"].get("label")

    def test_type_helpers(self):
        from app.agents.base import as_dict, as_list, dicts_only
        assert as_dict("x") == {} and as_dict(None) == {}
        assert as_dict({"a": 1}) == {"a": 1}
        assert as_list("x") == [] and as_list([1]) == [1]
        assert dicts_only(["a", {"b": 1}, 2]) == [{"b": 1}]


# ------------------------------------------------------------------ 评分自洽性
class TestDecisionScoreConsistency:
    """评分是「维度 + 配置权重」的确定性函数，不采信模型自报的总分。

    技术文档 §4.4/§4.5 的不变量：`total_score == Σ 各维贡献`。
    实测模型会自报一个与自身维度对不上的总分，破坏该不变量。
    """

    @staticmethod
    def _dims(score: float = 3.0):
        return [{"key": d["key"], "name": d["name"], "score": score,
                 "weight": 0.99}          # ← 权重故意给错，应被配置值覆盖
                for d in DEFAULT_DIMENSIONS]

    def test_self_reported_total_is_discarded(self):
        agent = DecisionSynthesisAgent()
        out = {"dimensions": self._dims(3.0), "total_score": 9.9, "explained": {}}
        r = agent.postprocess(out, AgentState(industry="AI+制造"))
        assert r["total_score"] == pytest.approx(3.0, abs=0.01)

    def test_contributions_sum_to_total(self):
        agent = DecisionSynthesisAgent()
        out = {"dimensions": self._dims(3.0), "total_score": 9.9}
        r = agent.postprocess(out, AgentState(industry="AI+制造"))
        contrib = sum(c["contribution"] for c in r["explained"]["dimensions"])
        assert contrib == pytest.approx(r["total_score"], abs=1e-6)

    def test_weights_taken_from_scorecard_not_model(self):
        agent = DecisionSynthesisAgent()
        out = {"dimensions": self._dims(3.0)}
        r = agent.postprocess(out, AgentState(industry="AI+制造"))
        assert [d["weight"] for d in r["dimensions"]] == \
            [d["weight"] for d in DEFAULT_DIMENSIONS]

    def test_verdict_matches_recomputed_score(self):
        agent = DecisionSynthesisAgent()
        out = {"dimensions": self._dims(5.0), "total_score": 1.0}
        r = agent.postprocess(out, AgentState(industry="AI+制造"))
        assert r["verdict"] == agent._verdict(5.0)

    def test_incomplete_dimensions_left_to_baseline(self):
        """维度不齐时不接管 —— 交给 backfill_contract 用规则基线兜底。"""
        agent = DecisionSynthesisAgent()
        out = {"dimensions": [{"key": "market_size", "name": "市场空间", "score": 4.0}],
               "total_score": 4.0}
        r = agent.postprocess(out, AgentState(industry="AI+制造"))
        assert len(r["dimensions"]) == 1


# ---------------------------------------------------------- 证据链编号反查
class TestEvidenceRefResolution:
    """模型只回 `E1`/`E3` 这类编号，必须反查成**真实证据**再落库。

    实测缺陷（2026-09-14）：`sync_analysis` 与 `persist_scan_result` 两处都是
    `existing.evidence = t.get("evidence")`，直接存模型原始输出
    ⇒ `trends.evidence` 里全是 `["E1","E3","E4"]` 字符串
    ⇒ 前端 `Trends.tsx` 取 `e.title` / `e.source` / `e.url` 全部落空
    ⇒ 证据链渲染成一排 `—`（40/40 条趋势均如此）。
    """

    @staticmethod
    def _agent_state(n: int = 3):
        """构造一个已检索过的 trend agent（`_retrieved_docs` 已被填充）。"""
        agent = TrendReconAgent()
        st = AgentState(
            query="AI+制造", industry="AI+制造", rag=_EmptyRAG(),
            signals=[_sig(i, "policy", f"政策{i}", keywords=["智能质检"])
                     for i in range(1, n + 1)],
        )
        agent.retrieve(st)
        return agent, st

    def test_maps_ref_to_real_evidence(self):
        """`["E1","E3"]` → 真实证据，顺序保持，且带 title/source/url。"""
        agent, _ = self._agent_state(3)
        out = agent.resolve_evidence_refs(["E1", "E3"])
        assert [e["signal_id"] for e in out] == ["sig-1", "sig-3"]
        assert all(e["title"] and e["source"] for e in out)

    def test_lowercase_and_whitespace_tolerated(self):
        """模型可能回 `e1` / `" E2 "` —— 归一化后仍要能命中。"""
        agent, _ = self._agent_state(3)
        out = agent.resolve_evidence_refs(["e1", " E2 "])
        assert [e["signal_id"] for e in out] == ["sig-1", "sig-2"]

    def test_drops_unknown_ref(self):
        """模型可能编出不存在的编号 —— 丢弃，绝不让前端渲染空条目。"""
        agent, _ = self._agent_state(2)
        out = agent.resolve_evidence_refs(["E1", "E99", "E0", "E-1", "不是编号"])
        assert [e["signal_id"] for e in out] == ["sig-1"]

    def test_dict_items_passed_through(self):
        """规则实现本就产出含 signal_id 的 dict —— 必须原样透传。"""
        agent, _ = self._agent_state(2)
        full = {"signal_id": "sig-1", "title": "政策1", "source": "src-policy"}
        mixed = agent.resolve_evidence_refs([full, "E2"])
        assert mixed[0] is full or mixed[0] == full
        assert mixed[1]["signal_id"] == "sig-2"

    def test_empty_and_none_input(self):
        agent, _ = self._agent_state(2)
        assert agent.resolve_evidence_refs(None) == []
        assert agent.resolve_evidence_refs([]) == []

    def test_no_retrieval_yields_empty(self):
        """从未检索过（无 `_retrieved_docs`）时不能抛错。"""
        assert TrendReconAgent().resolve_evidence_refs(["E1"]) == []

    def test_postprocess_resolves_trend_evidence(self):
        """端到端：模型输出带编号 → `postprocess` 后 trends[].evidence 是 dict。"""
        agent, st = self._agent_state(3)
        out = agent.postprocess(
            {"trends": [{"name": "智能质检趋势", "score": 0.8,
                         "confidence": 0.9, "direction": "tailwind",
                         "evidence": ["E1", "E2"]}]},
            st,
        )
        ev = out["trends"][0]["evidence"]
        assert [e["signal_id"] for e in ev] == ["sig-1", "sig-2"]
        assert out["trends"][0]["signal_count"] == 2

    def test_postprocess_keeps_rule_path_intact(self):
        """规则路径的 dict 证据在 `postprocess` 里不被破坏。"""
        agent = TrendReconAgent()
        st = AgentState(query="AI+制造", industry="AI+制造", rag=_EmptyRAG(),
                        signals=[_sig(1, "policy", "政策1", keywords=["智能质检"])])
        rule_out = agent.rule(st)
        assert isinstance(rule_out["trends"][0]["evidence"][0], dict)
        out = agent.postprocess(dict(rule_out), st)
        assert isinstance(out["trends"][0]["evidence"][0], dict)
        assert out["trends"][0]["evidence"][0].get("signal_id")

    def test_retrieved_docs_not_polluted_across_runs(self):
        """`_retrieved_docs` 每次 `retrieve` 重写，不跨调用残留。"""
        agent, st = self._agent_state(3)
        assert len(agent._retrieved_docs) == 3
        st2 = AgentState(query="x", rag=_EmptyRAG(), signals=[])
        assert agent.retrieve(st2) == ""
        assert agent._retrieved_docs == []


# ---------------------------------------------------- 需求探针契约（白屏缺陷）
class TestDemandPainPointContract:
    """`pain_points[].experiments` 必须补上。

    实测缺陷（2026-09-14）：LLM 路径的 prompt **没有要求**模型输出 `experiments`，
    postprocess 也只 `setdefault("samples", [])` —— 而前端 `Demand.tsx:194`
    直接 `p.experiments.map(...)` 渲染 ⇒ 整页白屏：
    `Cannot read properties of undefined (reading 'map')`。
    """

    def test_experiments_backfilled_from_table(self):
        agent = DemandDiscoveryAgent()
        out = agent.postprocess(
            {"pain_points": [{"name": "检测精度低", "freq": 3, "demand_score": 4.2}]},
            AgentState(query="x"))
        assert out["pain_points"][0]["experiments"] == ["落地页测试", "客户访谈", "POC"]

    def test_experiments_default_when_name_unknown(self):
        agent = DemandDiscoveryAgent()
        out = agent.postprocess(
            {"pain_points": [{"name": "一个没见过的痛点"}]}, AgentState(query="x"))
        assert out["pain_points"][0]["experiments"] == ["客户访谈", "问卷调研"]

    def test_model_supplied_experiments_respected(self):
        """模型真给了实验建议时要尊重它，不能被兜底覆盖。"""
        agent = DemandDiscoveryAgent()
        out = agent.postprocess(
            {"pain_points": [{"name": "检测精度低", "experiments": ["A/B 测试"]}]},
            AgentState(query="x"))
        assert out["pain_points"][0]["experiments"] == ["A/B 测试"]

    def test_non_list_experiments_coerced_to_list(self):
        """模型可能把数组返回成字符串 —— 前端 `.map()` 同样会崩。"""
        agent = DemandDiscoveryAgent()
        out = agent.postprocess(
            {"pain_points": [{"name": "检测精度低", "experiments": "落地页测试"}]},
            AgentState(query="x"))
        assert isinstance(out["pain_points"][0]["experiments"], list)
        assert out["pain_points"][0]["experiments"]

    def test_rule_path_also_has_experiments(self):
        """规则路径本来就有 experiments，不能被破坏。"""
        agent = DemandDiscoveryAgent()
        st = AgentState(query="AI+制造", industry="AI+制造", rag=_EmptyRAG(),
                        signals=[_sig(1, "social", "识别准确率不足", keywords=["智能质检"])])
        out = agent.rule(st)
        assert all(isinstance(p.get("experiments"), list) for p in out["pain_points"])


# ---------------------------------------------------- 能力匹配比对池
class TestCapabilityMatchPool:
    """比对池必须包含**证据信号的 keywords**。

    实测漏项根因（2026-09-14）：画像写「计算机视觉」，LLM 生成的趋势名写
    「工业视觉」「智能质检」—— 严格子串匹配不上；而证据信号的关键词里
    **明确就有「计算机视觉」**。原实现只取趋势自身字段，且 LLM 路径的
    prompt 不要求输出 keywords ⇒ 池子里根本没有这个词
    ⇒ 7 条应匹配的趋势全部漏判。
    """

    def _state(self):
        return AgentState(
            query="AI+制造", industry="AI+制造", rag=_EmptyRAG(),
            profile={"tech_stack": ["计算机视觉"]},
            signals=[_sig(1, "policy", "质检政策", keywords=["计算机视觉"])],
        )

    def test_evidence_signal_keywords_enter_pool(self):
        agent = TrendReconAgent()
        st = self._state()
        agent.retrieve(st)                      # 建立 _retrieved_docs
        out = agent.postprocess(
            {"trends": [{"name": "工业视觉规模化落地", "score": 0.8,
                         "confidence": 0.9, "direction": "tailwind",
                         "evidence": ["E1"]}]}, st)
        t = out["trends"][0]
        assert t["matched_capabilities"] == ["计算机视觉"]
        assert t["capability_match"] is True

    def test_name_without_keyword_still_matches(self):
        """趋势名直接含能力词时（原有行为）仍要匹配。"""
        agent = TrendReconAgent()
        st = self._state()
        agent.retrieve(st)
        out = agent.postprocess(
            {"trends": [{"name": "计算机视觉平台化", "score": 0.8,
                         "confidence": 0.9, "direction": "tailwind",
                         "evidence": []}]}, st)
        assert out["trends"][0]["matched_capabilities"] == ["计算机视觉"]

    def test_no_evidence_no_false_match(self):
        """既无证据也无同名词时**不能**造出匹配。"""
        agent = TrendReconAgent()
        st = self._state()
        agent.retrieve(st)
        out = agent.postprocess(
            {"trends": [{"name": "储能规模化交付", "score": 0.7,
                         "confidence": 0.8, "direction": "tailwind",
                         "evidence": []}]}, st)
        assert out["trends"][0]["matched_capabilities"] == []
        assert out["trends"][0]["capability_match"] is False

    def test_gap_does_not_contain_matched_word(self):
        """自洽约束：匹配上的能力词不能同时出现在缺口里。"""
        agent = TrendReconAgent()
        st = self._state()
        agent.retrieve(st)
        out = agent.postprocess(
            {"trends": [{"name": "工业视觉规模化落地", "score": 0.8,
                         "confidence": 0.9, "direction": "tailwind",
                         "evidence": ["E1"]}]}, st)
        gaps = {g["name"] for g in out["capability_gaps"]}
        assert "计算机视觉" not in gaps


# ---------------------------------------------------- 趋势标签 × 公司能力（解耦）
class TestTrendTagsDecoupled:
    """趋势只存**客观标签**，匹配由接口层按「当前公司」实时算。

    背景（2026-09-15 重构）：原实现把 `matched` 在落库那一刻冻结进
    `factors._capability`，却没记录是给哪家公司算的 ⇒
    ① 切换公司后标注失效；② 每次改匹配逻辑都要回算全库历史数据。
    """

    def _state_with(self, profile):
        agent = TrendReconAgent()
        st = AgentState(
            query="AI+制造", industry="AI+制造", rag=_EmptyRAG(),
            profile=profile,
            signals=[_sig(1, "policy", "质检政策", keywords=["计算机视觉", "智能质检"])],
        )
        agent.retrieve(st)
        return agent, st

    def test_tags_come_from_evidence_signals(self):
        """趋势标签取自证据链信号的关键词（客观事实）。"""
        agent, st = self._state_with({"tech_stack": ["计算机视觉"]})
        out = agent.postprocess(
            {"trends": [{"name": "工业视觉规模化落地", "score": 0.8,
                         "confidence": 0.9, "direction": "tailwind",
                         "evidence": ["E1"]}]}, st)
        tags = out["trends"][0]["factors"]["_tags"]
        assert "计算机视觉" in tags and "智能质检" in tags

    def test_same_trend_different_company_differs(self):
        """同一趋势标签 × 不同公司能力词 ⇒ 匹配结果不同（核心诉求）。"""
        trend = {"name": "工业视觉与大模型质检", "score": 0.8, "confidence": 0.9,
                 "direction": "tailwind", "evidence": ["E1"]}

        a_agent, a_st = self._state_with({"tech_stack": ["计算机视觉", "大模型"]})
        a_out = a_agent.postprocess({"trends": [dict(trend)]}, a_st)

        b_agent, b_st = self._state_with({"tech_stack": ["深度学习"]})
        b_out = b_agent.postprocess({"trends": [dict(trend)]}, b_st)

        # 标签必须一致（客观属性，与公司无关）
        assert a_out["trends"][0]["factors"]["_tags"] == \
            b_out["trends"][0]["factors"]["_tags"]
        # 但匹配结果随公司变化
        assert "计算机视觉" in a_out["trends"][0]["matched_capabilities"]
        assert "计算机视觉" not in b_out["trends"][0]["matched_capabilities"]

    def test_files_no_capability_snapshot(self):
        """`factors` 里不应再写 `_capability` 快照（已改为实时算）。"""
        agent, st = self._state_with({"tech_stack": ["计算机视觉"]})
        out = agent.postprocess(
            {"trends": [{"name": "质检趋势", "score": 0.7, "confidence": 0.8,
                         "direction": "tailwind", "evidence": ["E1"]}]}, st)
        assert "_capability" not in out["trends"][0]["factors"]
        assert "_tags" in out["trends"][0]["factors"]


class TestTechKeywordExtraction:
    """抽词不能把「证据编号」当技术词（实测缺陷 2026-09-15）。"""

    def test_code_refs_are_not_keywords(self):
        """LLM 描述里会引用证据编号（如「政策补贴（E3, E4）…」）。"""
        text = ("结合政策补贴（E3, E4）与产业基金重点投向（E1），"
                "工业视觉技术将走向大规模部署，智能质检应用将成爆发点。")
        kws = extract_tech_keywords(text, limit=8)
        assert "E1" not in kws and "E3" not in kws and "E4" not in kws

    def test_real_ascii_tech_words_kept(self):
        kws = extract_tech_keywords("基于 YOLO 与 TensorFlow 的缺陷检测", limit=8)
        assert "YOLO" in kws and "TensorFlow" in kws

    def test_alias_mapping_still_works(self):
        kws = extract_tech_keywords("面向 6G 与 AI 的智能体平台", limit=8)
        assert "6G" in kws
        assert "人工智能" in kws          # AI → 别名
        assert "智能体" in kws

    def test_no_fragment_fallback(self):
        """长标题不能再切成固定窗口碎片（原 `[\\u4e00-\\u9fff]{2,4}` 兜底）。"""
        kws = extract_tech_keywords("面向小样本的工业缺陷识别模型训练方法", limit=6)
        assert not any(x in kws for x in ("小样本工", "业缺陷识", "别模型训"))

    # ---- 英文噪声过滤（实测缺陷 2026-09-15）----
    # 论文类信号的正文是英文摘要，朴素抽取会把普通词收进 keywords，
    # 再经 trend_tags 传染到趋势标签（实测 tags 出现 'Military Technology and Strategies'）。

    def test_english_prose_not_extracted(self):
        text = ("作者：Zhang Wei\n发表：Repository of the University of Namur\n"
                "主题：Military Technology and Strategies、Legal and Regulatory Analysis\n"
                "Deep learning based image processing using OpenCV and TensorFlow")
        kws = extract_tech_keywords(text, limit=10)
        for noise in ("Military", "Technology", "Strategies", "Rough", "Zhang",
                      "of", "and", "in", "based", "using", "Repository"):
            assert noise not in kws, f"普通英文词 {noise!r} 不应成为关键词"
        assert "OpenCV" in kws and "TensorFlow" in kws, "真技术词必须保留"

    def test_url_fragments_filtered(self):
        kws = extract_tech_keywords("原文链接 https://36kr.com/p/123 详见 kr.com", limit=8)
        assert not any(x in kws for x in ("https", "kr.com", "com", "p"))

    def test_business_terms_filtered(self):
        """职位 / 融资轮次常出现在新闻正文，但不是技术词。"""
        kws = extract_tech_keywords("该公司 CEO 与 CTO 表示完成 Pre-A 轮融资", limit=8)
        assert not any(x in kws for x in ("CEO", "CTO", "Pre-A"))

    def test_model_names_and_acronyms_kept(self):
        kws = extract_tech_keywords(
            "GS-MGF-LPLs 模型在 BERT-base 上微调，推理走 GPU，协议支持 6G", limit=8)
        assert "GS-MGF-LPLs" in kws      # 含数字的型号
        assert "GPU" in kws              # 全大写缩写
        assert "6G" in kws


class TestSuggestedExperimentContract:
    """`suggested_experiments` 必须始终是**字符串列表**。

    模型经常返回结构化对象（`hypothesis` / `target_pain_point` / `metric`），
    而前端把它当 React child 渲染 —— React 会抛
    `Objects are not valid as a React child` 并**卸载整棵树**，
    表现为**整页白屏**（实测：需求探针点「开始分析」后页面全空，
    且只留下 `document.body.innerText === ''`，极易被误判成「接口没返回」）。
    """

    def _state(self):
        return AgentState(query="质检", signals=[])

    def test_structured_items_normalized_to_text(self):
        out = DemandDiscoveryAgent().postprocess(
            {
                "pain_points": [{"name": "整体体验不足"}],
                "suggested_experiments": [
                    {"hypothesis": "假设A", "target_pain_point": "P", "metric": "M"},
                    "字符串项",
                ],
            },
            self._state(),
        )
        se = out["suggested_experiments"]
        assert all(isinstance(x, str) for x in se)
        assert se[0] == "假设A"          # 优先取 hypothesis（前端要拿它预填实验假设）
        assert "字符串项" in se

    def test_unknown_dict_falls_back_to_joined_values(self):
        out = DemandDiscoveryAgent().postprocess(
            {"pain_points": [], "suggested_experiments": [{"a": "甲", "b": "乙"}]},
            self._state(),
        )
        assert out["suggested_experiments"] == ["甲；乙"]

    def test_missing_field_falls_back_like_rule_path(self):
        out = DemandDiscoveryAgent().postprocess(
            {"pain_points": [{"name": "整体体验不足", "experiments": ["客户访谈"]}]},
            self._state(),
        )
        assert out["suggested_experiments"] == ["客户访谈"]

    def test_empty_everything_gives_defaults(self):
        out = DemandDiscoveryAgent().postprocess({"pain_points": []}, self._state())
        se = out["suggested_experiments"]
        assert se and all(isinstance(x, str) for x in se)

    def test_dedup_and_cap(self):
        out = DemandDiscoveryAgent().postprocess(
            {"pain_points": [], "suggested_experiments": ["A", "A", "B", "C", "D", "E", "F"]},
            self._state(),
        )
        assert out["suggested_experiments"] == ["A", "B", "C", "D", "E"]
