"""RAG 引擎：切片 → 嵌入 → 检索 → 重排 → 引用。

对齐技术文档 §5.4 RAG 流程：
Query → Embedding → Milvus 检索 → Top-K → Rerank → Prompt 组装 → LLM → 引用来源 → 输出

本地实现使用「向量检索 + BM25 关键词检索」混合检索后融合排序（RRF），
与文档描述的混合检索策略一致。每个结论附带 source_id，前端可下钻。
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.core.config import settings
from app.services.embeddings import embed
from app.services.vector_store import VectorRecord, get_vector_store

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 400
DEFAULT_OVERLAP = 60


@dataclass
class RetrievedDoc:
    signal_id: str
    source: str
    signal_type: str
    title: str
    text: str
    url: str | None = None
    score: float = 0.0
    vector_score: float = 0.0
    keyword_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_evidence(self, index: int) -> dict[str, Any]:
        return {
            "ref": f"E{index}",
            "signal_id": self.signal_id,
            "source": self.source,
            "signal_type": self.signal_type,
            "title": self.title,
            "url": self.url,
            "excerpt": self.text[:160],
            "score": round(self.score, 4),
        }


def chunk_text(text: str, size: int = DEFAULT_CHUNK_SIZE,
               overlap: int = DEFAULT_OVERLAP) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return chunks


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]*|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    tokens = _TOKEN_RE.findall(text or "")
    grams: list[str] = list(tokens)
    for n in (2, 3):
        for i in range(len(tokens) - n + 1):
            grams.append("".join(tokens[i : i + n]))
    return [t.lower() for t in grams]


class BaseRAG:
    """与检索后端无关的后处理：上下文拼装与证据链完整率。

    `RAGEngine`（自建内存检索）与 `RAGFlowEngine`（外部 RAGFlow）都继承本类，
    保证两者对外行为一致 —— 这也是「换后端不改上层」的前提。
    """

    def build_context(self, docs: list[RetrievedDoc], max_chars: int = 4000) -> str:
        parts, used = [], 0
        for i, doc in enumerate(docs, start=1):
            block = f"[E{i}] ({doc.signal_type}/{doc.source}) {doc.title}\n{doc.text[:500]}"
            if used + len(block) > max_chars:
                break
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts)

    def citation_ratio(self, docs: list[RetrievedDoc]) -> float:
        """证据链完整率（技术文档 §9 AI 评测指标）。"""
        if not docs:
            return 0.0
        with_source = sum(1 for d in docs if d.url or d.signal_id)
        return round(with_source / len(docs), 4)


class RAGEngine(BaseRAG):
    """自建混合检索：向量 + BM25 → RRF 融合 + 引用生成。"""

    def __init__(self) -> None:
        self.store = get_vector_store()
        self._corpus: list[RetrievedDoc] = []
        self._df: Counter[str] = Counter()
        self._doc_tokens: list[Counter[str]] = []
        self._avg_len = 1.0

    # ------------------------------------------------------------ 索引
    def index(self, docs: Iterable[RetrievedDoc]) -> int:
        docs = list(docs)
        records, corpus, tokenized = [], [], []
        for doc in docs:
            vec = embed(f"{doc.title} {doc.text}")
            records.append(VectorRecord(
                id=doc.signal_id, embedding=vec, signal_type=doc.signal_type,
                source=doc.source, content=doc.text[:2000],
                metadata={"title": doc.title, "url": doc.url, **doc.metadata},
            ))
            corpus.append(doc)
            tokenized.append(Counter(tokenize(f"{doc.title} {doc.text}")))
        self.store.upsert(records)
        self._corpus = corpus
        self._doc_tokens = tokenized

        df: Counter[str] = Counter()
        for counter in tokenized:
            df.update(counter.keys())
        self._df = df
        self._avg_len = max(
            1.0, sum(sum(c.values()) for c in tokenized) / max(1, len(tokenized))
        )
        logger.info("RAG 索引完成：%d 篇文档", len(corpus))
        return len(corpus)

    # ------------------------------------------------------------ 检索
    def _bm25(self, query_tokens: list[str], index: int, k1: float = 1.5,
              b: float = 0.75) -> float:
        counter = self._doc_tokens[index]
        length = sum(counter.values()) or 1
        total_docs = max(1, len(self._corpus))
        score = 0.0
        for token in query_tokens:
            tf = counter.get(token, 0)
            if not tf:
                continue
            df = self._df.get(token, 0) or 1
            idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            score += idf * (tf * (k1 + 1)) / (
                tf + k1 * (1 - b + b * length / self._avg_len)
            )
        return score

    def retrieve(self, query: str, top_k: int = 8,
                 signal_type: str | None = None) -> list[RetrievedDoc]:
        if not self._corpus:
            return []
        query_vec = embed(query)
        vector_hits = self.store.search(query_vec, limit=max(top_k * 3, 20),
                                        signal_type=signal_type)
        id_to_index = {doc.signal_id: i for i, doc in enumerate(self._corpus)}

        vector_scores: dict[str, float] = {}
        for rec, score in vector_hits:
            if rec.id in id_to_index:
                vector_scores[rec.id] = max(0.0, float(score))

        query_tokens = tokenize(query)
        keyword_scores: dict[str, float] = {}
        for doc_id, idx in id_to_index.items():
            doc = self._corpus[idx]
            if signal_type and doc.signal_type != signal_type:
                continue
            s = self._bm25(query_tokens, idx)
            if s > 0:
                keyword_scores[doc_id] = s

        # RRF 融合排序
        rrf: dict[str, float] = {}
        for rank, (doc_id, _) in enumerate(
            sorted(vector_scores.items(), key=lambda x: x[1], reverse=True), start=1
        ):
            rrf[doc_id] = rrf.get(doc_id, 0.0) + 1.0 / (60 + rank)
        for rank, (doc_id, _) in enumerate(
            sorted(keyword_scores.items(), key=lambda x: x[1], reverse=True), start=1
        ):
            rrf[doc_id] = rrf.get(doc_id, 0.0) + 1.0 / (60 + rank)

        ranked = sorted(rrf.items(), key=lambda x: x[1], reverse=True)[:top_k]
        results: list[RetrievedDoc] = []
        for doc_id, fused in ranked:
            doc = self._corpus[id_to_index[doc_id]]
            doc.vector_score = round(vector_scores.get(doc_id, 0.0), 4)
            doc.keyword_score = round(keyword_scores.get(doc_id, 0.0), 4)
            doc.score = round(fused, 6)
            results.append(doc)

        if not results:  # 关键字与向量均无命中时，兜底返回同类型最新文档
            subset = [d for d in self._corpus if not signal_type or d.signal_type == signal_type]
            results = subset[:top_k]
        return results

    def count_total(self) -> int:
        """已索引文档数（用于判断是否需要重建索引）。"""
        return len(self._corpus)


_engine: BaseRAG | None = None


def _build_rag() -> BaseRAG:
    """按 `RAG_BACKEND` 构造引擎。

    `ragflow` 未配置（缺 API Key / dataset）或不可达时，**降级为自建 memory 检索并告警** ——
    外部依赖不应让整条分析链路中断。
    """
    backend = (settings.RAG_BACKEND or "memory").lower()
    if backend == "ragflow":
        try:
            from app.services.ragflow import RAGFlowEngine

            engine = RAGFlowEngine(
                base_url=settings.RAGFLOW_BASE_URL,
                api_key=settings.RAGFLOW_API_KEY,
                dataset_id=settings.RAGFLOW_DATASET_ID,
                timeout=settings.RAGFLOW_TIMEOUT_SECONDS,
                map_path=settings.RAGFLOW_MAP_PATH,
                similarity_threshold=settings.RAGFLOW_SIMILARITY_THRESHOLD,
                vector_weight=settings.RAGFLOW_VECTOR_WEIGHT,
                parse_wait_seconds=settings.RAGFLOW_PARSE_WAIT_SECONDS,
                keyword_extraction=settings.RAGFLOW_KEYWORD_EXTRACTION,
            )
            ready, reason = engine.ready()
            if ready:
                logger.info("RAG 后端 = ragflow @ %s（dataset=%s）",
                            settings.RAGFLOW_BASE_URL, settings.RAGFLOW_DATASET_ID)
                return engine
            logger.warning("RAGFlow 未就绪（%s），降级为自建 memory 检索", reason)
        except Exception as exc:  # pragma: no cover - 依赖外部服务
            logger.warning("RAGFlow 初始化失败，降级为自建 memory 检索: %s", exc)
    return RAGEngine()


def get_rag() -> BaseRAG:
    global _engine
    if _engine is None:
        _engine = _build_rag()
    return _engine


def reset_rag() -> None:
    global _engine
    _engine = None
