"""趋势侦察 Agent（技术文档 §5.3.1）。

输入：政策、论文、专利、新闻信号
工具：search_policies / search_patents / search_news（本地实现为信号分桶）
输出：trends[{name, description, confidence, direction, evidence}]
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.agents.base import (
    BaseAgent,
    AgentState,
    SignalView,
    extract_patent_terms,
    extract_tech_keywords,
    growth_rate,
    hit_any,
)
from app.services.llm import COMPLEXITY_COMPLEX
from app.services.scoring import direction_of, normalize, trend_score

POLICY_BOOST = ["支持", "鼓励", "意见", "规划", "试点", "补贴", "行动方案", "指导意见", "重点"]
HEADWIND_HINTS = ["限制", "收紧", "退坡", "产能过剩", "泡沫", "监管", "降温"]


class TrendReconAgent(BaseAgent):
    key = "trend"
    name = "趋势侦察 Agent"
    description = "每日扫描 PESTEL、技术拐点、政策红利，识别未来 6-12 个月可能爆发的技术方向。"
    complexity = COMPLEXITY_COMPLEX
    #: 契约字段：雷达页与 `trends` 表落库都依赖它
    required_keys = ("trends",)

    def system_prompt(self) -> str:
        return (
            "你是一个科技趋势分析师。基于给定信号，识别未来 6-12 个月可能爆发的技术方向。"
            "严格输出 JSON：{\"trends\":[{\"name\":\"\",\"description\":\"\","
            "\"confidence\":0.0,\"direction\":\"tailwind|headwind\",\"evidence\":[\"E1\"]}]}"
        )

    # ------------------------------------------------------------ 规则实现
    def rule(self, state: AgentState) -> dict[str, Any]:
        buckets = self._bucket(state)
        caps = capability_terms(state)

        trends: list[dict[str, Any]] = []
        for name, sigs in buckets.items():
            policy_sigs = [s for s in sigs if s.signal_type == "policy"]
            funding_sigs = [s for s in sigs if s.signal_type == "funding"]
            policy_text = " ".join(s.text for s in policy_sigs)
            policy_support = min(1.0, len(policy_sigs) * 0.18 + hit_any(policy_text, POLICY_BOOST) * 0.05)
            capital_heat = sum(float((s.metrics or {}).get("amount", 0) or 0) for s in funding_sigs)
            scored = trend_score(len(sigs), growth_rate(sigs), policy_support, capital_heat)

            headwind = sum(1 for s in sigs if hit_any(s.text, HEADWIND_HINTS))
            if headwind > len(sigs) * 0.5:
                scored["score"] = round(scored["score"] * 0.7, 4)
                scored["factors"]["headwind_penalty"] = round(headwind / max(1, len(sigs)), 4)

            top = sorted(sigs, key=lambda s: _signal_weight(s), reverse=True)[:4]
            industries = [s.industry for s in sigs if s.industry]
            # 趋势自身的**客观技术标签**（桶名 + 桶下全部信号关键词）——
            # 与能力匹配解耦：标签落库，匹配由接口层按「当前公司」实时算
            tags = sorted({str(k) for s in sigs for k in (s.keywords or [])} | {name})
            factors = dict(scored["factors"])
            factors["_tags"] = tags
            trends.append({
                "name": name,
                "description": self._describe(name, sigs, policy_sigs, funding_sigs),
                "industry": state.industry or (industries[0] if industries else None),
                "confidence": round(min(0.95, 0.35 + scored["score"] * 0.65), 2),
                "direction": direction_of(scored["score"]),
                "score": scored["score"],
                "factors": factors,
                "signal_count": len(sigs),
                "policy_support": round(policy_support, 4),
                "capital_heat": round(capital_heat, 2),
                "evidence": [
                    {"signal_id": s.id, "source": s.source, "url": s.url, "title": s.title,
                     "signal_type": s.signal_type,
                     "published_at": s.published_at.isoformat() if s.published_at else None}
                    for s in top
                ],
                "keywords": sorted({k for s in sigs for k in (s.keywords or [])})[:8],
                # ---- 与当前公司能力画像的交叉核对（画像只参与**标注**，不参与打分）----
                "matched_capabilities": _match_capabilities(tags, caps),
            })
        for t in trends:
            t["capability_match"] = bool(t["matched_capabilities"])

        trends.sort(key=lambda t: t["score"], reverse=True)
        return {
            "trends": trends[:10],
            "industry": state.industry,
            "region": state.region,
            "scanned_signals": len(state.signals),
            # 画像里写了、但**外部信号里零证据**的方向 —— 这是"情报空白"，
            # 不是趋势。必须显式区分，否则会把"自己填的词"回显成"外部趋势"。
            "capability_gaps": _capability_gaps(caps, _covered_from_buckets(buckets)),
            "capability_terms": caps,
        }

    def _bucket(self, state: AgentState) -> dict[str, list[SignalView]]:
        """按技术关键词聚桶；无关键词时退化为按行业分桶。"""
        buckets: dict[str, list[SignalView]] = defaultdict(list)
        for sig in state.since():
            keys = sig.keywords or []
            if not keys:
                buckets[sig.industry or "综合"].append(sig)
                continue
            for k in keys:
                buckets[k].append(sig)
        return {k: v for k, v in buckets.items() if len(v) >= 1}

    @staticmethod
    def _describe(name: str, sigs: list[SignalView], policy: list[SignalView],
                  funding: list[SignalView]) -> str:
        bits = []
        if policy:
            bits.append(f"政策信号 {len(policy)} 条")
        if funding:
            total = sum(float((s.metrics or {}).get("amount", 0) or 0) for s in funding)
            bits.append(f"融资事件 {len(funding)} 起（合计 {total / 1e8:.1f} 亿元）")
        tech = [s for s in sigs if s.signal_type in ("paper", "patent")]
        if tech:
            bits.append(f"技术侧 {len(tech)} 条")
        return f"{name}：共 {len(sigs)} 条信号，" + "；".join(bits) + "。"

    def build_prompt(self, state: AgentState, context: str) -> str:
        return (
            f"行业={state.industry} 地域={state.region} 窗口={state.days}天\n"
            f"信号：\n{context}\n\n"
            "识别未来 6-12 个月可能爆发的技术方向，输出 JSON。"
        )

    def postprocess(self, output: dict[str, Any], state: AgentState) -> dict[str, Any]:
        """把趋势列表补齐成下游契约。

        真实大模型只给 `name/description/confidence`，**不给 `score`** —— 而
        `score` 是雷达排序与 `trends` 表落库的必需字段（缺了会被写成 0.0，
        导致扫出的趋势在页面上全部垫底）。这里用 `rule()` 里
        `confidence = min(0.95, 0.35 + score × 0.65)` 的**反函数**还原，
        保证与规则实现量纲一致，而不是随便填一个数。
        """
        caps = capability_terms(state)
        # 证据信号也进比对池（见 `_capability_pool` 的说明）
        signals_by_id = {s.id: s for s in state.signals}
        for t in output.get("trends") or []:
            if not isinstance(t, dict):
                continue
            score = _to_float(t.get("score"))
            if score is None:
                conf = _to_float(t.get("confidence"))
                if conf is not None:
                    score = max(0.0, min(1.0, (conf - 0.35) / 0.65))
                    t["score_source"] = "confidence_inverse"
                else:
                    score = 0.0
                    t["score_source"] = "default"
            t["score"] = round(score, 4)
            if _to_float(t.get("confidence")) is None:
                t["confidence"] = round(min(0.95, 0.35 + score * 0.65), 2)
            t.setdefault("direction", direction_of(t["score"]))
            # 证据编号 → 真实证据：模型只回 `"E1"` 这类编号，直接落库会让前端
            # 证据链渲染成一排 `—`（实测：40/40 条趋势的 evidence 全是编号串）。
            # 规则路径产出的 dict 由 `resolve_evidence_refs` 原样透传。
            t["evidence"] = self.resolve_evidence_refs(t.get("evidence"))
            factors = dict(t.get("factors") or {})
            # 落库**客观标签**（趋势自身的技术标签，与任何公司无关）——
            # 匹配不再冻结在 factors 里，由接口层按「当前公司」实时计算，
            # 这样切换公司即时生效、也不需要回算历史数据。
            factors["_tags"] = trend_tags(t, signals_by_id)
            t["factors"] = factors
            if not t.get("signal_count"):
                t["signal_count"] = len(t.get("evidence") or [])
            t["matched_capabilities"] = _match_capabilities(factors["_tags"], caps)
            t["capability_match"] = bool(t["matched_capabilities"])

        # 缺口必须用**最终**匹配结果重算：否则会出现「同一能力既匹配了某趋势、
        # 又出现在缺口里」的自相矛盾（实测「大模型」就是这种情况）。
        matched = {str(c) for t in (output.get("trends") or [])
                   for c in (t.get("matched_capabilities") or [])}
        covered = {str(k) for s in state.signals for k in (s.keywords or [])} | matched
        output["capability_gaps"] = _capability_gaps(caps, covered)
        return output


def _to_float(value: Any) -> float | None:
    """安全转 float —— 真实大模型可能把数值写成字符串（如 `"0.82"`）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


