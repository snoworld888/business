"""Agent 基类与共享状态。

对齐技术文档 §5.2 Agent 通用结构：
  1 检索相关上下文 → 2 构建 Prompt → 3 调用 LLM → 4 解析输出 → 5 更新状态

设计要点：每个 Agent 必须同时实现
  · `rule()`：确定性规则实现（默认路径，可离线、可断言、可回归）
  · `build_prompt()`：Prompt 模板（切真实 LLM 时启用，规则实现作为校验基线）
两者产出同一个 JSON Schema，因此「换模型不改业务」。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.llm import COMPLEXITY_COMPLEX, COMPLEXITY_SIMPLE, llm
from app.services.rag import BaseRAG, RetrievedDoc

logger = logging.getLogger(__name__)

#: 信号类型的基础权重（与趋势侦察的排序口径一致）
_SIGNAL_TYPE_WEIGHT = {
    "policy": 1.0, "funding": 0.9, "patent": 0.8, "paper": 0.7,
    "news": 0.6, "bidding": 0.6, "recruit": 0.5, "social": 0.4,
}


def as_dict(value: Any) -> dict[str, Any]:
    """把「本应是对象」的值强制成 `dict`。

    真实大模型不保证类型：实测会把 `coverage` / `unit_economics` 这类本应是对象的
    字段返回成**字符串**，进而让规则基线 `rule()` 抛
    `'str' object has no attribute 'get'` —— 基线一崩，契约回填整条失效，
    最终表现为 `verdict` 丢失、E2E 挂在 `KeyError: 'label'`。
    """
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    """把「本应是数组」的值强制成 `list`（模型有时返回字符串或对象）。"""
    return value if isinstance(value, list) else []


def dicts_only(items: Any) -> list[dict[str, Any]]:
    """过滤出列表里的 dict 元素 —— 模型可能返回 `["痛点A","痛点B"]` 这种字符串数组。"""
    return [x for x in as_list(items) if isinstance(x, dict)]


def signal_weight(sig: SignalView) -> float:
    """信号权重：类型基础分 + 融资额量级加成。"""
    base = _SIGNAL_TYPE_WEIGHT.get(sig.signal_type, 0.4)
    amount = float((sig.metrics or {}).get("amount", 0) or 0)
    if amount <= 0:
        return base
    return base + min(1.0, amount / 1e9) * 0.3


def build_retrieval_query(state: AgentState) -> str:
    """把 `state.query` 补成一个**有语义价值**的检索串。

    背景（实测教训）：`run_analysis` 在只传 `industry` 时会把行业分类标签
    （如 `"AI+制造"`）当成查询词。这类标签是**分类体系里的键、不是自然语言**，
    实测在 RAGFlow 上命中数为 **0**（相似度全部低于阈值）⇒ Agent 拿到零证据
    ⇒ 真实大模型会直接编造内容。

    因此：若查询串过短或与行业标签高度重叠，就用**行业 + 本地信号里最高频的
    技术关键词/标题**拼一个更有效的查询。
    """
    raw = (state.query or "").strip()
    industry = (state.industry or "").strip()
    # 查询与行业标签相同（或仅差分隔符）时，说明它没有额外语义
    degenerate = (not raw) or (raw == industry) or (
        raw.replace("+", "").replace(" ", "") == industry.replace("+", "").replace(" ", ""))
    if not degenerate:
        return raw

    # 注意：**不要把行业标签本身放进检索串**。
    # 实测 `"AI+制造"` 在 RAGFlow（Elasticsearch query_string）上会被解析成
    # 「必须同时包含 AI 与 制造」，命中数直接归零；即使拼上高质量关键词也照样 0 命中。
    # 行业范围在 SQL 层已经过滤过（`Signal.industry == industry`），检索串不需要它。
    keywords: list[str] = []
    seen: set[str] = set()
    for sig in sorted(state.signals, key=signal_weight, reverse=True):
        for kw in (sig.keywords or []):
            kw = str(kw).strip()
            if kw and kw not in seen:
                seen.add(kw)
                keywords.append(kw)
        if len(keywords) >= 8:
            break
    if keywords:
        return " ".join(keywords)
    # 没有关键词可用时，退回「去掉检索元字符的行业标签」
    return re.sub(r"[+\-&|!(){}\[\]^\"~*?:\\/]", " ", industry or raw).strip() \
        or raw or "综合赛道分析"


# ------------------------------------------------------------------ 状态
@dataclass
class SignalView:
    """信号的只读视图（与 ORM 解耦，便于测试）。"""

    id: str
    signal_type: str
    source: str
    title: str
    content: str = ""
    url: str | None = None
    industry: str | None = None
    region: str | None = None
    keywords: list[str] = field(default_factory=list)
    entities: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    sentiment: float = 0.0
    published_at: datetime | None = None

    @classmethod
    def from_orm(cls, obj) -> "SignalView":
        return cls(
            id=obj.id, signal_type=obj.signal_type, source=obj.source, title=obj.title,
            content=obj.content or "", url=obj.url, industry=obj.industry,
            region=obj.region, keywords=list(obj.keywords or []),
            entities=dict(obj.entities or {}), metrics=dict(obj.metrics or {}),
            sentiment=float(obj.sentiment or 0.0), published_at=obj.published_at,
        )

    @property
    def text(self) -> str:
        return f"{self.title} {self.content}".strip()


@dataclass
class AgentState:
    """在编排图中传递的状态（对应 LangGraph 的 State）。"""

    query: str = ""
    industry: str | None = None
    region: str | None = None
    days: int = 90
    profile: dict[str, Any] = field(default_factory=dict)
    signals: list[SignalView] = field(default_factory=list)
    rag: BaseRAG | None = None
    results: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    # ---------------- 便捷访问器 ----------------
    def by_type(self, *types: str) -> list[SignalView]:
        wanted = set(types)
        return [s for s in self.signals if s.signal_type in wanted]

    def since(self, days: int | None = None) -> list[SignalView]:
        days = days or self.days
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        out = []
        for s in self.signals:
            ts = s.published_at
            if ts is None:
                out.append(s)
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                out.append(s)
        return out


# ------------------------------------------------------------------ 基类
class BaseAgent:
    key: str = "base"
    name: str = "基础 Agent"
    description: str = ""
    complexity: str = COMPLEXITY_COMPLEX
    #: 检索证据条数（RAG 命中数与本地回退条数共用）
    top_k: int = 6
    #: **契约必需字段**：这些键若缺失或为空（`None` / `""` / `[]` / `{}`），
    #: 一律用 `rule()` 结果回填。
    #: 只列「空了会直接破坏下游」的键 —— 其余键尊重模型给出的显式空值，
    #: 因为显式空值也是模型的真实结论，覆盖它等于替模型编内容。
    required_keys: tuple[str, ...] = ()

    # -------------------------------------------------- 必须实现
    def rule(self, state: AgentState) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def build_prompt(self, state: AgentState, context: str) -> str:  # pragma: no cover
        raise NotImplementedError

    # -------------------------------------------------- 模板方法
    def run(self, state: AgentState) -> dict[str, Any]:
        context = self.retrieve(state)
        prompt = self.build_prompt(state, context)
        output = llm.complete(
            prompt, system=self.system_prompt(), complexity=self.complexity,
            rule_engine=lambda: self.rule(state),
        )
        output = self.postprocess(output, state)
        output = self.backfill_contract(output, state)
        output.setdefault("agent", self.key)
        output.setdefault("agent_name", self.name)
        self.write_state(state, output)
        return output

    # -------------------------------------------------- 可覆写钩子
    def system_prompt(self) -> str:
        return f"你是赛道罗盘的{self.name}。{self.description} 严格输出 JSON，不要额外解释。"

    def retrieve(self, state: AgentState) -> str:
        """默认检索：优先 RAG 混合检索；**无命中时回退本地信号**。

        为什么必须有回退（实测教训）：换真实大模型后，一旦 context 为空，
        Agent 的 prompt 只剩几十个字符的模板，模型会**凭空编造**产业事实
        （实测输出中出现过无来源的厂商名与时间点）。
        因此这里保证「证据永远非空」——RAG 没给就用本地信号按权重补齐。
        """
        query = build_retrieval_query(state)
        docs: list[RetrievedDoc] = []
        if state.rag is not None:
            try:
                docs = list(state.rag.retrieve(query, top_k=self.top_k))
            except Exception as exc:  # 外部检索失败不应中断分析
                logger.warning("%s RAG 检索异常，回退本地信号: %s", self.key, exc)
                docs = []

        if not docs:
            docs = self._local_docs(state)
            if docs:
                logger.info("%s RAG 无命中（query=%r），回退本地信号 %d 条",
                            self.key, query, len(docs))

        if not docs:
            self._retrieved_docs = []
            return ""
        # 记录**本次**检索的证据（顺序与 prompt 里 `[E{i}]` 的编号严格一致）。
        # 模型只会回 `E1`/`E3` 这类编号，必须能反查回真实证据 —— 见
        # `resolve_evidence_refs()`。
        # ⚠️ 不能拿 `state.evidence` 当映射表：① 它是跨 Agent 累积的；
        #    ② 第 1 层 trend / demand 是**并行**执行的，编号会互相交错。
        self._retrieved_docs = list(docs)
        for doc in docs:
            state.evidence.append(doc.to_evidence(len(state.evidence) + 1))
        if state.rag is not None:
            return state.rag.build_context(docs)
        # 无 RAG 时的等价实现（与 BaseRAG.build_context 同格式）
        parts, used = [], 0
        for i, doc in enumerate(docs, start=1):
            block = f"[E{i}] ({doc.signal_type}/{doc.source}) {doc.title}\n{doc.text[:500]}"
            if used + len(block) > 4000:
                break
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts)

    def _local_docs(self, state: AgentState) -> list[RetrievedDoc]:
        """把本地信号视图转成 RetrievedDoc（RAG 无命中时的兜底证据）。"""
        ranked = sorted(state.signals, key=signal_weight, reverse=True)[: self.top_k]
        return [
            RetrievedDoc(
                signal_id=s.id, source=s.source, signal_type=s.signal_type,
                title=s.title, text=s.text or s.title, url=s.url,
                metadata={"industry": s.industry, "keywords": s.keywords or []},
            )
            for s in ranked
        ]

    #: 本次检索送给模型的证据（顺序 = prompt 里 `[E{i}]` 的编号）。
    #: 由 `retrieve()` 赋值，`resolve_evidence_refs()` 消费。
    _retrieved_docs: list[RetrievedDoc] | None = None

    def resolve_evidence_refs(self, items: Any) -> list[dict[str, Any]]:
        """把模型返回的证据**编号**（`E1` / `E3`）反查成本次检索的真实证据。

        为什么必须有（实测缺陷，2026-09-14）
        ------------------------------------
        LLM 路径的 prompt 只要求模型回编号（`"evidence":["E1"]`），而落库时
        **直接存了模型的原始输出**（`analysis.py:172` / `opportunities.py:237`
        两处都是 `existing.evidence = t.get("evidence", [])`）
        ⇒ `trends.evidence` 里全是 `["E1","E3","E4"]` 这类字符串
        ⇒ 前端 `Trends.tsx` 取 `e.title` / `e.source` / `e.url` **全部落空**
        ⇒ 证据链渲染成一排 `—`（无标题、无来源、无链接）。

        映射表是 `self._retrieved_docs` —— **不是** `state.evidence`：
        · `state.evidence` 跨 Agent 累积，编号被 `len()+1` 续写；
        · 第 1 层 trend / demand 并行执行，两边 append 交错。
        只有 Agent 自己刚检索的那批 docs，其顺序才与 prompt 里的 `[E{i}]` 严格一致
        （`build_context` 与无 RAG 分支都是 `enumerate(docs, start=1)`）。

        规则实现（`rule()`）产出的是含 `signal_id` 的 dict 列表，因此：
        · `dict` 原样透传（规则路径 / 已是完整证据）
        · 字符串编号按本次检索顺序反查
        · **反查不到的一律丢弃** —— 模型可能编出不存在的编号，
          宁可少一条证据，也不让前端渲染空条目
        """
        by_ref: dict[str, dict[str, Any]] = {
            f"E{i}": d.to_evidence(i)
            for i, d in enumerate(self._retrieved_docs or [], start=1)
        }
        out: list[dict[str, Any]] = []
        for item in as_list(items):
            if isinstance(item, dict):
                out.append(item)
                continue
            hit = by_ref.get(str(item).strip().upper())
            if hit is not None:
                out.append(hit)
        return out

    def postprocess(self, output: dict[str, Any], state: AgentState) -> dict[str, Any]:
        return output

    def backfill_contract(self, output: dict[str, Any], state: AgentState) -> dict[str, Any]:
        """把 LLM 输出**对齐到 `rule()` 的契约**：缺失字段用规则结果回填。

        本文档开头的设计写的是「切真实 LLM 时，规则实现作为校验基线」，但此前
        只在少数 Agent 里弱覆盖。实测切到 qwen3.8:27b 后发现模型**不保证输出全部字段**：

        · `trends[].score` 缺失 ⇒ `persist_scan_result` 里 `float(None or 0)` 把趋势评分
          **写成 0.0**，雷达页排序全部垫底（等于扫不出来）
        · `decision.verdict` 缺失 ⇒ 结论标签为空

        这些都是**下游契约字段**（落库 / 评分 / 前端），必须由基线兜住。
        单独放在 `run()` 里调用，而不是塞进 `postprocess()` —— 因为各子类都重写了
        `postprocess()` 且都没有调用 `super()`。
        """
        if not output:
            return output
        try:
            baseline = self.rule(state)
        except Exception as exc:  # 基线失败不应影响主输出
            logger.warning("%s 规则基线生成失败，跳过回填: %s", self.key, exc)
            return output

        filled: list[str] = []
        for key, value in baseline.items():
            # 默认只回填**真正缺失**的键（不存在或为 None）；
            # 显式的 `[]` / `{}` 通常代表模型的真实"空结果"，不覆盖。
            missing = key not in output or output[key] is None
            # 但契约必需字段一旦为空就必须兜住 —— 实测模型会给出 `verdict: {}`，
            # 让 `/opportunities/{id}/score` 的 `verdict.label` 直接 KeyError。
            if key in self.required_keys and output.get(key) in (None, "", [], {}):
                missing = True
            if missing:
                output[key] = value
                filled.append(key)
        filled += self._backfill_items(output, baseline)
        if filled:
            output.setdefault("_meta", {})["baseline_backfill"] = sorted(set(filled))
        return output

    @staticmethod
    def _backfill_items(output: dict[str, Any],
                        baseline: dict[str, Any]) -> list[str]:
        """按 `name` 对齐列表元素，只补空缺字段、不覆盖模型给出的值。"""
        filled: list[str] = []
        for key, base_items in baseline.items():
            if not isinstance(base_items, list):
                continue
            out_items = output.get(key)
            if not isinstance(out_items, list):
                continue
            by_name = {str(b.get("name")): b for b in base_items
                       if isinstance(b, dict) and b.get("name")}
            for item in out_items:
                if not isinstance(item, dict):
                    continue
                ref = by_name.get(str(item.get("name")))
                if not ref:
                    continue
                for field_name, value in ref.items():
                    if field_name not in item or item[field_name] is None:
                        item[field_name] = value
                        filled.append(f"{key}[].{field_name}")
        return filled

    def write_state(self, state: AgentState, output: dict[str, Any]) -> None:
        state.results[self.key] = output
        meta = output.get("_meta") or {}
        provider = meta.get("provider", "rule")
        if provider == "rule" or str(provider).endswith("-fallback"):
            engine = "rule-engine"
        else:
            engine = "llm"
        state.trace.append({
            # 唯一序号：反馈循环会**重跑** trend / demand，同一 `agent` 会出现多次
            # （实测 trace 14 条 = 7 个 Agent × 2 轮）⇒ `agent` 不能当列表 key
            # （前端曾因此报 "Each child in a list should have a unique key prop"）。
            "seq": len(state.trace) + 1,
            "agent": self.key,
            "agent_name": self.name,
            "provider": provider,
            "model": meta.get("model"),
            "engine": engine,
            "cached": bool(meta.get("cached")),
            "latency_ms": meta.get("latency_ms", 0),
            "prompt_chars": meta.get("prompt_chars", 0),
            "error": meta.get("error"),
        })


# ------------------------------------------------------------------ 工具函数
CN_PAIN_PATTERNS: dict[str, dict[str, Any]] = {
    "检测精度低": {"patterns": ["漏检", "误检", "精度不够", "准确率低", "识别错", "良率误判"],
                   "willingness": 0.7, "urgency": 0.6},
    "部署成本高": {"patterns": ["太贵", "用不起", "成本高", "预算不够", "投入大", "性价比低"],
                   "willingness": 0.8, "urgency": 0.4},
    "交付周期长": {"patterns": ["交付慢", "周期长", "上线慢", "排期久", "响应慢", "实施久"],
                   "willingness": 0.5, "urgency": 0.8},
    "数据孤岛": {"patterns": ["数据孤岛", "系统不互通", "接口不开放", "数据打不通", "无法集成"],
                 "willingness": 0.6, "urgency": 0.5},
    "人工复检多": {"patterns": ["人工复检", "还得人工", "靠人盯", "人力投入", "依赖老师傅"],
                   "willingness": 0.6, "urgency": 0.6},
    "维护成本高": {"patterns": ["维护贵", "运维复杂", "调参难", "不稳定", "经常坏"],
                   "willingness": 0.5, "urgency": 0.6},
    "兼容性差": {"patterns": ["不兼容", "适配难", "改造量大", "老设备", "产线改造"],
                 "willingness": 0.5, "urgency": 0.5},
    "效果不可解释": {"patterns": ["不可解释", "黑盒", "不敢用", "无法追溯", "说不清"],
                     "willingness": 0.4, "urgency": 0.4},
    "合规风险": {"patterns": ["合规", "审计", "数据出境", "安全审查", "资质"],
                 "willingness": 0.5, "urgency": 0.7},
    "人才短缺": {"patterns": ["招不到", "人才缺", "缺算法", "团队不足", "依赖外包"],
                 "willingness": 0.4, "urgency": 0.6},
}

PRICE_HINTS = ["价格", "报价", "预算", "多少钱", "万元", "成本可控", "付费", "采购"]
URGENCY_HINTS = ["急需", "尽快", "紧急", "本月", "季度内", "立刻", "刻不容缓", "deadline"]
NEGATIVE_HINTS = ["不行", "差", "问题", "痛点", "难", "慢", "贵", "低", "失败", "投诉", "不满意",
                  "麻烦", "头疼", "困扰"]

TRL_HINTS = [
    (2, ["概念", "论文", "原理"]),
    (3, ["实验室", "原型", "验证"]),
    (4, ["样机", "测试环境"]),
    (5, ["中试", "试点", "相关环境"]),
    (6, ["示范", "小批量"]),
    (7, ["量产", "上线", "真实环境"]),
    (8, ["规模化", "认证", "标准"]),
    (9, ["成熟", "普及", "标配"]),
]


def hit_any(text: str, words: list[str]) -> int:
    return sum(1 for w in words if w in text)


def negative_ratio(texts: list[str]) -> float:
    if not texts:
        return 0.0
    hits = sum(1 for t in texts if hit_any(t, NEGATIVE_HINTS))
    return round(hits / len(texts), 4)


def willingness_ratio(texts: list[str]) -> float:
    if not texts:
        return 0.0
    return round(sum(1 for t in texts if hit_any(t, PRICE_HINTS)) / len(texts), 4)


def urgency_ratio(texts: list[str]) -> float:
    if not texts:
        return 0.0
    return round(sum(1 for t in texts if hit_any(t, URGENCY_HINTS)) / len(texts), 4)


def growth_rate(signals: list[SignalView]) -> float:
    """近半窗 vs 远半窗的信号增速，返回 [-1, +∞) 的比率。"""
    dated = [s for s in signals if s.published_at is not None]
    if len(dated) < 4:
        return 0.0
    ordered = sorted(dated, key=lambda s: s.published_at)  # type: ignore[arg-type]
    half = len(ordered) // 2
    older, recent = len(ordered[:half]) or 1, len(ordered[half:])
    return round((recent - older) / older, 4)


TECH_VOCAB = {
    "人工智能", "机器学习", "深度学习", "计算机视觉", "机器人", "具身智能", "大模型",
    "边缘计算", "数字孪生", "工业互联网", "预测性维护", "智能质检", "智能排产",
    "自动驾驶", "芯片", "传感器", "储能", "光伏", "氢能", "固态电池", "新材料",
    "脑机接口", "量子计算", "6G", "卫星互联网", "生物医药", "医疗器械", "合成生物",
    # ---- 2026-09-14 补充：前沿 AI 方向（此前缺失，导致「智能体」等词无法被抽取）----
    "智能体", "多智能体", "大语言模型", "语言模型", "生成式AI", "多模态",
    "强化学习", "知识图谱", "向量数据库", "检索增强", "推理优化", "模型微调",
    "算力", "智算中心", "AI芯片", "工业软件", "数字工厂", "智能制造",
}


#: 「单字母 + 数字」形如 `E1` / `A2` —— 是**证据编号**，不是技术词。
#: LLM 生成的趋势描述会引用它们（如「结合政策补贴（E3, E4）…」），必须排除。
_ASCII_CODE_REF = re.compile(r"^[A-Za-z]\d+$")

#: 英文停用词 / URL 与文献格式片段 —— 会被英文正则捞进来但不是技术词。
#: 实测污染源：`https`、`kr.com`、`of`、`in`、`and`、`Zhang`（作者名）等。
_ASCII_STOPWORDS = {
    "https", "http", "www", "com", "cn", "org", "net", "html", "htm", "pdf",
    "doi", "journal", "review", "of", "the", "and", "for", "with", "from",
    "using", "based", "towards", "into", "via", "on", "to", "in", "at", "by",
    "vol", "no", "pp", "ed", "et", "al", "its", "new", "an", "is", "are",
    "it", "id", "top", "top10",
    # 职位 / 融资轮次 / 企业缩写 —— 常出现在新闻正文，但不是技术词
    "ceo", "cto", "cfo", "coo", "cmo", "vp", "hr",
    "pre-a", "pre-b", "pre-c", "pre-ipo", "ipo", "vc", "pe", "fa", "esg",
}

#: 英文技术词白名单（驼峰式不适用、但确实是技术词的那些）
EN_TECH_VOCAB = {
    "llm", "rag", "yolo", "bert", "gpt", "transformer", "pytorch", "tensorflow",
    "opencv", "resnet", "slam", "lidar", "fpga", "asic", "cnn", "rnn", "gan",
    "vit", "ner", "ocr", "clip", "lora", "moe", "kv", "triton", "cuda",
}


def _looks_like_tech_ascii(term: str) -> bool:
    """判断一个英文串是否值得当作技术关键词。

    背景（实测教训 2026-09-15）：论文类信号的正文是**英文摘要**，
    朴素地「抽出所有英文单词」会把 `Rough` / `Sets` / `Military` /
    `Technology` 这类普通词收进 keywords，再经 `trend_tags` 传染到趋势标签上
    （实测 tags 出现 `'Military Technology and Strategies'`）。

    收紧为四类白名单：词表命中 · 含数字的型号 · 全大写缩写 · 驼峰式命名。
    """
    low = term.lower()
    if _ASCII_CODE_REF.match(term):
        return False                      # E1 / A2 —— 证据编号
    if low in _ASCII_STOPWORDS:
        return False                      # https / of / journal / Zhang 类噪声
    if low in EN_TECH_VOCAB:
        return True                       # 显式白名单
    if any(ch.isdigit() for ch in term):
        return True                       # 6G / BERT-base / GS-MGF-LPLs
    if term.isupper() and 2 <= len(term) <= 10:
        return True                       # LLM / RAG / YOLO / IT
    if sum(1 for ch in term if ch.isupper()) >= 2:
        return True                       # OpenCV / TensorFlow / PyTorch
    return False


def extract_tech_keywords(text: str, limit: int = 8) -> list[str]:
    """轻量实体抽取：领域词表子串匹配 + 英文技术词。

    中文使用子串扫描而非分词，避免 jieba 分词边界把「计算机视觉」切碎；
    生产环境可替换为 NER 模型（技术文档 §3.2 实体抽取）。
    """
    if not text:
        return []
    hits = [term for term in TECH_VOCAB if term in text]

    ascii_terms = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,20}", text)
    alias = {"ai": "人工智能", "cv": "计算机视觉", "llm": "大模型",
             "nlp": "人工智能", "agv": "机器人", "6g": "6G"}
    merged: list[str] = []
    for term in hits + ascii_terms:
        mapped = alias.get(term.lower(), term)
        if mapped not in TECH_VOCAB:
            # 英文词必须通过形态白名单（见 `_looks_like_tech_ascii`）：
            # 此前只要求「长度>1 且是 ascii」，会把英文摘要里的普通词
            # （Rough / Sets / Military / Technology）与 URL 片段（https / kr.com）
            # 一并收进来，再传染到趋势标签。
            if not _looks_like_tech_ascii(mapped):
                continue
        if mapped not in merged:
            merged.append(mapped)

    # ⚠️ 这里**不再兜底切分**（实测教训 2026-09-15）：
    # 原实现用 `re.findall(r"[\u4e00-\u9fff]{2,4}", text)` 按**固定 4 字窗口**切分，
    # 完全不管词边界 —— 专利标题「面向小样本的工业缺陷识别模型训练方法」
    # 被切成 `小样本工` / `业缺陷识` / `别模型训` / `练方法` 这类碎片，
    # 它们既匹配不到任何信号，又会当作「能力词」显示在画像面板里污染 UI。
    # 调用方（`trend.capability_terms`）已有 `or [text]` 兜底，返回空即可。
    return merged[:limit]


# ================================================================ 专利标题抽词
#: 专利标题套话前缀 —— 剥离后剩下的才是技术实质
_PATENT_NOISE_PREFIX = (
    "一种", "一类", "一款", "基于", "面向", "用于", "采用", "具有", "新型",
    "高效", "智能", "及其", "以及", "一种基于", "一种面向",
)

#: 专利标题套话后缀 —— 都是"载体"而非"技术"
_PATENT_NOISE_SUFFIX = (
    "的方法", "的系统", "的装置", "的设备", "的技术", "的方案", "的工艺",
    "方法", "系统", "装置", "设备", "技术", "方案", "工艺", "平台", "产品",
    "介质", "程序", "终端", "服务器", "结构", "研究", "应用", "设计", "用途",
)

#: 中文技术名词的**动作后缀** —— 技术词几乎都以其中一个收尾
#: （缺陷**识别**、目标**检测**、模型**训练**、路径**优化**、成本**预测**…）
_TECH_ACTION_SUFFIX = (
    "识别", "检测", "训练", "调度", "优化", "预测", "控制", "感知", "建模",
    "分割", "跟踪", "推理", "部署", "协同", "定位", "测量", "补偿", "标注",
    "检索", "排序", "仿真", "标定", "评估", "规划", "决策", "监控", "预测",
)

#: 短语边界 —— 跨过这些字符的组合没有语义，先切段再抽词
_PATENT_BOUNDARY_RE = re.compile(r"[的及与和或、,，；;:：（）()\[\]【】「」\-—_/\s]+")

#: 动作后缀的独立匹配器（只用于定位后缀出现的位置）
_PATENT_ACTION_RE = re.compile("|".join(dict.fromkeys(_TECH_ACTION_SUFFIX)))

#: **高频词尾字** —— 出现在这些字后面时，说明 2-4 字窗口已经跨进了上一个词。
#: 例：「视觉引导机器人抓取定位」若直接取 4 字窗口会得到「器人抓取定位」，
#: 首字「器」是词尾字 ⇒ 逐字回退到「抓取定位」。
#: 这是无 NER 情况下的启发式（见《遗留项台账》L-07），不是通用解法。
_WORD_TAIL_CHARS = frozenset("人型性化度量率力层统器机备法点体系面端域物种群")


def _action_terms(core: str) -> list[str]:
    """在切段后的核心串里抽「技术对象 + 动作后缀」短语。

    两个关键约束（都是实测踩出来的）：
    ① **前缀不得跨过上一个后缀** —— 否则「工业缺陷识别模型训练」会抽出
       「识别模型训练」这种把前一个词吃进来的残片；
    ② **首字为词尾字则回退到 2 字** —— 否则「视觉引导机器人抓取定位」
       会抽出「器人抓取定位」。
    """
    terms: list[str] = []
    prev_end = 0
    for m in _PATENT_ACTION_RE.finditer(core):
        start = m.start()
        if start - prev_end < 2:            # 与前一个后缀挨太近，跳过
            prev_end = m.end()
            continue
        prefix = core[max(prev_end, start - 4):start]
        while len(prefix) > 2 and prefix[0] in _WORD_TAIL_CHARS:
            prefix = prefix[1:]
        terms.append(prefix + m.group(0))
        prev_end = m.end()
    return terms


def extract_patent_terms(text: str, limit: int = 6) -> list[str]:
    """从**专利标题**抽取可用技术词。

    ## 为什么单独做

    专利标题在 `TECH_VOCAB` 上的命中率很低。实测两条真实专利：

    | 专利标题 | 词表命中 | 原实现结果 |
    | --- | --- | --- |
    | 基于深度学习的表面缺陷检测方法 | `['深度学习']` | 无增量（技术栈里已有） |
    | 小样本工业缺陷识别模型训练方法 | **`[]`** | **整句当能力词** ⇒ 全库 36 个趋势标签命中 **0** |

    ⇒ 专利**名义上参与**能力匹配，**实际贡献恒为 0**。

    抽不出词时**不能丢弃**（专利是真实能力证据，应出现在分析依据里），
    也不能退回整句（与任何短标签都配不上）。这里按中文技术名词的构词规律抽：

      ① 词表命中（术语最稳定，展示也最友好）
      ② 「2-4 字技术对象 + 动作后缀」—— 构词法：`工业缺陷`+`识别`、`模型`+`训练`
      ③ 去套话后剩余的**短核心**（≤8 字且以动作后缀收尾）—— 如「多目标优化」

    全程 0 新增依赖、确定性可复算。
    """
    if not text:
        return []
    out: list[str] = []

    def _add(value: str) -> None:
        v = str(value).strip()
        if 2 <= len(v) <= 12 and v not in out:
            out.append(v)

    for term in extract_tech_keywords(text, limit=limit):
        _add(term)

    # 按边界切段，避免「学习的表面缺陷检测」这类跨边界组合
    for segment in _PATENT_BOUNDARY_RE.split(text):
        core = segment.strip()
        if not core:
            continue
        # 剥离套话前/后缀（可能叠加，如「一种…方法」）
        changed = True
        while changed and core:
            changed = False
            for pre in _PATENT_NOISE_PREFIX:
                if core.startswith(pre) and len(core) > len(pre):
                    core, changed = core[len(pre):], True
                    break
            for suf in _PATENT_NOISE_SUFFIX:
                if core.endswith(suf) and len(core) > len(suf):
                    core, changed = core[: -len(suf)], True
                    break
        if not core:
            continue

        terms = _action_terms(core)
        for term in terms:
            _add(term)

        # ③ 整个核心本身就是「…动作」时才直接采用（如「多目标优化」「缺陷识别」）。
        # 仅当前缀短语没抽出东西时才走 —— 否则会与前缀短语重复
        # （实测「大模型推理优化」同时产出「大模型推理」与「大模型推理优化」）。
        if not terms and 3 <= len(core) <= 8 and core.endswith(_TECH_ACTION_SUFFIX):
            _add(core)

    return out[:limit]

