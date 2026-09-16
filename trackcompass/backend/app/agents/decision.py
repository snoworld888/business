"""决策综合 Agent（技术文档 §5.3.6）。

输入：其余六个 Agent 的输出 + 公司能力画像 + 评分卡权重
工具：scorecard_calculator / report_generator
输出：七维评分、综合得分、风险预警、证据链
"""

from __future__ import annotations

from typing import Any

from app.agents.base import AgentState, BaseAgent, as_dict, as_list, dicts_only
from app.services.llm import COMPLEXITY_COMPLEX
from app.services.scoring import (
    DEFAULT_DIMENSIONS,
    clamp,
    explain_score,
    normalize,
    risk_alerts_from,
    scale_to_5,
    total_score,
)


class DecisionSynthesisAgent(BaseAgent):
    key = "decision"
    name = "决策综合 Agent"
    description = "汇总六个 Agent 结论，按可配置评分卡产出综合评分与决策报告草稿。"
    complexity = COMPLEXITY_COMPLEX
    #: 这三个字段空了会直接破坏下游：`/score` 的 `verdict.label`、七维贡献度自洽校验、
    #: 机会状态机判定。实测真实模型会返回 `verdict: {}`，必须由规则基线兜住。
    required_keys = ("verdict", "dimensions", "total_score")

    def system_prompt(self) -> str:
        return (
            "你是决策分析师。基于各 Agent 结论按评分卡七维度打分（1-5），输出 JSON："
            "{\"dimensions\":[{\"key\":\"\",\"name\":\"\",\"weight\":0.0,\"score\":0.0,"
            "\"rationale\":\"\"}],\"total_score\":0.0,\"risk_alerts\":[]}"
        )

    # ------------------------------------------------------------ 规则实现
    def rule(self, state: AgentState) -> dict[str, Any]:
        r = state.results
        weights = self._weights(state)
        # 全部用 as_dict / dicts_only 收口：上游是**大模型的输出**，不保证类型。
        # 实测模型会把 `coverage` / `unit_economics` 返回成字符串、把
        # `pain_points` 返回成 `["痛点A"]` 字符串数组 —— 直接 `.get` 会让整个
        # 规则基线崩溃，进而使契约回填失效（表现为 verdict 丢失）。
        market = as_dict(r.get("market"))
        demand = as_dict(r.get("demand"))
        competition = as_dict(r.get("competition"))
        tech = as_dict(r.get("tech"))
        trend = as_dict(r.get("trend"))

        dims: list[dict[str, Any]] = []

        # 1 市场空间：TAM/SOM 量级 + 增长率
        tam = float(market.get("TAM", 0) or 0)
        growth = float(market.get("growth_rate", 0) or 0)
        market_5 = scale_to_5(0.7 * normalize(tam) + 0.3 * min(1.0, growth / 0.5))
        dims.append(self._dim("market_size", weights, market_5,
                              as_list(market.get("assumptions"))[:2],
                              f"TAM {tam / 1e8:.1f} 亿元，年增速 {growth:.1%}"))

        # 2 需求真实性：需求强度为主，痛点数量为辅
        demand_5 = clamp(float(demand.get("demand_score_5", 0) or 0), 1, 5)
        pains = dicts_only(demand.get("pain_points"))
        first_pain = pains[0].get("name", "—") if pains else "—"
        first_strength = pains[0].get("demand_score", "—") if pains else "—"
        dims.append(self._dim("demand", weights, demand_5,
                              [p.get("evidence_ref") for p in pains[:3] if p.get("evidence_ref")],
                              f"痛点聚类 {len(pains)} 类，最强「{first_pain}」"
                              f"（强度 {first_strength}）" if pains else "需求侧证据不足"))

        # 3 技术成熟度
        tech_5 = clamp(float(tech.get("overall_5", 0) or 0), 1, 5)
        dims.append(self._dim("tech_maturity", weights, tech_5,
                              [e for d in dicts_only(tech.get("dimensions"))
                               for e in as_list(d.get("evidence"))][:3],
                              f"六维均值 {float(tech.get('overall_9', 0) or 0):.1f}/9，"
                              f"最弱 {tech.get('weakest', '—')}"))

        # 4 竞争壁垒：壁垒分直接可复用
        barrier_5 = clamp(float(competition.get("barrier_score_5", 0) or 0), 1, 5)
        dims.append(self._dim("competition", weights, barrier_5,
                              as_list(competition.get("evidence_refs"))[:3],
                              f"威胁等级 {competition.get('threat_level', '—')}，"
                              f"玩家 {competition.get('player_count', 0)} 家"))

        # 5 商业模式：支付意愿 + 客单价合理性 + 玩家数量（越少越易定价）
        demand_w = float(as_dict(demand.get("coverage")).get("willingness_ratio", 0) or 0)
        arpu = float(as_dict(market.get("unit_economics")).get("arpu", 0) or 0)
        biz_5 = scale_to_5(0.5 * min(1.0, demand_w * 2) + 0.3 * normalize(arpu)
                           + 0.2 * (1 - min(1.0, competition.get("player_count", 0) / 12)))
        dims.append(self._dim("business_model", weights, biz_5, [],
                              f"付费信号占比 {demand_w:.0%}，客单价 "
                              f"{arpu / 1e4:.1f} 万元/年" if arpu else f"付费信号占比 {demand_w:.0%}"))

        # 6 时机：趋势分 + 政策支持
        trends = sorted(dicts_only(trend.get("trends")),
                        key=lambda x: float(x.get("score", 0) or 0), reverse=True)
        best = trends[0] if trends else {}
        timing_5 = scale_to_5(0.6 * float(best.get("score", 0) or 0)
                              + 0.4 * float(best.get("policy_support", 0) or 0))
        dims.append(self._dim("timing", weights, timing_5,
                              [e.get("signal_id") for e in dicts_only(best.get("evidence"))[:2]
                               if e.get("signal_id")],
                              f"最强趋势「{best.get('name', '—')}」"
                              f"（置信度 {best.get('confidence', 0)}，"
                              f"{'顺风' if best.get('direction') == 'tailwind' else '逆风'}）"))

        # 7 内部匹配度：能力画像与赛道关键词重合度
        fit_5 = self._fit_score(state, best)
        dims.append(self._dim("fit", weights, fit_5, [],
                              self._fit_reason(state, best)))

        total = total_score(dims)
        explained = explain_score(dims)
        risks = risk_alerts_from({d["key"]: d["score"] for d in dims})

        # 冲突点检测：需求高但壁垒低，属于典型「伪机会」
        conflicts: list[str] = []
        dim_map = {d["key"]: d["score"] for d in dims}
        if dim_map.get("demand", 0) >= 4.0 and dim_map.get("competition", 0) <= 3.0:
            conflicts.append("需求旺盛但竞争壁垒薄弱，需警惕大厂复制风险")
        if dim_map.get("tech_maturity", 0) >= 4.0 and dim_map.get("market_size", 0) <= 3.0:
            conflicts.append("技术就绪但市场空间有限，存在「技术自嗨」风险")
        if dim_map.get("fit", 0) <= 2.8:
            conflicts.append("与公司现有能力匹配度偏低，落地需外部补能力")

        return {
            "dimensions": dims,
            "total_score": total,
            "explained": explained,
            "risk_alerts": risks,
            "conflicts": conflicts,
            "verdict": self._verdict(total),
            "weights_used": weights,
        }

    # ------------------------------------------------------------ 后处理
    def postprocess(self, output: dict[str, Any], state: AgentState) -> dict[str, Any]:
        """把 LLM 输出对齐成**自洽**的评分结果。

        关键不变量（技术文档 §4.4 / §4.5）：`total_score == Σ 各维贡献`。

        真实大模型会自己编一个 `total_score`，与它给出的 `dimensions` 对不上；
        而 `explained` 又由规则基线回填 —— 三者来自不同来源，自洽性直接破坏
        （实测 E2E 的「解释性贡献度自洽」断言因此失败）。

        ⇒ 因此**不采信模型算出来的总分**：总分是「维度 + 配置权重」的确定性函数，
        由本代码重算，并让 `verdict` 与重算后的分数保持一致。
        维度数不齐时不接管（交给 `backfill_contract` 用规则基线兜底）。
        """
        dims = dicts_only(output.get("dimensions"))
        if not dims:
            return output
        weights = self._weights(state)
        norm: list[dict[str, Any]] = []
        for d in dims:
            key = str(d.get("key") or "")
            meta = next((x for x in DEFAULT_DIMENSIONS
                         if x["key"] == key or x["name"] == d.get("name")), None)
            if meta is None:
                continue
            norm.append({
                "key": meta["key"],
                "name": meta["name"],
                # 权重一律取配置值：模型自报的权重不可信
                "weight": float(weights.get(meta["key"], meta["weight"])),
                "score": clamp(float(d.get("score", 0) or 0), 1, 5),
                "rationale": str(d.get("rationale") or d.get("note")
                                 or d.get("reason") or ""),
                "evidence": as_list(d.get("evidence")),
            })
        if len(norm) != len(DEFAULT_DIMENSIONS):
            return output
        output["dimensions"] = norm
        output["weights_used"] = weights
        output["total_score"] = total_score(norm)
        output["explained"] = explain_score(norm)
        output["verdict"] = self._verdict(output["total_score"])
        return output

    # ------------------------------------------------------------ 子算法
    @staticmethod
    def _weights(state: AgentState) -> dict[str, float]:
        custom = (state.profile or {}).get("scorecard_weights")
        if custom:
            return {k: float(v) for k, v in custom.items()}
        return {d["key"]: d["weight"] for d in DEFAULT_DIMENSIONS}

    @staticmethod
    def _dim(key: str, weights: dict[str, float], score: float,
             evidence: list, rationale: str) -> dict[str, Any]:
        name = next((d["name"] for d in DEFAULT_DIMENSIONS if d["key"] == key), key)
        return {
            "key": key, "name": name,
            "weight": float(weights.get(key, 0.0)),
            "score": round(clamp(score, 1, 5), 2),
            "evidence": [e for e in evidence if e],
            "rationale": rationale,
        }

    @staticmethod
    def _fit_score(state: AgentState, best_trend: dict[str, Any]) -> float:
        """内部匹配度 = 技术栈重合度(0.5) + 赛道方向一致性(0.3) + 客户资源覆盖(0.2)。"""
        profile = state.profile or {}
        stack = [str(t).lower() for t in (profile.get("tech_stack") or [])]
        if not stack:
            return 3.0

        trend_keys = [str(k).lower() for k in (best_trend.get("keywords") or [])]
        trend_name = str(best_trend.get("name", "")).lower()
        pool = [p for p in trend_keys + [trend_name] if p]
        overlap = sum(1 for s in stack if any(s in p or p in s for p in pool))
        ratio = overlap / max(1, len(stack))

        # 赛道方向一致性：分析口径、趋势所属行业、公司主营行业三者取任一命中
        profile_industry = str(profile.get("industry") or "")
        candidates = [state.industry or "", str(best_trend.get("industry") or "")]
        industry_hit = 1.0 if profile_industry and any(
            profile_industry in c or c in profile_industry for c in candidates if c
        ) else 0.0

        # 客户资源覆盖：客户行业词是否出现在趋势关键词或分析口径中
        customers = [str(c).lower() for c in (profile.get("customers") or [])]
        haystack = " ".join(pool) + " " + (state.industry or "").lower()
        customer_hit = 1.0 if customers and any(
            c in haystack or any(tok and tok in c for tok in pool) for c in customers
        ) else 0.3

        return round(clamp(
            scale_to_5(0.5 * ratio + 0.3 * industry_hit + 0.2 * customer_hit), 1, 5), 2)

    @staticmethod
    def _fit_reason(state: AgentState, best_trend: dict[str, Any]) -> str:
        profile = state.profile or {}
        stack = profile.get("tech_stack") or []
        if not stack:
            return "未建立能力画像，采用中性分 3.0"
        return (f"公司技术栈 {'/'.join(str(s) for s in stack[:4])}、"
                f"主营 {profile.get('industry') or '—'} 与趋势「{best_trend.get('name', '—')}」"
                f"（{best_trend.get('industry') or state.industry or '—'}）的匹配度评估")

    @staticmethod
    def _verdict(total: float) -> dict[str, Any]:
        if total >= 4.2:
            return {"code": "go", "label": "建议立项/投资", "color": "success"}
        if total >= 3.5:
            return {"code": "verify", "label": "建议继续验证", "color": "processing"}
        if total >= 2.8:
            return {"code": "watch", "label": "建议观察", "color": "warning"}
        return {"code": "hold", "label": "建议暂缓", "color": "default"}

    def build_prompt(self, state: AgentState, context: str) -> str:
        summary = {k: (v if not isinstance(v, dict) else
                       {kk: vv for kk, vv in v.items() if kk != "_meta"})
                   for k, v in state.results.items()}
        return (
            f"公司能力画像={state.profile}\n各 Agent 结论={summary}\n"
            f"评分卡权重={self._weights(state)}\n"
            "按七维度打分并给出综合评分与风险预警，输出 JSON。"
        )
