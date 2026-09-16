"""竞争情报 Agent（技术文档 §5.3.4）。

输入：竞品、专利、招聘
工具：competitor_discovery / patent_analysis
输出：竞争格局、玩家清单、空白点、大厂威胁评分
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.agents.base import AgentState, BaseAgent, SignalView
from app.services.llm import COMPLEXITY_COMPLEX
from app.services.scoring import normalize, scale_to_5, threat_score

# 视为「大厂」的信号来源标记（真实场景接入企业能力库/市值数据）
MAJOR_PLAYER_HINTS = ["集团", "科技有限", "股份有限公司", "研究院", "大厂", "头部", "龙头"]


class CompetitionIntelAgent(BaseAgent):
    key = "competition"
    name = "竞争情报 Agent"
    description = "分析竞品动态、大厂威胁、替代方案与供应链卡点，输出竞争格局与空白点。"
    complexity = COMPLEXITY_COMPLEX
    #: 契约字段：决策卡「竞争壁垒」维度读 threat_score
    required_keys = ("threat_score", "threat_level")

    def system_prompt(self) -> str:
        return (
            "你是竞争情报分析师。输出 JSON：{\"players\":[],\"threat_score\":0.0,"
            "\"threat_level\":\"\",\"gaps\":[],\"barriers\":[]}"
        )

    def rule(self, state: AgentState) -> dict[str, Any]:
        signals = state.since()
        players = self._discover_players(signals)
        patents = [s for s in signals if s.signal_type == "patent"]
        funding = [s for s in signals if s.signal_type == "funding"]
        recruit = [s for s in signals if s.signal_type == "recruit"]
        news = [s for s in signals if s.signal_type == "news"]

        major_players = sum(
            1 for name in players
            if any(h in name for h in MAJOR_PLAYER_HINTS)
        )
        patent_density = self._patent_density(patents)
        funding_momentum = sum(float((s.metrics or {}).get("amount", 0) or 0) for s in funding)
        substitution = self._substitution_risk(state, signals)

        scored = threat_score(major_players, patent_density, funding_momentum, substitution)

        gaps = self._gaps(state, players, recruit, signals)
        barriers = self._barriers(patents, recruit, news)

        return {
            "players": players[:10],
            "player_count": len(players),
            "major_player_count": major_players,
            "threat_score": scored["threat_score"],
            "threat_level": scored["threat_level"],
            "barrier_score_5": scored["barrier_score_5"],
            "factors": scored["factors"],
            "patent_density": round(patent_density, 4),
            "funding_momentum": round(funding_momentum, 2),
            "substitution_risk": round(substitution, 4),
            "gaps": gaps,
            "barriers": barriers,
            "evidence_refs": [s.id for s in (patents[:2] + funding[:2])],
        }

    # ------------------------------------------------------------ 子算法
    @staticmethod
    def _discover_players(signals: list[SignalView]) -> list[str]:
        counter: Counter[str] = Counter()
        for sig in signals:
            for name in (sig.entities or {}).get("company", []) or []:
                counter[str(name)] += 2
            if sig.signal_type in ("funding", "news"):
                for name in (sig.metrics or {}).get("companies", []) or []:
                    counter[str(name)] += 1
        return [name for name, _ in counter.most_common(10) if name]

    @staticmethod
    def _patent_density(patents: list[SignalView]) -> float:
        if not patents:
            return 0.0
        # 密度 = 同技术关键词下的专利信号占全部信号的比例（归一化到 0-1）
        return min(1.0, normalize(len(patents)) * 0.8 + 0.1)

    @staticmethod
    def _substitution_risk(state: AgentState, signals: list[SignalView]) -> float:
        hints = ["替代", "取代", "开源", "免费", "自研", "内部团队", "降本方案"]
        hits = sum(1 for s in signals if any(h in s.text for h in hints))
        return min(1.0, hits / max(1, len(signals)) * 2.5)

    @staticmethod
    def _gaps(state: AgentState, players: list[str], recruit: list[SignalView],
              signals: list[SignalView]) -> list[str]:
        gaps: list[str] = []
        smb_hints = ["中小企业", "小微", "轻量化", "低门槛", "标准品"]
        if any(any(h in s.text for h in smb_hints) for s in signals):
            gaps.append("中小企业/轻量化市场尚未被头部玩家覆盖")
        if len(players) < 5:
            gaps.append("玩家分散，缺乏绝对主导者，存在整合窗口")
        if any("服务" in s.text or "咨询" in s.text for s in recruit):
            gaps.append("缺乏配套的实施与运维服务能力")
        if not gaps:
            gaps.append("细分场景（垂直工况）尚无标准化产品")
        return gaps[:4]

    @staticmethod
    def _barriers(patents: list[SignalView], recruit: list[SignalView],
                  news: list[SignalView]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if patents:
            out.append({"type": "专利壁垒", "level": "medium" if len(patents) < 8 else "high",
                        "detail": f"可检索到 {len(patents)} 条相关专利信号"})
        if recruit:
            fields = Counter(
                kw for s in recruit for kw in (s.keywords or []) if kw
            )
            top = "、".join(k for k, _ in fields.most_common(3)) or "算法/工程"
            out.append({"type": "人才争夺", "level": "medium",
                        "detail": f"招聘热点集中在 {top}"})
        if news:
            out.append({"type": "生态卡位", "level": "low",
                        "detail": "头部厂商正在通过生态合作锁定渠道"})
        return out

    def build_prompt(self, state: AgentState, context: str) -> str:
        return (
            f"行业={state.industry}\n竞品与专利信号：\n{context}\n\n"
            "输出竞争格局 JSON：players / threat_score(0-1) / gaps / barriers(0-5)。"
        )

    def postprocess(self, output: dict[str, Any], state: AgentState) -> dict[str, Any]:
        output.setdefault("players", [])
        output.setdefault("gaps", [])
        output.setdefault("barriers", [])
        output.setdefault("threat_score", 0.4)
        if "barrier_score_5" not in output:
            output["barrier_score_5"] = scale_to_5(1 - float(output.get("threat_score", 0.4)))
        return output
