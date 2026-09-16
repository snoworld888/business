"""市场测算 Agent（技术文档 §5.3.3）。

自下而上（客户数 × 客单价）+ 自上而下（行业规模 × 渗透率），融合取几何均值。
规则实现由信号 metrics 与行业基线推导；真实场景可接 Euromonitor / 企查查 / 财报 API。
"""

from __future__ import annotations

from typing import Any

from app.agents.base import AgentState, BaseAgent
from app.services.llm import COMPLEXITY_COMPLEX
from app.services.scoring import normalize, tam_sam_som

# 行业基线（数量级参考值，生产环境应由行情数据源覆盖）
INDUSTRY_BASELINE: dict[str, dict[str, float]] = {
    "AI+制造": {"customers": 42_000, "arpu": 380_000, "industry_size": 1.2e11, "penetration": 0.07},
    "AI+医疗": {"customers": 26_000, "arpu": 520_000, "industry_size": 9.0e10, "penetration": 0.05},
    "新能源科技": {"customers": 12_000, "arpu": 2_400_000, "industry_size": 4.5e11, "penetration": 0.06},
    "AI+农业": {"customers": 60_000, "arpu": 90_000, "industry_size": 6.0e10, "penetration": 0.03},
    "具身智能": {"customers": 8_000, "arpu": 1_600_000, "industry_size": 2.0e11, "penetration": 0.02},
}
DEFAULT_BASELINE = {"customers": 20_000, "arpu": 300_000,
                    "industry_size": 8.0e10, "penetration": 0.05}


class MarketSizingAgent(BaseAgent):
    key = "market"
    name = "市场测算 Agent"
    description = "自下而上 + 自上而下测算 TAM/SAM/SOM，并给出增长率区间。"
    complexity = COMPLEXITY_COMPLEX
    #: 契约字段：决策卡「市场规模」维度直接读 TAM/SAM/SOM
    required_keys = ("TAM", "SAM", "SOM")

    def system_prompt(self) -> str:
        return (
            "你是市场规模分析师。输出 JSON：{\"TAM\":0,\"SAM\":0,\"SOM\":0,"
            "\"growth_rate\":0.0,\"method\":{},\"assumptions\":[]}"
        )

    def rule(self, state: AgentState) -> dict[str, Any]:
        industry = state.industry or (state.profile.get("industry") if state.profile else None)
        base = dict(INDUSTRY_BASELINE.get(industry or "", DEFAULT_BASELINE))

        funding = [s for s in state.since() if s.signal_type == "funding"]
        policy = [s for s in state.since() if s.signal_type == "policy"]
        patent = [s for s in state.since() if s.signal_type in ("patent", "paper")]

        # 信号修正：融资热度抬升渗透率预期，政策加码抬升可服务比例
        capital = sum(float((s.metrics or {}).get("amount", 0) or 0) for s in funding)
        penetration_adj = base["penetration"] * (1 + normalize(capital) * 0.6)
        reachable = min(0.6, 0.15 + len(policy) * 0.03)
        obtainable = min(0.35, 0.08 + normalize(len(patent)) * 0.15)

        result = tam_sam_som(
            total_customers=base["customers"], arpu=base["arpu"],
            industry_size=base["industry_size"], penetration=penetration_adj,
            reachable_ratio=reachable, obtainable_ratio=obtainable,
        )
        result["growth_rate"] = round(
            min(0.65, 0.12 + normalize(capital) * 0.25 + len(policy) * 0.012), 4
        )
        result["assumptions"] = [
            f"行业目标客户基数 {base['customers']:,} 家（{industry or '综合'}基线）",
            f"客单价中位数 {base['arpu']:,} 元/年",
            f"渗透率由基线 {base['penetration']:.1%} 按资本热度修正为 {penetration_adj:.1%}",
            f"可服务比例 {reachable:.1%}（依据政策信号 {len(policy)} 条）",
            f"可获得比例 {obtainable:.1%}（依据技术信号 {len(patent)} 条）",
        ]
        result["industry"] = industry or "综合"
        result["evidence_refs"] = [s.id for s in (funding + policy)[:4]]
        return result

    def build_prompt(self, state: AgentState, context: str) -> str:
        return (
            f"行业={state.industry}\n可用信号：\n{context}\n\n"
            "用自下而上与自上而下两种口径估算 TAM/SAM/SOM（单位 CNY），输出 JSON。"
        )

    def postprocess(self, output: dict[str, Any], state: AgentState) -> dict[str, Any]:
        for key in ("TAM", "SAM", "SOM"):
            output.setdefault(key, 0)
        output.setdefault("growth_rate", 0.0)
        return output