#: 画像里可当作「能力词」直接比对的长度上限；超过则改用抽词
_SHORT_TERM_MAX = 14

#: 参与能力比对的画像字段（顺序即展示顺序）
CAPABILITY_FIELDS: tuple[str, ...] = ("tech_stack", "patents")

#: 字段专属抽词器。
#: 专利标题在通用词表上命中率极低（实测「小样本工业缺陷识别模型训练方法」命中 0），
#: 原实现会退化成**整句当能力词** ⇒ 与任何趋势标签都配不上。
#: 见 `base.extract_patent_terms` 的说明。
_FIELD_EXTRACTORS = {"patents": extract_patent_terms}


def capability_terms_by_source(state: AgentState) -> dict[str, list[dict[str, Any]]]:
    """按**画像来源**展开能力词，供「本次分析依据」展示（见 `/analyze` 的 `profile_used`）。

    返回 `{字段: [{"raw": 原始条目, "terms": [抽出的能力词]}]}`，例如::

        {"patents": [{"raw": "小样本工业缺陷识别模型训练方法",
                      "terms": ["工业缺陷识别", "模型训练"]}]}

    这样界面上可以如实回答「这个能力词是从哪条专利抽出来的」。
    """
    profile = state.profile or {}
    out: dict[str, list[dict[str, Any]]] = {}
    for field in CAPABILITY_FIELDS:
        rows: list[dict[str, Any]] = []
        for raw in (profile.get(field) or []):
            text = str(raw).strip()
            if not text:
                continue
            extractor = _FIELD_EXTRACTORS.get(field)
            if len(text) <= _SHORT_TERM_MAX:
                candidates = [text]
            elif extractor is not None:
                candidates = extractor(text, limit=6) or [text]
            else:
                candidates = extract_tech_keywords(text, limit=6) or [text]
            terms = [str(c).strip() for c in candidates]
            rows.append({"raw": text, "terms": [c for c in dict.fromkeys(terms) if c]})
        out[field] = rows
    return out


