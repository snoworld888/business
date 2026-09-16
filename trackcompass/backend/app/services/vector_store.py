"""向量库适配器。

memory  —— 进程内列表 + 余弦检索（默认，零依赖）
milvus  —— Milvus 2.4，集合 signal_embeddings，IVF_FLAT + COSINE（技术文档 §3.3.3）

两者共用同一份检索接口，业务代码不感知底层实现。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.services.embeddings import cosine

logger = logging.getLogger(__name__)


@dataclass
class VectorRecord:
    id: str
    embedding: list[float]
    signal_type: str | None = None
    source: str | None = None
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: int = 0


class BaseVectorStore:
    def upsert(self, records: list[VectorRecord]) -> int:  # pragma: no cover
        raise NotImplementedError

    def search(
        self,
        query_vector: list[float],
        limit: int = 20,
        signal_type: str | None = None,
    ) -> list[tuple[VectorRecord, float]]:  # pragma: no cover
        raise NotImplementedError

    def count(self) -> int:  # pragma: no cover
        raise NotImplementedError


class MemoryVectorStore(BaseVectorStore):
    def __init__(self) -> None:
        self._data: dict[str, VectorRecord] = {}

    def upsert(self, records: list[VectorRecord]) -> int:
        for r in records:
            self._data[r.id] = r
        return len(records)

    def search(self, query_vector, limit=20, signal_type=None):
        hits: list[tuple[VectorRecord, float]] = []
        for rec in self._data.values():
            if signal_type and rec.signal_type != signal_type:
                continue
            hits.append((rec, cosine(query_vector, rec.embedding)))
        hits.sort(key=lambda x: x[1], reverse=True)
        return hits[:limit]

    def count(self) -> int:
        return len(self._data)


class MilvusVectorStore(BaseVectorStore):  # pragma: no cover - 需 Milvus 服务
    """生产适配器。需 pymilvus；未安装或不可达时由 get_vector_store() 降级。"""

    COLLECTION = "signal_embeddings"

    def __init__(self, uri: str, dim: int):
        from pymilvus import (  # type: ignore
            Collection, CollectionSchema, DataType, FieldSchema, connections, utility,
        )

        self._Collection = Collection
        self._utility = utility
        connections.connect(alias="default", uri=uri)
        if not utility.has_collection(self.COLLECTION):
            schema = CollectionSchema(
                fields=[
                    FieldSchema("id", DataType.VARCHAR, is_primary=True, max_length=64),
                    FieldSchema("embedding", DataType.FLOAT_VECTOR, dim=dim),
                    FieldSchema("signal_type", DataType.VARCHAR, max_length=64),
                    FieldSchema("source", DataType.VARCHAR, max_length=256),
                    FieldSchema("content", DataType.VARCHAR, max_length=8192),
                    FieldSchema("created_at", DataType.INT64),
                ]
            )
            self.collection = Collection(self.COLLECTION, schema)
            self.collection.create_index(
                field_name="embedding",
                index_params={"index_type": "IVF_FLAT", "metric_type": "COSINE",
                              "params": {"nlist": 1024}},
            )
        else:
            self.collection = Collection(self.COLLECTION)

    def upsert(self, records):
        self.collection.upsert([
            [r.id for r in records], [r.embedding for r in records],
            [r.signal_type or "" for r in records], [r.source or "" for r in records],
            [r.content[:8000] for r in records], [r.created_at for r in records],
        ])
        return len(records)

    def search(self, query_vector, limit=20, signal_type=None):
        self.collection.load()
        expr = f'signal_type == "{signal_type}"' if signal_type else None
        res = self.collection.search(
            data=[query_vector], anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 10}},
            limit=limit, expr=expr, output_fields=["signal_type", "source", "content"],
        )
        out = []
        for hit in res[0]:
            ent = hit.entity
            out.append((
                VectorRecord(id=hit.id, embedding=[], signal_type=ent.get("signal_type"),
                             source=ent.get("source"), content=ent.get("content")),
                float(hit.score),
            ))
        return out

    def count(self):
        return self.collection.num_entities


_store: BaseVectorStore | None = None


def get_vector_store() -> BaseVectorStore:
    global _store
    if _store is None:
        if settings.VECTOR_BACKEND == "milvus":
            try:
                _store = MilvusVectorStore(settings.MILVUS_URI, settings.EMBEDDING_DIM)
                logger.info("向量库后端 = milvus @ %s", settings.MILVUS_URI)
            except Exception as exc:
                logger.warning("Milvus 不可用，降级 memory 实现: %s", exc)
        if _store is None:
            _store = MemoryVectorStore()
    return _store


def reset_vector_store() -> None:
    global _store
    _store = None
