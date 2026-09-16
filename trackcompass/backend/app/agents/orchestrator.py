"""Agent 编排器（技术文档 §5.1）。

采用 LangGraph 的有向图语义：
  [趋势侦察] → [需求挖掘] → [市场测算] → [竞争情报] → [技术评估] → [决策综合] → [监控预警]
       ↑                                                                        ↓
       └──────────────────────────── 反馈循环 ──────────────────────────────────┘

本地以「拓扑执行 + 并行分组 + 条件分支 + 有界反馈」实现同等语义：
  · 第一层（趋势、需求）并行 —— 互不依赖
  · 第二层（市场、竞争、技术）并行 —— 依赖上一层结论但彼此独立
  · 第三层（决策综合）
  · 第四层（监控预警）
  · 若决策综合得到的 verdict=verify 且仍在循环预算内 → 携带冲突点回灌第一层重跑
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from app.agents.base import AgentState, BaseAgent
from app.agents.competition import CompetitionIntelAgent
from app.agents.decision import DecisionSynthesisAgent
from app.agents.demand import DemandDiscoveryAgent
from app.agents.market import MarketSizingAgent
from app.agents.monitor import MonitorAlertAgent
from app.agents.tech import TechAssessmentAgent
from app.agents.trend import TrendReconAgent

logger = logging.getLogger(__name__)


@dataclass
class Workflow:
    """编排图定义。"""

    name: str
    layers: list[list[BaseAgent]]
    max_loops: int = 1
    parallel: bool = True

    def all_agents(self) -> list[BaseAgent]:
        return [a for layer in self.layers for a in layer]


@dataclass
class RunResult:
    state: AgentState
    trace: list[dict[str, Any]] = field(default_factory=list)
    loops: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def results(self) -> dict[str, Any]:
        return self.state.results


def default_workflow(max_loops: int = 1) -> Workflow:
    return Workflow(
        name="trackcompass-full-analysis",
        layers=[
            [TrendReconAgent(), DemandDiscoveryAgent()],
            [MarketSizingAgent(), CompetitionIntelAgent(), TechAssessmentAgent()],
            [DecisionSynthesisAgent()],
            [MonitorAlertAgent()],
        ],
        max_loops=max_loops,
    )


class Orchestrator:
    def __init__(self, workflow: Workflow | None = None,
                 on_agent_done: Callable[[dict[str, Any]], None] | None = None):
        self.workflow = workflow or default_workflow()
        self.on_agent_done = on_agent_done

    # ------------------------------------------------------------ 执行
    def run(self, state: AgentState) -> RunResult:
        result = RunResult(state=state)
        loop = 0

        while True:
            for layer in self.workflow.layers:
                self._run_layer(layer, state, result)
            loop += 1

            decision = state.results.get("decision", {}) or {}
            verdict = (decision.get("verdict") or {}).get("code")
            conflicts = decision.get("conflicts") or []
            should_loop = (
                verdict in ("verify", "watch")
                and conflicts
                and loop <= self.workflow.max_loops
            )
            if not should_loop:
                break

            # 反馈循环：把冲突点写入 query，驱动趋势与需求 Agent 重跑
            logger.info("触发反馈循环 #%d，冲突点=%s", loop, conflicts)
            state.query = f"{state.query} | 聚焦冲突: {'；'.join(conflicts)}"
            state.results["_feedback"] = conflicts

        result.loops = loop
        # 统一证据链去重，保证 ref 连续
        state.evidence = self._dedupe_evidence(state.evidence)
        result.trace = list(state.trace)
        return result

    def _run_layer(self, layer: list[BaseAgent], state: AgentState,
                   result: RunResult) -> None:
        if self.workflow.parallel and len(layer) > 1:
            with ThreadPoolExecutor(max_workers=len(layer)) as pool:
                futures = {pool.submit(self._safe_run, agent, state): agent for agent in layer}
                for future in futures:
                    agent = futures[future]
                    try:
                        output = future.result()
                        if self.on_agent_done:
                            self.on_agent_done(output)
                    except Exception as exc:  # pragma: no cover
                        logger.exception("Agent %s 执行失败", agent.key)
                        result.errors.append({"agent": agent.key, "error": str(exc)})
        else:
            for agent in layer:
                try:
                    output = self._safe_run(agent, state)
                    if self.on_agent_done:
                        self.on_agent_done(output)
                except Exception as exc:  # pragma: no cover
                    logger.exception("Agent %s 执行失败", agent.key)
                    result.errors.append({"agent": agent.key, "error": str(exc)})

    @staticmethod
    def _safe_run(agent: BaseAgent, state: AgentState) -> dict[str, Any]:
        return agent.run(state)

    @staticmethod
    def _dedupe_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for item in evidence:
            key = f"{item.get('signal_id')}|{item.get('excerpt', '')[:40]}"
            if key in seen:
                continue
            seen.add(key)
            item = dict(item)
            item["ref"] = f"E{len(out) + 1}"
            out.append(item)
        return out


def analyze(
    signals: Sequence[Any],
    *,
    query: str = "",
    industry: str | None = None,
    region: str | None = None,
    days: int = 90,
    profile: dict[str, Any] | None = None,
    rag=None,
    max_loops: int = 1,
    agents: list[BaseAgent] | None = None,
) -> RunResult:
    """便捷入口：给定信号与画像，跑完整条流水线。"""
    from app.agents.base import SignalView

    views = [s if isinstance(s, SignalView) else SignalView.from_orm(s) for s in signals]
    state = AgentState(
        query=query or industry or "综合赛道分析",
        industry=industry, region=region, days=days,
        profile=profile or {}, signals=views, rag=rag,
    )
    if agents:
        workflow = Workflow(
            name="custom",
            layers=[[a] for a in agents],
            max_loops=max_loops,
        )
        return Orchestrator(workflow).run(state)
    return Orchestrator(default_workflow(max_loops)).run(state)
