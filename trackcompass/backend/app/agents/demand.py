"""需求挖掘 Agent（技术文档 §5.3.2 / §4.4）。

输入：社媒、评论、论坛、招标、招聘（需求侧，**主证据**）
      + 产业媒体、论文、政策（**补充证据**，2026-09-16 放宽口径，见下）
工具：cluster_pain_points / sentiment_analysis
输出：痛点聚类 + 需求强度 + 建议验证实验

口径放宽的依据（用户裁决 ③，2026-09-16）
----------------------------------------
原实现只认需求侧五类。但真实信号库长期只有 `news / paper / policy`
（需求侧**一条都没有**，见 `docs/遗留项台账.md` L-15）⇒ `by_type()` 恒空
⇒ 需求强度恒为 0，需求探针结构性不可用。

放宽后承认：**政策（强制性要求/补贴导向）、论文（未解的技术痛点）、
产业媒体（行业诉求与扩产意图）同样承载需求信息**，只是
**证据强度弱于用户自己发声**（没有直接的付费意愿与负面情绪表达）。
因此按「需求侧优先、补充类型随后、全量兜底」的顺序组池，
并在 prompt 里如实说明证据类型，让模型自己判断可信度。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.agents.base import (
    CN_PAIN_PATTERNS,
    URGENCY_HINTS,
    AgentState,
    BaseAgent,
    SignalView,
    as_list,
    hit_any,
    negative_ratio,
    urgency_ratio,
    willingness_ratio,
)
from app.services.llm import COMPLEXITY_COMPLEX
from app.services.scoring import demand_score

#: 需求侧信号 —— 用户/客户**自己发声**，是需求强度的主证据
DEMAND_SOURCE_TYPES = ("social", "review", "bidding", "recruit", "forum")

#: 补充信号 —— 承载需求信息，但证据强度弱于用户发声
CONTEXT_SOURCE_TYPES = ("news", "paper", "policy")

#: 实际参与聚类的类型 = 主证据 + 补充证据
SOURCE_TYPES = DEMAND_SOURCE_TYPES + CONTEXT_SOURCE_TYPES

#: 组池时的优先级（需求侧在前 —— 模型与聚类样本都会先看到它们）
_TYPE_PRIORITY = {t: i for i, t in enumerate(SOURCE_TYPES)}


def demand_pool(state: AgentState) -> list[SignalView]:
    """需求探针的取数口径 —— **rule / build_prompt 必须共用这一份**。

    优先级：需求侧五类 → 补充三类 → （都为空时）全量兜底。

    ⚠️ 为什么必须抽成一个函数：`build_prompt` 曾自己写 `state.by_type(*SOURCE_TYPES)`，
    而真正送给模型的 context 来自 `retrieve()`（用的是全量信号）——
    两者不一致时 prompt 会自称「以下是 0 条信号」却塞进一屏真实信号，
    模型据此输出的可信度无从判断。口径不统一 = 自相矛盾的提示词。
    """
    pool = state.by_type(*SOURCE_TYPES)
    if not pool:
        # 全量兜底：宁可让模型看到弱相关信号，也不要喂空 prompt（会凭空编造）
        pool = state.since()
    return sorted(pool, key=lambda s: _TYPE_PRIORITY.get(s.signal_type, len(SOURCE_TYPES)))

EXPERIMENT_RECOMMEND = {
    "检测精度低": ["落地页测试", "客户访谈", "POC"],
    "部署成本高": ["定价测试", "问卷调研", "预售"],
    "交付周期长": ["客户访谈", "POC"],
    "数据孤岛": ["客户访谈", "渠道测试"],
    "人工复检多": ["POC", "客户访谈"],
    "维护成本高": ["问卷调研", "客户访谈"],
    "兼容性差": ["POC", "渠道测试"],
    "效果不可解释": ["问卷调研", "客户访谈"],
    "合规风险": ["问卷调研", "客户访谈"],
    "人才短缺": ["渠道测试", "问卷调研"],
}


class DemandDiscoveryAgent(BaseAgent):
    key = "demand"
    name = "需求挖掘 Agent"
    description = ("聚类外部信号中的痛点（需求侧用户发声优先，产业媒体/论文/政策作为补充），"
                   "输出需求强度与验证方案。")
    complexity = COMPLEXITY_COMPLEX
    #: 契约字段：需求强度是决策卡「需求强度」维度的输入
    required_keys = ("pain_points",)

    def system_prompt(self) -> str:
        return (
            "你是需求分析师。基于用户发声数据聚类痛点，输出 JSON："
            "{\"pain_points\":[{\"name\":\"\",\"freq\":0,\"sentiment\":-0.5,"
            "\"demand_score\":0.0,\"samples\":[]}],\"suggested_experiments\":[]}"
        )

    def rule(self, state: AgentState) -> dict[str, Any]:
        pool = demand_pool(state)
        clusters = self._cluster(pool)

        pain_points: list[dict[str, Any]] = []
        for name, sigs in clusters.items():
            texts = [s.text for s in sigs]
            cfg = CN_PAIN_PATTERNS.get(name, {})
            neg = negative_ratio(texts) or 0.6
            scored = demand_score(
                len(sigs), -neg,
                cfg.get("willingness", willingness_ratio(texts)) or 0.5,
                cfg.get("urgency", urgency_ratio(texts)) or 0.5,
            )
            top = sorted(sigs, key=lambda s: abs(s.sentiment or 0.6), reverse=True)[:3]
            pain_points.append({
                "name": name,
                "freq": len(sigs),
                "sentiment": round(-neg, 2),
                "willingness": round(
                    cfg.get("willingness", willingness_ratio(texts)) or 0.5, 2),
                "urgency": round(cfg.get("urgency", urgency_ratio(texts)) or 0.5, 2),
                "demand_score": scored["demand_score_5"],
                "demand_score_raw": scored["score"],
                "factors": scored["factors"],
                "samples": [s.title or s.content[:80] for s in top],
                "sources": sorted({s.source for s in sigs})[:5],
                "evidence_ref": top[0].id if top else None,
                "experiments": EXPERIMENT_RECOMMEND.get(name, ["客户访谈", "问卷调研"]),
            })

        pain_points.sort(key=lambda p: p["demand_score"], reverse=True)

        suggested: list[str] = []
        for p in pain_points[:3]:
            for e in p["experiments"]:
                if e not in suggested:
                    suggested.append(e)

        overall = (
            round(sum(p["demand_score"] for p in pain_points[:5]) / max(1, len(pain_points[:5])), 2)
            if pain_points else 0.0
        )
        return {
            "pain_points": pain_points[:8],
            "suggested_experiments": suggested[:5] or ["落地页测试", "客户访谈", "问卷调研"],
            "demand_score_5": overall,
            "sample_size": len(pool),
            "coverage": {
                "negative_ratio": negative_ratio([s.text for s in pool]),
                "willingness_ratio": willingness_ratio([s.text for s in pool]),
                "urgency_ratio": urgency_ratio([s.text for s in pool]),
            },
        }

    # ------------------------------------------------------------ 聚类
    def _cluster(self, signals: list[SignalView]) -> dict[str, list[SignalView]]:
        """痛点聚类：词典模式匹配（生产可替换为 BERT + HDBSCAN 语义聚类）。"""
        clusters: dict[str, list[SignalView]] = defaultdict(list)
        unmatched: list[SignalView] = []

        for sig in signals:
            text = sig.text
            matched = [
                name for name, cfg in CN_PAIN_PATTERNS.items()
                if hit_any(text, cfg["patterns"])
            ]
            if not matched:
                unmatched.append(sig)
                continue
            for name in matched:
                clusters[name].append(sig)

        # 未命中已知痛点的文本按关键词兜底归桶，保证召回
        for sig in unmatched:
            label = self._fallback_label(sig)
            if label:
                clusters[label].append(sig)

        return {k: v for k, v in clusters.items() if v}

    @staticmethod
    def _fallback_label(sig: SignalView) -> str | None:
        text = sig.text
        for hint, label in (("精度", "检测精度低"), ("成本", "部署成本高"),
                            ("周期", "交付周期长"), ("集成", "数据孤岛"),
                            ("人工", "人工复检多"), ("维护", "维护成本高")):
            if hint in text:
                return label
        if sig.signal_type == "recruit":
            return "人才短缺"
        if sig.signal_type == "bidding":
            return "交付周期长"
        # 带明确负面表达的文本归「整体体验不足」。
        # 2026-09-16 放宽口径时把 `news` 一并纳入：产业媒体常转述行业抱怨与产能瓶颈，
        # 而 policy / paper 多为正向或中性表述，泛化纳进来只会造出假痛点。
        if re.search(r"(不|没|差|难|慢|贵)", text) and sig.signal_type in (
                "social", "review", "forum", "news"):
            return "整体体验不足"
        return None

    def build_prompt(self, state: AgentState, context: str) -> str:
        pool = demand_pool(state)
        kinds = sorted({s.signal_type for s in pool})
        return (
            f"关键词={state.query}\n"
            f"以下是 {len(pool)} 条需求相关信号（类型：{'/'.join(kinds) or '无'}；"
            f"其中社媒/评论/论坛/招标/招聘为用户直接发声，是主证据；"
            f"产业媒体/论文/政策为补充证据，请据此判断可信度）：\n{context}\n\n"
            "输出痛点聚类 JSON，sentiment 取负面情感占比（负值），demand_score 为 1-5 分。"
        )

    def postprocess(self, output: dict[str, Any], state: AgentState) -> dict[str, Any]:
        pts = output.get("pain_points", [])
        for i, p in enumerate(pts, start=1):
            p.setdefault("demand_score", p.get("demand_score_raw", 3.0) * 4 + 1)
            p.setdefault("samples", [])
            p.setdefault("ref", f"P{i}")
            # ⚠️ `experiments` 必须补：prompt 里**没有**要求模型输出它，
            # 而前端 `Demand.tsx` 直接 `p.experiments.map(...)` 渲染 ——
            # 缺失会让整页白屏（实测 `Cannot read properties of undefined
            # (reading 'map')` at Demand.tsx:194）。
            # 推荐算法与规则路径保持一致：按痛点名查表，查不到给默认两项。
            p["experiments"] = as_list(p.get("experiments")) or \
                EXPERIMENT_RECOMMEND.get(str(p.get("name") or ""), ["客户访谈", "问卷调研"])
        return output
