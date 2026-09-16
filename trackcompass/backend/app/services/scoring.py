"""评分算法库：全部为纯函数，便于单测与回归。

对应关系：
  normalize / trend_score      → 技术文档 §4.2 趋势雷达
  demand_score                 → 技术文档 §4.4 需求探针
  total_score / explain_score  → 技术文档 §4.5 赛道评分卡
  tam_sam_som                  → 技术文档 §5.3.3 市场测算 Agent
  six_dim_assessment           → 《设计文档》§8 技术评估六维
  threat_score                 → 技术文档 §5.3.4 竞争情报 Agent
"""

from __future__ import annotations

import math
from typing import Any


# ------------------------------------------------------------------ 工具
def normalize(value: float, lo: float | None = None, hi: float | None = None) -> float:
    """把任意量纲映射到 [0,1]。未给定上下界时使用对数压缩，避免长尾拉爆。"""
    if value is None:
        return 0.0
    if lo is not None and hi is not None and hi > lo:
        return max(0.0, min(1.0, (value - lo) / (hi - lo)))
    v = max(0.0, float(value))
    return round(1 - math.exp(-v / 10.0), 6)


def clamp(value: float, lo: float = 1.0, hi: float = 5.0) -> float:
    return max(lo, min(hi, float(value)))


def scale_to_5(value_0_1: float) -> float:
    """[0,1] → [1,5]，与文档中「评分 1–5」量纲对齐。"""
    return round(1 + 4 * max(0.0, min(1.0, value_0_1)), 2)


# ------------------------------------------------------------------ §4.2 趋势雷达
def trend_score(
    signal_count: float,
    growth_rate: float,
    policy_support: float,
    capital_heat: float,
) -> dict[str, Any]:
    """文档原文公式：
    0.3*normalize(signal_count) + 0.3*normalize(growth_rate)
    + 0.2*policy_support + 0.2*normalize(capital_heat)
    """
    f = {
        "signal_count": round(normalize(signal_count), 4),
        "growth_rate": round(normalize(growth_rate, -1.0, 1.0), 4),
        "policy_support": round(max(0.0, min(1.0, policy_support)), 4),
        "capital_heat": round(normalize(capital_heat), 4),
    }
    score = (
        0.3 * f["signal_count"]
        + 0.3 * f["growth_rate"]
        + 0.2 * f["policy_support"]
        + 0.2 * f["capital_heat"]
    )
    return {
        "score": round(score, 4),
        "score_5": scale_to_5(score),
        "factors": f,
        "formula": "0.3*signal + 0.3*growth + 0.2*policy + 0.2*capital",
    }


def direction_of(score: float, threshold: float = 0.5) -> str:
    return "tailwind" if score >= threshold else "headwind"


# ------------------------------------------------------------------ §4.4 需求探针
def demand_score(freq: float, sentiment: float, willingness: float,
                 urgency: float) -> dict[str, Any]:
    """文档原文公式：
    0.3*normalize(freq) + 0.3*sentiment + 0.2*willingness + 0.2*urgency
    其中 sentiment 取「负面情感占比」绝对值，越负面需求越强。
    """
    f = {
        "frequency": round(normalize(freq), 4),
        "sentiment": round(abs(max(-1.0, min(0.0, sentiment))), 4),
        "willingness": round(max(0.0, min(1.0, willingness)), 4),
        "urgency": round(max(0.0, min(1.0, urgency)), 4),
    }
    score = (
        0.3 * f["frequency"]
        + 0.3 * f["sentiment"]
        + 0.2 * f["willingness"]
        + 0.2 * f["urgency"]
    )
    return {
        "score": round(score, 4),
        "demand_score_5": scale_to_5(score),
        "factors": f,
        "formula": "0.3*freq + 0.3*negative_sentiment + 0.2*willingness + 0.2*urgency",
    }


