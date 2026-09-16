"""知识图谱适配器。

记忆图（NetworkX）—— 默认实现，零依赖，支持技术文档 §3.3.2 的全部
节点标签与关系类型，以及 §4.3 的空白点识别图算法。
Neo4j 适配器 —— 生产实现，Cypher 语句与技术文档保持一致。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import networkx as nx

from app.core.config import settings

logger = logging.getLogger(__name__)

NODE_LABELS = [
    "Company", "Technology", "Product", "Track", "Opportunity",
    "Patent", "Funding", "Talent", "Policy", "Signal",
]

RELATION_TYPES = [
    "OWNS", "APPLIES_TO", "CONTAINS", "DEPENDS_ON", "IMPACTS",
    "COMPETES_WITH", "BELONGS_TO", "INVESTS_IN", "VERIFIES",
]


class BaseGraphStore:
    def add_node(self, label: str, key: str, **props) -> None:  # pragma: no cover
        raise NotImplementedError

    def add_edge(self, src_label: str, src_key: str, rel: str,
                 dst_label: str, dst_key: str, **props) -> None:  # pragma: no cover
        raise NotImplementedError

    def neighbors(self, label: str, key: str, rel: str | None = None,
                  depth: int = 1, direction: str = "out") -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def blank_spots(self, threshold: int = 5) -> list[dict]:  # pragma: no cover
        raise NotImplementedError

    def to_cytoscape(self) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError


class MemoryGraphStore(BaseGraphStore):
    """内存图。节点 id 形如 `Track::AI+制造`，属性字典可挂任意业务字段。"""

    def __init__(self) -> None:
        self.g = nx.MultiDiGraph()

    @staticmethod
    def nid(label: str, key: str) -> str:
        return f"{label}::{key}"

    def add_node(self, label: str, key: str, **props) -> None:
        self.g.add_node(self.nid(label, key), label=label, key=key, **props)

    def add_edge(self, src_label, src_key, rel, dst_label, dst_key, **props) -> None:
        s, d = self.nid(src_label, src_key), self.nid(dst_label, dst_key)
        if s not in self.g:
            self.add_node(src_label, src_key)
        if d not in self.g:
            self.add_node(dst_label, dst_key)
        self.g.add_edge(s, d, rel=rel, **props)

    def neighbors(self, label, key, rel=None, depth=1,
                  direction: str = "out") -> dict[str, Any]:
        """遍历邻居。direction: out（默认，等价 Cypher `(a)-[*]->(b)`）/ in / both。"""
        start = self.nid(label, key)
        if start not in self.g:
            return {}

        def edges_of(node: str):
            if direction == "in":
                return [(src, data) for src, _, data in self.g.in_edges(node, data=True)]
            if direction == "both":
                return ([(dst, data) for _, dst, data in self.g.out_edges(node, data=True)]
                        + [(src, data) for src, _, data in self.g.in_edges(node, data=True)])
            return [(dst, data) for _, dst, data in self.g.out_edges(node, data=True)]

        nodes: dict[str, dict] = {}
        frontier = {start}
        for _ in range(depth):
            nxt: set[str] = set()
            for n in frontier:
                for other, data in edges_of(n):
                    if rel and data.get("rel") != rel:
                        continue
                    nodes[other] = dict(self.g.nodes[other], relation=data.get("rel"))
                    nxt.add(other)
            frontier = nxt
        return {k: v for k, v in nodes.items() if k != start}

    def blank_spots(self, threshold: int = 5) -> list[dict]:
        """连接度低的赛道节点 = 空白点（技术文档 §4.3 Cypher 的等价实现）。"""
        out: list[dict] = []
        for node, data in self.g.nodes(data=True):
            if data.get("label") != "Track":
                continue
            degree = self.g.degree(node)
            if degree < threshold:
                out.append({"name": data.get("key"), "degree": degree,
                            "industry": data.get("industry")})
        return sorted(out, key=lambda x: x["degree"])

    def centrality(self, top: int = 10) -> list[dict]:
        if self.g.number_of_nodes() == 0:
            return []
        cent = nx.degree_centrality(self.g)
        ranked = sorted(cent.items(), key=lambda x: x[1], reverse=True)[:top]
        # `name` 必须是**可读名** —— 前端"中心性排名"表直接用它渲染，
        # 原实现给的是 key（Signal / Opportunity 为 UUID）⇒ 表里全是 UUID。
        return [{"name": self.g.nodes[n].get("title") or self.g.nodes[n].get("key"),
                 "key": self.g.nodes[n].get("key"),
                 "label": self.g.nodes[n].get("label"),
                 "centrality": round(v, 4)} for n, v in ranked]

    def communities(self) -> list[list[str]]:
        """社区发现（技术文档 §4.3：稀疏区域识别）。"""
        undirected = nx.Graph(self.g)
        if undirected.number_of_nodes() == 0:
            return []
        try:
            groups = nx.community.greedy_modularity_communities(undirected)
        except Exception:  # pragma: no cover
            return []
        return [[self.g.nodes[n].get("key", n) for n in grp] for grp in groups]

    def to_cytoscape(self) -> dict[str, Any]:
        nodes = [
            {"data": {
                "id": n,
                # 显示名必须**可读**：`key` 对 Signal / Opportunity 是 UUID，
                # 直接当 label 会让图谱上出现 64 个 36 位 UUID（实测缺陷，
                # 用户反馈"知识图谱里有很多非文字字符串"）。
                # 可读名优先取 title（信号标题 / 机会标题），退回 name / key。
                "label": d.get("title") or d.get("name") or d.get("key"),
                "type": d.get("label"),
                # `key` 单独带上：原实现把它并进 props 又被 exclude 掉，
                # 前端拿不到，无法按 key 下钻。
                "key": d.get("key"),
                **{k: v for k, v in d.items() if k not in {"key", "label"}},
            }}
            for n, d in self.g.nodes(data=True)
        ]
        edges = [
            {"data": {"id": f"e{i}", "source": s, "target": t, "label": d.get("rel")}}
            for i, (s, t, d) in enumerate(self.g.edges(data=True))
        ]
        return {"nodes": nodes, "edges": edges,
                "legend": {"node_types": NODE_LABELS, "edge_types": RELATION_TYPES}}


class Neo4jGraphStore(BaseGraphStore):  # pragma: no cover - 需 Neo4j 服务
    def __init__(self, uri: str, user: str, password: str):
        from neo4j import GraphDatabase  # type: ignore

        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def add_node(self, label, key, **props):
        with self.driver.session() as s:
            s.run(f"MERGE (n:{label} {{key: $key}}) SET n += $props", key=key, props=props)

    def add_edge(self, src_label, src_key, rel, dst_label, dst_key, **props):
        cypher = (
            f"MERGE (a:{src_label} {{key: $sk}}) "
            f"MERGE (b:{dst_label} {{key: $dk}}) "
            f"MERGE (a)-[r:{rel}]->(b) SET r += $props"
        )
        with self.driver.session() as s:
            s.run(cypher, sk=src_key, dk=dst_key, props=props)

    def neighbors(self, label, key, rel=None, depth=1, direction="out"):
        arrow = "->" if direction == "out" else ("<-" if direction == "in" else "-")
        pattern = f"-[{':' + rel if rel else ''}*1..{depth}]{arrow}"
        cypher = f"MATCH (a:{label} {{key: $key}}){pattern}(b) RETURN b, labels(b) AS l"
        with self.driver.session() as s:
            return {r["b"].get("key"): dict(r["b"], label=r["l"][0]) for r in s.run(cypher, key=key)}

    def blank_spots(self, threshold=5):
        cypher = (
            "MATCH (t:Track) OPTIONAL MATCH (t)-[r]-() WITH t, count(r) AS degree "
            "WHERE degree < $th RETURN t.key AS name, degree ORDER BY degree ASC"
        )
        with self.driver.session() as s:
            return [dict(r) for r in s.run(cypher, th=threshold)]

    def to_cytoscape(self):
        with self.driver.session() as s:
            nodes = [{"data": {"id": r["id"], "label": r["key"], "type": r["label"]}}
                     for r in s.run("MATCH (n) RETURN id(n) AS id, n.key AS key, "
                                    "head(labels(n)) AS label")]
            edges = []
            for i, r in enumerate(s.run(
                "MATCH (a)-[r]->(b) RETURN id(a) AS s, id(b) AS t, type(r) AS rel"
            )):
                edges.append({"data": {"id": f"e{i}", "source": r["s"],
                                       "target": r["t"], "label": r["rel"]}})
            return {"nodes": nodes, "edges": edges,
                    "legend": {"node_types": NODE_LABELS, "edge_types": RELATION_TYPES}}


_store: BaseGraphStore | None = None


def get_graph_store() -> BaseGraphStore:
    global _store
    if _store is None:
        if settings.GRAPH_BACKEND == "neo4j":
            try:
                _store = Neo4jGraphStore(
                    settings.NEO4J_URI, settings.NEO4J_USER, settings.NEO4J_PASSWORD
                )
                logger.info("图谱后端 = neo4j @ %s", settings.NEO4J_URI)
            except Exception as exc:
                logger.warning("Neo4j 不可用，降级内存图实现: %s", exc)
        if _store is None:
            _store = MemoryGraphStore()
    return _store


def reset_graph_store() -> None:
    global _store
    _store = None


def build_from_signals(db) -> MemoryGraphStore:
    """从信号与业务实体重建图谱（**真正幂等**）。"""
    from app.models import Opportunity, Signal, Trend

    # ⚠️ 必须先重置图存储（实测缺陷 2026-09-15）：
    # `get_graph_store()` 返回的是**全局单例**，不重置就会在旧图上继续
    # `add_node` / `add_edge` —— 函数虽然注释自称「幂等」，实际是**累加**：
    #   · 连续两次重建：节点 173 不变、但边数 792 → 986 持续增长
    #   · 已删除的趋势仍以 `Trend` 节点残留（库里 4 条，图上 17 个）
    #   · `blank_spots()` / `centrality()` 等图算法因此读到幽灵数据
    # 重置后每次重建都从空图开始，结果只取决于当前库内容。
    reset_graph_store()
    store = get_graph_store()
    label_counts: dict[str, int] = defaultdict(int)

    # 图谱是**全局共享**的（进程内单例），只纳入公共信号；
    # 租户私有信号不进全局图谱，避免跨租户泄露（见 `core.visibility`）。
    for sig in db.query(Signal).filter(Signal.tenant_id.is_(None)).all():
        store.add_node("Signal", sig.id, signal_type=sig.signal_type,
                       source=sig.source, title=sig.title, industry=sig.industry)
        label_counts["Signal"] += 1
        for tech in (sig.keywords or []):
            store.add_node("Technology", tech, industry=sig.industry)
            store.add_edge("Technology", tech, "IMPACTS", "Signal", sig.id)
        if sig.industry:
            store.add_node("Track", sig.industry, industry=sig.industry)
            store.add_edge("Signal", sig.id, "IMPACTS", "Track", sig.industry)
        for ent_kind, names in (sig.entities or {}).items():
            label = {"company": "Company", "patent": "Patent", "policy": "Policy",
                     "funding": "Funding", "talent": "Talent"}.get(ent_kind)
            if not label:
                continue
            for name in (names if isinstance(names, list) else [names]):
                store.add_node(label, str(name))
                store.add_edge(label, str(name), "BELONGS_TO", "Signal", sig.id)

    for tr in db.query(Trend).all():
        # ⚠️ 趋势**不是赛道**：原实现 `add_node("Track", tr.name)` 把趋势名也建成
        # Track 节点 ⇒ `blank_spots()`（筛 `label == "Track"`）会把趋势名当成
        # "赛道"返回，前端「空白点识别」表的**赛道**列因此显示趋势名
        # （实测：空白点 top3 = 智能质检 / 工业视觉与智能质检规模化落地 / …）。
        # 趋势单独作为 `Trend` 节点，并连到它所属的赛道。
        store.add_node("Trend", tr.name, industry=tr.industry, score=tr.score)
        if tr.industry:
            store.add_node("Track", tr.industry, industry=tr.industry)
            store.add_edge("Track", tr.industry, "HAS_TREND", "Trend", tr.name)

    for op in db.query(Opportunity).all():
        store.add_node("Opportunity", op.id, title=op.title, score=(op.score or {}).get("total"))
        if op.track_id:
            from app.models import Track

            track = db.get(Track, op.track_id)
            if track:
                store.add_node("Track", track.name, industry=track.industry)
                store.add_edge("Track", track.name, "CONTAINS", "Opportunity", op.id)

    logger.info("图谱重建完成: nodes=%s", store.g.number_of_nodes()
                if isinstance(store, MemoryGraphStore) else "n/a")
    return store  # type: ignore[return-value]
