"""七个 AI Agent 与编排器。"""

from app.agents.base import AgentState, BaseAgent, SignalView
from app.agents.competition import CompetitionIntelAgent
from app.agents.decision import DecisionSynthesisAgent
from app.agents.demand import DemandDiscoveryAgent
from app.agents.market import MarketSizingAgent
from app.agents.monitor import MonitorAlertAgent, RULES, apply_calibration
from app.agents.orchestrator import Orchestrator, RunResult, analyze, default_workflow
from app.agents.tech import TechAssessmentAgent
from app.agents.trend import TrendReconAgent

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "trend": TrendReconAgent,
    "demand": DemandDiscoveryAgent,
    "market": MarketSizingAgent,
    "competition": CompetitionIntelAgent,
    "tech": TechAssessmentAgent,
    "decision": DecisionSynthesisAgent,
    "monitor": MonitorAlertAgent,
}

AGENT_CATALOG = [
    {"key": "trend", "name": "趋势侦察 Agent", "responsibility": "每日扫描 PESTEL、技术拐点、政策红利",
     "inputs": ["政策", "论文", "专利", "新闻"], "outputs": ["趋势列表", "顺风/逆风", "置信度"]},
    {"key": "demand", "name": "需求挖掘 Agent", "responsibility": "聚类社媒/评论/招标/招聘中的痛点，输出需求强度",
     "inputs": ["社媒", "评论", "招标", "招聘"], "outputs": ["痛点聚类", "需求强度", "验证方案"]},
    {"key": "market", "name": "市场测算 Agent", "responsibility": "自下而上+自上而下测算 TAM/SAM/SOM",
     "inputs": ["行业数据", "财报", "融资"], "outputs": ["TAM/SAM/SOM", "增长率", "假设清单"]},
    {"key": "competition", "name": "竞争情报 Agent", "responsibility": "竞品动态、大厂威胁、替代方案、供应链卡点",
     "inputs": ["竞品", "专利", "招聘"], "outputs": ["竞争格局", "威胁评分", "空白点"]},
    {"key": "tech", "name": "技术评估 Agent", "responsibility": "TRL/CRL/BRL/IPRL/TMRL/FRL 六维评分",
     "inputs": ["技术文档", "专利"], "outputs": ["六维评分", "最弱维度"]},
    {"key": "decision", "name": "决策综合 Agent", "responsibility": "综合评分，生成立项/投资报告",
     "inputs": ["各 Agent 输出"], "outputs": ["综合评分", "决策报告", "冲突点"]},
    {"key": "monitor", "name": "监控预警 Agent", "responsibility": "跟踪关键信号，触发变相时刻提醒",
     "inputs": ["信号更新"], "outputs": ["预警通知", "复盘建议", "校准建议"]},
]

__all__ = [
    "AgentState", "BaseAgent", "SignalView", "TrendReconAgent", "DemandDiscoveryAgent",
    "MarketSizingAgent", "CompetitionIntelAgent", "TechAssessmentAgent",
    "DecisionSynthesisAgent", "MonitorAlertAgent", "Orchestrator", "RunResult",
    "analyze", "default_workflow", "RULES", "apply_calibration",
    "AGENT_REGISTRY", "AGENT_CATALOG",
]