# ------------------------------------------------------------------ §4.5 评分卡
DEFAULT_DIMENSIONS: list[dict[str, Any]] = [
    {"key": "market_size", "name": "市场空间", "weight": 0.20},
    {"key": "demand", "name": "需求真实性", "weight": 0.20},
    {"key": "tech_maturity", "name": "技术成熟度", "weight": 0.15},
    {"key": "competition", "name": "竞争壁垒", "weight": 0.15},
    {"key": "business_model", "name": "商业模式", "weight": 0.10},
    {"key": "timing", "name": "时机", "weight": 0.10},
    {"key": "fit", "name": "内部匹配度", "weight": 0.10},
]


def validate_weights(weights: dict[str, float], tolerance: float = 1e-6) -> dict[str, Any]:
    total = sum(weights.values())
    return {
        "ok": abs(total - 1.0) < tolerance,
        "total": round(total, 6),
        "message": "权重合法" if abs(total - 1.0) < tolerance
        else f"权重之和应为 1.0，当前 {total:.4f}",
    }


def total_score(dimensions: list[dict[str, Any]]) -> float:
    """文档原文：sum(d.weight * d.score)。权重自动归一化，容忍用户输入 0-100。"""
    weight_sum = sum(d.get("weight", 0) for d in dimensions) or 1.0
    raw = sum(d.get("weight", 0) * d.get("score", 0) for d in dimensions) / weight_sum
    return round(raw, 2)


def explain_score(dimensions: list[dict[str, Any]]) -> dict[str, Any]:
    """输出可解释结构：每个维度的贡献度 + 证据引用（技术文档 §4.5 可解释性）。"""
    weight_sum = sum(d.get("weight", 0) for d in dimensions) or 1.0
    contributions = []
    for d in dimensions:
        weight = d.get("weight", 0)
        score = d.get("score", 0)
        contributions.append({
            "key": d.get("key"),
            "name": d.get("name"),
            "weight": round(weight / weight_sum, 4),
            "score": round(score, 2),
            "contribution": round(weight / weight_sum * score, 4),
            "evidence": d.get("evidence", []),
            "rationale": d.get("rationale", ""),
        })
    contributions.sort(key=lambda x: x["contribution"], reverse=True)
    return {
        "total_score": total_score(dimensions),
        "dimensions": contributions,
        "weights_valid": validate_weights(
            {c["key"]: c["weight"] for c in contributions}
        ),
    }


