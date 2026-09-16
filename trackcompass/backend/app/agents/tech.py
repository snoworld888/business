"""技术评估 Agent（技术文档 §5.3.5）。

输入：技术文档、专利、招聘、产业侧信号
工具：trl_assessment / crl_assessment
输出：TRL / CRL / BRL / IPRL / TMRL / FRL 六维就绪度（1–9 量纲）
"""

from __future__ import annotations

from typing import Any

from app.agents.base import TRL_HINTS, AgentState, BaseAgent, SignalView, hit_any
from app.services.llm import COMPLEXITY_COMPLEX
from app.services.scoring import DEFAULT_DIMENSIONS, six_dim_assessment


class TechAssessmentAgent(BaseAgent):
    key = "tech"
    name = "技术评估 Agent"
    description = "基于技术文档与专利信号，输出 TRL/CRL/BRL/IPRL/TMRL/FRL 六维评分。"
    complexity = COMPLEXITY_COMPLEX
    #: 契约字段：决策卡「技术成熟度」维度读 dimensions/overall
    required_keys = ("dimensions", "overall")

    def system_prompt(self) -> str:
        return (
            "你是技术成熟度评估专家。输出 JSON：{\"TRL\":7,\"CRL\":6,\"BRL\":5,"
            "\"IPRL\":5,\"TMRL\":6,\"FRL\":5,\"evidence\":{}}，量纲 1-9。"
        )

    def rule(self, state: AgentState) -> dict[str, Any]:
        signals = state.since()
        tech_sigs = [s for s in signals if s.signal_type in ("paper", "patent", "news")]
        patent_sigs = [s for s in signals if s.signal_type == "patent"]
        policy_sigs = [s for s in signals if s.signal_type == "policy"]
        recruit_sigs = [s for s in signals if s.signal_type == "recruit"]
        funding_sigs = [s for s in signals if s.signal_type == "funding"]
        demand_sigs = [s for s in signals
                       if s.signal_type in ("social", "review", "bidding", "forum")]

        text = " ".join(s.text for s in tech_sigs)
        trl = self._trl_from_text(text)

        crl = self._lerp(3.0, 9.0, [
            (len(demand_sigs) / 15, 0.4),
            (len(policy_sigs) / 6, 0.3),
            (self._pilot_ratio(signals), 0.3),
        ])
        brl = self._lerp(3.0, 9.0, [
            (len(funding_sigs) / 6, 0.45),
            (self._scale_hint(text), 0.35),
            (len(policy_sigs) / 8, 0.20),
        ])
        iprl = self._lerp(2.5, 9.0, [
            (len(patent_sigs) / 10, 0.7),
            (1.0 if any("标准" in s.text for s in signals) else 0.0, 0.3),
        ])
        tmrl = self._lerp(3.0, 9.0, [
            (len(recruit_sigs) / 8, 0.45),
            (self._team_hint(state), 0.55),
        ])
        frl = self._lerp(3.0, 9.0, [
            (len(funding_sigs) / 5, 0.6),
            ((state.profile.get("funding") or {}).get("amount", 0) / 1e8
             if state.profile else 0.0, 0.4),
        ])

        scores = {"TRL": trl, "CRL": crl, "BRL": brl, "IPRL": iprl, "TMRL": tmrl, "FRL": frl}
        evidence = {
            "TRL": [s.id for s in tech_sigs[:3]],
            "CRL": [s.id for s in demand_sigs[:2]],
            "BRL": [s.id for s in funding_sigs[:2]],
            "IPRL": [s.id for s in patent_sigs[:3]],
            "TMRL": [s.id for s in recruit_sigs[:2]],
            "FRL": [s.id for s in funding_sigs[:2]],
        }
        assessment = six_dim_assessment(scores, evidence)
        assessment["signals_used"] = {
            "tech": len(tech_sigs), "patent": len(patent_sigs),
            "policy": len(policy_sigs), "recruit": len(recruit_sigs),
            "funding": len(funding_sigs), "demand": len(demand_sigs),
        }
        return assessment

    # ------------------------------------------------------------ 子算法
    @staticmethod
    def _trl_from_text(text: str) -> float:
        level = 3.0
        for value, hints in TRL_HINTS:
            if hit_any(text, hints):
                level = max(level, float(value))
        return level

    @staticmethod
    def _lerp(lo: float, hi: float, parts: list[tuple[float, float]]) -> float:
        """按权重把多个 [0,1] 归一量映射到 [lo, hi]。"""
        total_weight = sum(w for _, w in parts) or 1.0
        ratio = sum(min(1.0, max(0.0, v)) * w for v, w in parts) / total_weight
        return round(lo + (hi - lo) * ratio, 2)

    @staticmethod
    def _pilot_ratio(signals: list[SignalView]) -> float:
        hints = ["试点", "示范", "中试", "批量", "上线"]
        hits = sum(1 for s in signals if hit_any(s.text, hints))
        return hits / max(1, len(signals))

    @staticmethod
    def _scale_hint(text: str) -> float:
        for hint, value in (("规模化", 1.0), ("量产", 0.85), ("批量", 0.7),
                            ("小批", 0.45), ("样机", 0.3), ("原型", 0.15)):
            if hint in text:
                return value
        return 0.35

    @staticmethod
    def _team_hint(state: AgentState) -> float:
        talents = (state.profile or {}).get("talents") or {}
        if not talents:
            return 0.4
        total = sum(v for v in talents.values() if isinstance(v, (int, float)))
        tech = sum(v for k, v in talents.items()
                   if isinstance(v, (int, float)) and any(h in k for h in ("算法", "研发", "工程", "技术")))
        return min(1.0, (tech / total if total else 0) + 0.2)

    def build_prompt(self, state: AgentState, context: str) -> str:
        return (
            f"行业={state.industry}，公司能力画像={state.profile}\n"
            f"技术侧信号：\n{context}\n\n"
            "输出六维就绪度 JSON（TRL/CRL/BRL/IPRL/TMRL/FRL，1-9）。"
        )

    def postprocess(self, output: dict[str, Any], state: AgentState) -> dict[str, Any]:
        if "dimensions" not in output:
            scores = {k: float(output.get(k, 3.0)) for k in
                      ("TRL", "CRL", "BRL", "IPRL", "TMRL", "FRL")}
            merged = six_dim_assessment(scores, output.get("evidence") or {})
            merged["_meta"] = output.get("_meta", {})
            return merged
        return output

    # 供评分卡复用：技术成熟度 → 1-5 分
    @staticmethod
    def to_scorecard_dimension(assessment: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": "tech_maturity",
            "name": dict((d["key"], d["name"]) for d in DEFAULT_DIMENSIONS).get(
                "tech_maturity", "技术成熟度"),
            "score": assessment.get("overall_5", 3.0),
            "evidence": [e for d in assessment.get("dimensions", [])
                         for e in (d.get("evidence") or [])][:3],
            "rationale": f"六维均值 {assessment.get('overall', 0):.2f}，"
                         f"最弱 {assessment.get('weakest')}，最强 {assessment.get('strongest')}",
        }