def capability_terms(state: AgentState) -> list[str]:
    """从能力画像提取**能力词**（技术栈 + 专利），用于与趋势交叉核对。

    · 技术栈本身就是短词（「智能体」「大模型」）⇒ 直接采用
    · 专利常是长标题 ⇒ 用 `extract_patent_terms` 按构词法抽词，
      抽不到时才退回整句（**不静默丢弃** —— 专利要能在分析依据里被看到）

    ⚠️ 两条调用链必须同口径：Agent 分析走 `analysis.build_profile_input`，
    接口层走 `deps.current_capability_terms`。后者一直传了 `patents`，
    前者到 2026-09-16 才补上（此前分析路径的专利能力恒为空）。
    """
    terms: list[str] = []
    for rows in capability_terms_by_source(state).values():
        for row in rows:
            for c in row["terms"]:
                if c not in terms:
                    terms.append(c)
    return terms


def _match_capabilities(pool: list[str], caps: list[str]) -> list[str]:
    """能力词与一组文本的重合（双向子串，兼容「智能体」⊂「多智能体」）。"""
    pool_l = [str(p).lower() for p in pool if p]
    out: list[str] = []
    for c in caps:
        cl = c.lower()
        if any(cl in p or p in cl for p in pool_l):
            out.append(c)
    return out


#: 公开别名 —— 接口层要按「当前公司」实时算匹配（见 `deps.current_capability_terms`），
#: 跨模块引用私有名不合适，故导出为公开符号。
match_capabilities = _match_capabilities