def risk_alerts_from(dimensions: dict[str, float],
                     thresholds: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """维度低于阈值即触发风险预警（《原型设计》§5.7 风险预警区）。"""
    thresholds = thresholds or {
        "market_size": 3.0, "demand": 3.0, "tech_maturity": 3.0,
        "competition": 2.5, "business_model": 3.0, "timing": 3.0, "fit": 3.0,
    }
    catalog = {
        "market_size": "市场空间不足", "demand": "需求真实性存疑",
        "tech_maturity": "技术成熟度偏低", "competition": "竞争壁垒薄弱（大厂入场风险）",
        "business_model": "商业模式尚未闭环", "timing": "时机窗口不利",
        "fit": "与公司能力匹配度低",
    }
    alerts = []
    for key, threshold in thresholds.items():
        value = dimensions.get(key)
        if value is None or value >= threshold:
            continue
        alerts.append({
            "dimension": key,
            "level": "critical" if value < threshold - 1 else "warning",
            "title": catalog.get(key, key),
            "detail": f"{key} 得分 {value:.2f} < 阈值 {threshold:.2f}",
        })
    return alerts


def rank_opportunities(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """排序对比（《原型设计》§5.7）。"""
    ranked = sorted(items, key=lambda x: x.get("total_score", 0), reverse=True)
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i
    return ranked


# ------------------------------------------------------------------ §5.3.3 市场测算
def tam_sam_som(
    *,
    total_customers: float | None = None,
    arpu: float | None = None,
    industry_size: float | None = None,
    penetration: float | None = None,
    reachable_ratio: float = 0.2,
    obtainable_ratio: float = 0.1,
) -> dict[str, Any]:
    """自下而上（客户数 × 客单价）与自上而下（行业规模 × 渗透率）双路测算后取几何均值。"""
    bottom_up = None
    if total_customers and arpu:
        bottom_up = float(total_customers) * float(arpu)
    top_down = None
    if industry_size and penetration:
        top_down = float(industry_size) * float(penetration)

    estimates = [v for v in (bottom_up, top_down) if v]
    if estimates:
        tam = math.prod(estimates) ** (1 / len(estimates))  # 几何均值，抑制单路偏差
    else:
        tam = 0.0

    sam = tam * reachable_ratio
    som = sam * obtainable_ratio
    return {
        "TAM": round(tam, 2),
        "SAM": round(sam, 2),
        "SOM": round(som, 2),
        "method": {
            "bottom_up": round(bottom_up, 2) if bottom_up else None,
            "top_down": round(top_down, 2) if top_down else None,
            "fusion": "geometric_mean" if len(estimates) > 1 else "single_source",
            "reachable_ratio": reachable_ratio,
            "obtainable_ratio": obtainable_ratio,
        },
        "unit_economics": {
            "total_customers": total_customers,
            "arpu": arpu,
            "industry_size": industry_size,
        },
        "unit": "CNY",
    }


# ------------------------------------------------------------------ §8 技术评估六维
TECH_DIMENSIONS = ["TRL", "CRL", "BRL", "IPRL", "TMRL", "FRL"]

TRL_ANCHORS = [
    (1, "基本原理发现"), (2, "技术概念形成"), (3, "关键功能验证"),
    (4, "实验室环境验证"), (5, "相关环境验证"), (6, "相关环境演示"),
    (7, "真实环境演示"), (8, "系统完成并认证"), (9, "真实环境成熟运行"),
]


def trl_anchor(level: float) -> str:
    idx = int(max(1, min(9, round(level)))) - 1
    return TRL_ANCHORS[idx][1]


def six_dim_assessment(scores: dict[str, float],
                       evidence: dict[str, list] | None = None) -> dict[str, Any]:
    """TRL/CRL/BRL/IPRL/TMRL/FRL 六维，统一 1–9 量纲。"""
    evidence = evidence or {}
    dims = []
    for key in TECH_DIMENSIONS:
        value = clamp(scores.get(key, 1.0), 1.0, 9.0)
        dims.append({
            "key": key,
            "name": DIMENSION_CN[key],
            "score": round(value, 2),
            "normalized": round((value - 1) / 8, 4),
            "evidence": evidence.get(key, []),
            "anchor": trl_anchor(value) if key == "TRL" else None,
        })
    avg = sum(d["normalized"] for d in dims) / len(dims)
    return {
        "dimensions": dims,
        "overall": round(avg, 4),
        "overall_9": round(1 + 8 * avg, 2),
        "overall_5": scale_to_5(avg),
        "weakest": min(dims, key=lambda d: d["score"])["key"],
        "strongest": max(dims, key=lambda d: d["score"])["key"],
    }


DIMENSION_CN = {
    "TRL": "技术就绪水平", "CRL": "客户就绪水平", "BRL": "商业就绪水平",
    "IPRL": "知识产权就绪水平", "TMRL": "团队就绪水平", "FRL": "资金就绪水平",
}


# ------------------------------------------------------------------ §5.3.4 竞争情报
def threat_score(major_players: int, patent_density: float,
                 funding_momentum: float, substitution_risk: float) -> dict[str, Any]:
    """大厂威胁评分：越高代表竞争压力越大、壁垒越难建立。"""
    f = {
        "major_players": round(normalize(major_players, 0, 10), 4),
        "patent_density": round(normalize(patent_density, 0, 1), 4),
        "funding_momentum": round(normalize(funding_momentum), 4),
        "substitution_risk": round(max(0.0, min(1.0, substitution_risk)), 4),
    }
    score = (
        0.35 * f["major_players"]
        + 0.25 * f["patent_density"]
        + 0.20 * f["funding_momentum"]
        + 0.20 * f["substitution_risk"]
    )
    return {
        "threat_score": round(score, 4),
        "threat_level": "high" if score >= 0.7 else "medium" if score >= 0.4 else "low",
        "barrier_score_5": scale_to_5(1 - score),
        "factors": f,
    }
