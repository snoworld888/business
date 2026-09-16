"""监控预警 Agent（技术文档 §5.3.7 / §4.8）。

输入：信号更新
工具：rule_engine / anomaly_detection
输出：预警通知、复盘建议、评分模型校准建议
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.base import AgentState, BaseAgent
from app.services.llm import COMPLEXITY_SIMPLE
from app.services.scoring import validate_weights

logger = logging.getLogger(__name__)

#: 预警级别的**规范词表**（前端 `LEVEL_META` 与本文件规则表共用）。
ALERT_LEVELS: tuple[str, ...] = ("critical", "warning", "info")

#: 别名 → 规范级别。
#: 历史数据里出现过两套非规范写法（词表统一前落库、未做数据迁移）：
#:   `high/medium/low`（49 条）与首字母大写 `High/Medium/Low`（4 条）
#: 而接口的 severity 聚合只统计规范值 ⇒ **绝大部分预警的级别被静默丢弃**，
#: 严重度汇总恒为 0/0/0。这里按序数强度一对一归并。
_LEVEL_ALIASES: dict[str, str] = {
    "high": "critical", "medium": "warning", "low": "info",
    "severe": "critical", "warn": "warning", "notice": "info",
}


def normalize_alert_level(level: Any) -> str:
    """把任意来源的级别归一为 `ALERT_LEVELS` 之一。

    未知值降级为 `info` 并记 warning 日志 —— 不丢条目，同时让口径漂移在日志里可见。
    """
    raw = str(level or "").strip().lower()
    mapped = _LEVEL_ALIASES.get(raw, raw)
    if mapped in ALERT_LEVELS:
        return mapped
    if raw:
        logger.warning("未知预警级别 %r，已按 info 处理（请检查规则表词表）", level)
    return "info"


def level_rank(level: Any) -> int:
    """排序权重：critical(0) → warning(1) → info(2)。

    归一函数保证返回值必在词表内，故不会 `KeyError` / `ValueError`
    —— 原实现是 `{"critical":0,...}[a["level"]]` 的硬查表，遇到非规范值直接崩。
    """
    return ALERT_LEVELS.index(normalize_alert_level(level))


def level_variants(level: Any) -> tuple[str, ...]:
    """返回某级别在**库里可能出现的全部写法**，用于 SQL 过滤。

    例：`critical` → `('High', 'critical', 'high', 'severe')`。
    不这样做的话，按 `critical` 过滤会漏掉历史数据里的 `high`。
    """
    canon = normalize_alert_level(level)
    variants = {canon, canon.capitalize()}
    for alias, target in _LEVEL_ALIASES.items():
        if target == canon:
            variants.add(alias)
            variants.add(alias.capitalize())
    return tuple(sorted(variants))

# 预警规则（YAML 语义的 Python 等价实现，技术文档 §4.8）
RULES: list[dict[str, Any]] = [
    {
        "name": "大厂入场",
        "signal_type": "funding",
        "level": "critical",
        "min_amount": 1e8,
        "hints": ["集团", "科技有限", "股份有限公司", "研究院", "龙头", "头部"],
        "action": "alert",
        "advice": "头部玩家进入将快速压缩窗口期，建议 30 天内完成差异化定位或重新评估壁垒。",
    },
    {
        "name": "政策利好",
        "signal_type": "policy",
        "level": "info",
        "hints": ["支持", "鼓励", "试点", "补贴", "行动方案", "指导意见"],
        "action": "alert",
        "advice": "政策红利期通常 12-24 个月，建议同步准备资质与标杆案例。",
    },
    {
        "name": "技术突破",
        "signal_type": "paper",
        "level": "warning",
        "hints": ["突破", "首次", "刷新", "SOTA", "新算法", "新架构"],
        "action": "alert",
        "advice": "技术拐点可能重构竞争要素，建议复评 TRL 与专利布局。",
    },
    {
        "name": "需求激增",
        "signal_type": "social",
        "level": "warning",
        "hints": ["急需", "迫切", "投诉", "无法忍受", "严重影响"],
        "action": "alert",
        "advice": "需求侧出现强情绪表达，建议加速落地页测试以捕获转化。",
    },
    {
        "name": "招标放量",
        "signal_type": "bidding",
        "level": "info",
        "hints": ["招标", "采购", "公开遴选", "框架协议"],
        "action": "alert",
        "advice": "出现规模化采购信号，建议核对资质门槛并测算投标 ROI。",
    },
    {
        "name": "监管收紧",
        "signal_type": "policy",
        "level": "critical",
        "hints": ["限制", "收紧", "退坡", "监管", "整改", "专项整治"],
        "action": "alert",
        "advice": "合规成本将上升，建议先做合规可行性专项评估再投放资源。",
    },
]


class MonitorAlertAgent(BaseAgent):
    key = "monitor"
    name = "监控预警 Agent"
    description = "跟踪关键信号，触发变相时刻提醒，输出复盘与评分模型校准建议。"
    complexity = COMPLEXITY_SIMPLE
    #: 契约字段：预警页与复盘都依赖 alerts
    required_keys = ("alerts",)

    def system_prompt(self) -> str:
        return (
            "你是信号监控分析师。输出 JSON：{\"alerts\":[{\"type\":\"\",\"level\":\"\","
            "\"title\":\"\",\"summary\":\"\",\"advice\":\"\"}],\"calibration\":[]}"
        )

    def rule(self, state: AgentState) -> dict[str, Any]:
        signals = state.since()
        alerts: list[dict[str, Any]] = []

        for rule in RULES:
            matched = [
                s for s in signals
                if s.signal_type == rule["signal_type"]
                and any(h in s.text for h in rule["hints"])
                and self._passes_amount(rule, s)
            ]
            if not matched:
                continue
            top = max(matched, key=lambda s: float((s.metrics or {}).get("amount", 0) or 0))
            alerts.append({
                "rule": rule["name"],
                "type": rule["name"],
                "level": rule["level"],
                "signal_type": rule["signal_type"],
                "title": f"{rule['name']}：{top.title}",
                "summary": top.content[:200] or top.title,
                "advice": rule["advice"],
                "matched_count": len(matched),
                "signal_id": top.id,
                "source": top.source,
                "url": top.url,
                "track": top.industry,
                "amount": float((top.metrics or {}).get("amount", 0) or 0),
                "payload": {
                    "signal_id": top.id,
                    "matched_count": len(matched),
                    "rule": rule["name"],
                    "track": top.industry,
                },
            })

        alerts.sort(key=lambda a: level_rank(a["level"]))
        calibration = self._calibration(state)

        return {
            "alerts": alerts[:20],
            "alert_count": len(alerts),
            "severity": {
                lv: sum(1 for a in alerts
                        if normalize_alert_level(a["level"]) == lv)
                for lv in ALERT_LEVELS
            },
            "calibration": calibration,
            "scanned": len(signals),
        }

    # ------------------------------------------------------------ 子算法
    @staticmethod
    def _passes_amount(rule: dict[str, Any], sig) -> bool:
        threshold = rule.get("min_amount")
        if not threshold:
            return True
        return float((sig.metrics or {}).get("amount", 0) or 0) >= threshold

    @staticmethod
    def _calibration(state: AgentState) -> list[dict[str, Any]]:
        """根据需求/竞争/技术侧的强弱，给出评分卡权重校准建议（《原型设计》§5.10）。"""
        suggestions: list[dict[str, Any]] = []
        demand = state.results.get("demand", {}) or {}
        competition = state.results.get("competition", {}) or {}
        tech = state.results.get("tech", {}) or {}

        demand_score = float(demand.get("demand_score_5", 0) or 0)
        threat = float(competition.get("threat_score", 0) or 0)
        tech_score = float(tech.get("overall_5", 0) or 0)

        if demand_score >= 4.0 and threat >= 0.6:
            suggestions.append({
                "dimension": "competition",
                "from": 0.15, "to": 0.20, "delta": 0.05,
                "reason": "需求已验证充分但大厂威胁高，历史复盘显示竞争壁垒权重被低估",
                "source": "monitor",
            })
            suggestions.append({
                "dimension": "demand", "from": 0.20, "to": 0.15, "delta": -0.05,
                "reason": "需求侧共识度高，边际信息量下降",
                "source": "monitor",
            })
        if tech_score and tech_score <= 3.0:
            suggestions.append({
                "dimension": "tech_maturity", "from": 0.15, "to": 0.18, "delta": 0.03,
                "reason": f"技术就绪度仅 {tech_score}，建议提升该维度权重以抑制高估",
                "source": "monitor",
            })
        if suggestions:
            total = 0.0
            for s in suggestions:
                total += s["delta"]
            if abs(total) > 1e-6:  # 归一化，保证权重和仍为 1
                target = "timing"
                suggestions.append({
                    "dimension": target, "from": 0.10, "to": round(0.10 - total, 4),
                    "delta": round(-total, 4),
                    "reason": "配平权重总和至 1.0",
                    "source": "monitor",
                })
        return suggestions

    def build_prompt(self, state: AgentState, context: str) -> str:
        return (
            f"最新信号：\n{context}\n\n"
            "按规则识别关键变化（大厂入场/政策利好/技术突破/需求激增/招标放量/监管收紧），"
            "输出预警 JSON。"
        )

    def postprocess(self, output: dict[str, Any], state: AgentState) -> dict[str, Any]:
        output.setdefault("alerts", [])
        output.setdefault("calibration", [])
        return output


def apply_calibration(weights: dict[str, float],
                      suggestions: list[dict[str, Any]]) -> dict[str, Any]:
    """把校准建议应用到权重，返回新权重与差异（供 API 层落库 + 版本管理）。"""
    new_weights = dict(weights)
    diff: dict[str, Any] = {}
    for s in suggestions:
        key = s.get("dimension")
        if key not in new_weights:
            continue
        before = float(new_weights[key])
        after = float(s.get("to", before))
        new_weights[key] = round(after, 4)
        diff[key] = {"from": round(before, 4), "to": round(after, 4),
                     "delta": round(after - before, 4), "reason": s.get("reason")}
    total = round(sum(new_weights.values()), 6)
    if abs(total - 1.0) > 1e-6 and new_weights:
        scale = 1.0 / total
        new_weights = {k: round(v * scale, 4) for k, v in new_weights.items()}
    return {
        "weights": new_weights,
        "diff": diff,
        "validation": validate_weights(new_weights),
    }