def trend_tags(trend: dict[str, Any],
               signals_by_id: dict[str, Any] | None = None) -> list[str]:
    """趋势自身的**技术标签**（客观属性，与任何公司无关）。

    ⚠️ 这是把「能力匹配」从**落库快照**改为**查询时计算**的核心。

    原实现的缺陷：`factors._capability.matched` 在落库时按「当时的当前公司」算好并冻结，
    却没记录是给哪家公司算的 ——
    ① 切换公司后标注失效（可能显示"匹配的词不属于当前公司"）；
    ② 代码修复后必须回算历史数据（实测 5 条趋势的标注一直停在旧逻辑上）。

    现在趋势只存**客观标签**，匹配交给接口层实时算：

        matched = 当前公司能力词 ∩ 趋势标签

    ⇒ 多公司天然隔离、切换即时生效、永不需回算。

    标签来源（全部客观、可复算）：
      ① 证据链上各信号的关键词 —— 外部情报里明确出现的技术词
      ② 趋势名 / 描述中命中 `TECH_VOCAB` 的词 —— 词表匹配，非自由生成
    """
    tags: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            tags.append(text)

    if signals_by_id:
        for e in (trend.get("evidence") or []):
            sid = e.get("signal_id") if isinstance(e, dict) else None
            sig = signals_by_id.get(str(sid)) if sid else None
            if sig is not None:
                for k in (getattr(sig, "keywords", None) or []):
                    _add(k)

    label = f"{trend.get('name') or ''} {trend.get('description') or ''}"
    for term in extract_tech_keywords(label, limit=10):
        _add(term)
    return tags


def _capability_pool(trend: dict[str, Any],
                    signals_by_id: dict[str, Any] | None = None) -> list[str]:
    """用于能力比对的文本池。

    ⚠️ **必须包含「证据信号的关键词」**（实测漏项根因 2026-09-14）：
    LLM 生成的趋势名措辞与画像用词并不同构 —— 画像写「计算机视觉」，
    趋势名写「工业视觉」「智能质检」，严格子串匹配不上；
    而**证据信号的关键词里明确就有「计算机视觉」**（种子数据里 8 条信号带该词）。

    原实现只取趋势自身的字段（name / description / keywords），
    但 LLM 路径的 prompt **不要求输出 keywords** ⇒ 池子里根本没有「计算机视觉」
    ⇒ 实测 7 条应匹配的趋势**全部漏判**。

    匹配池构成：趋势名 + 描述 + 趋势关键词 + 证据信号的 keywords。
    """
    pool = [str(trend.get("name") or ""), str(trend.get("description") or "")]
    pool += [str(k) for k in (trend.get("keywords") or [])]
    if signals_by_id:
        for e in (trend.get("evidence") or []):
            sid = e.get("signal_id") if isinstance(e, dict) else None
            sig = signals_by_id.get(str(sid)) if sid else None
            if sig is not None:
                pool += [str(k) for k in (getattr(sig, "keywords", None) or [])]
    return [p for p in pool if p]


def _capability_gaps(caps: list[str], covered: set[str]) -> list[dict[str, Any]]:
    """画像提到、但**外部证据为零**的能力词 —— 属情报空白，不是趋势。

    `covered` 是"已有证据支撑的词集合"（信号关键词 + 命中趋势的能力词）。
    """
    covered_l = {str(c).lower() for c in covered if c}
    gaps: list[dict[str, Any]] = []
    for c in caps:
        cl = str(c).lower()
        if any(cl in k or k in cl for k in covered_l):
            continue
        gaps.append({
            "name": c,
            "source": "capability_profile",
            "evidence_count": 0,
            "direction": "unknown",
            "note": "来自能力画像，暂无外部信号支撑 —— 属情报空白而非趋势，建议补数据源或定向调研",
        })
    return gaps


def _covered_from_buckets(buckets: dict[str, list[SignalView]]) -> set[str]:
    """规则路径的"已覆盖词"：桶名 + 所有信号关键词。"""
    covered: set[str] = set(buckets.keys())
    for sigs in buckets.values():
        for s in sigs:
            covered.update(str(k) for k in (s.keywords or []))
    return covered


def _signal_weight(sig: SignalView) -> float:
    base = {"policy": 1.0, "funding": 0.9, "patent": 0.8, "paper": 0.7,
            "news": 0.6, "recruit": 0.5, "social": 0.4, "bidding": 0.6}.get(sig.signal_type, 0.4)
    return base + normalize(float((sig.metrics or {}).get("amount", 0) or 0)) * 0.3
