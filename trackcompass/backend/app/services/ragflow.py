"""RAGFlow 检索适配器（全量替换自建 RAG）。

设计目标：**上层零改动**。`RAGFlowEngine` 与 `RAGEngine` 继承同一个 `BaseRAG`，
公开契约完全一致：

    index(docs) -> int
    retrieve(query, top_k=8, signal_type=None) -> list[RetrievedDoc]
    build_context(docs, max_chars=4000) -> str
    citation_ratio(docs) -> float
    count_total() -> int

因此 7 个 Agent / API 层 / 前端无需任何修改。

数据映射（关键设计）
--------------------
TrackCompass 的一个 signal  ⇄  RAGFlow 的一个 document

* **document 文件名 = `{signal_id}.txt`** ⇒ 检索返回的 `document_keyword`
  可直接反查 `signal_id`，**证据链与下钻得以完整保留**。
* RAGFlow 的 retrieval **只返回正文**（`content` / `document_id` / `document_keyword`），
  因此 `source` / `signal_type` / `title` / `url` 需本地旁路映射：
  `index()` 时写入 `RAGFLOW_MAP_PATH` 指向的 JSON，进程重启后自动加载。

与自建实现的两处语义差异（务必知晓）
------------------------------------
1. **异步解析**：RAGFlow 是「上传 → 触发解析 → 轮询状态」的流水线，
   上传后有一段**不可检索窗口**。`index()` 会在 `RAGFLOW_PARSE_WAIT_SECONDS`
   内轮询；超时不报错，仅告警（文档稍后可检索）。
2. **延迟量级**：每次检索要经过 HTTP + ES + Ollama 嵌入，为**几十到几百毫秒**
   （自建实现是进程内亚毫秒）。

RAGFlow 接口契约（v0.25.x，取自官方 http_api_reference.md）
----------------------------------------------------------
| 用途 | 端点 |
| --- | --- |
| 就绪探测 | `GET  /api/v1/datasets?id=&page_size=1` |
| 上传文档 | `POST /api/v1/datasets/{id}/documents`（multipart `file`，**文档名取文件名**） |
| 列出文档 | `GET  /api/v1/datasets/{id}/documents?page=&page_size=` |
| 触发解析 | `POST /api/v1/datasets/{id}/chunks`（`{"document_ids":[...]}`） |
| 检索 | `POST /api/v1/retrieval` |
| 删除文档 | `DELETE /api/v1/datasets/{id}/documents`（`{"ids":[...]}`） |

⚠️ RAGFlow 成功返回 `{"code": 0}`（**不是 HTTP 200 语义**）；鉴权失败返回 HTTP 401。
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Iterable

import httpx

from app.services.rag import BaseRAG, RetrievedDoc

logger = logging.getLogger(__name__)

#: 文档解析状态（document.run）：3=DONE，4=FAIL，其余为进行中
_RUN_DONE = {"3", "DONE"}
_RUN_FAIL = {"4", "FAIL"}


class RAGFlowEngine(BaseRAG):
    """基于 RAGFlow HTTP API 的检索引擎。"""

    def __init__(self, base_url: str, api_key: str, dataset_id: str, *,
                 timeout: int = 30, map_path: str | None = None,
                 similarity_threshold: float = 0.2, vector_weight: float = 0.3,
                 parse_wait_seconds: int = 90,
                 keyword_extraction: bool = False) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.dataset_id = dataset_id or ""
        self.timeout = timeout
        self.keyword_extraction = keyword_extraction
        #: 最近一次检索失败的原因（供自查脚本如实上报，避免"无命中"掩盖真实故障）
        self.last_error = ""
        self.map_path = Path(map_path) if map_path else None
        self.similarity_threshold = similarity_threshold
        self.vector_weight = vector_weight
        self.parse_wait_seconds = parse_wait_seconds
        self._meta: dict[str, dict[str, Any]] = self._load_map()

    # ------------------------------------------------------------ 元数据映射表
    def _load_map(self) -> dict[str, dict[str, Any]]:
        if not self.map_path or not self.map_path.is_file():
            return {}
        try:
            data = json.loads(self.map_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("RAGFlow 元数据映射表读取失败: %s", exc)
            return {}

    def _save_map(self) -> None:
        if not self.map_path:
            return
        try:
            self.map_path.parent.mkdir(parents=True, exist_ok=True)
            self.map_path.write_text(
                json.dumps(self._meta, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as exc:
            logger.warning("RAGFlow 元数据映射表写入失败: %s", exc)

    @staticmethod
    def _doc_name(signal_id: str) -> str:
        """文档名 = `{signal_id}.txt`（上传接口用文件名作为文档名）。"""
        return f"{signal_id}.txt"

    @staticmethod
    def _strip_ext(document_keyword: str) -> str:
        name = str(document_keyword or "")
        return name[:-4] if name.lower().endswith(".txt") else name

    #: RAGFlow 的检索走 Elasticsearch `query_string`，下列字符有**查询语法含义**。
    #: 实测踩坑：`"AI+制造"` 里的 `+` 表示「必需项」，查询被解析为
    #: 「必须同时含 AI 与 制造」⇒ 命中数归零（0 命中，且可能拖到超时）。
    _QUERY_META = re.compile(r"[+\-&|!(){}\[\]^\"~*?:\\/]")

    @classmethod
    def _sanitize_query(cls, query: str) -> str:
        """把查询串里的 ES 元字符替换为空格，避免被当成查询语法。"""
        cleaned = cls._QUERY_META.sub(" ", str(query or ""))
        return re.sub(r"\s+", " ", cleaned).strip()

    # ------------------------------------------------------------ HTTP 底座
    def _client(self) -> httpx.Client:
        # trust_env=False：本机 http_proxy 会污染请求（实测返回 502 假象）
        return httpx.Client(
            base_url=f"{self.base_url}/api/v1",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
            trust_env=False,
        )

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        with self._client() as c:
            r = c.get(path, params=params)
            r.raise_for_status()
            return r.json()

    def _post_json(self, path: str, payload: dict) -> dict:
        with self._client() as c:
            r = c.post(path, json=payload)
            r.raise_for_status()
            return r.json()

    def _post_files(self, path: str, files: dict) -> dict:
        with self._client() as c:
            r = c.post(path, files=files)
            r.raise_for_status()
            return r.json()

    def _delete_json(self, path: str, payload: dict) -> dict:
        with self._client() as c:
            r = c.request("DELETE", path, json=payload)
            r.raise_for_status()
            return r.json()

    @staticmethod
    def _rows(data: dict | None) -> list[dict]:
        """兼容 `data` 为列表或 `{docs:[...]}` 两种返回形态。"""
        payload = (data or {}).get("data")
        if isinstance(payload, dict):
            payload = payload.get("docs")
        return payload if isinstance(payload, list) else []

    # ------------------------------------------------------------ 就绪判定
    def ready(self) -> tuple[bool, str]:
        """`(是否可用, 原因)`。缺配置直接判否，避免每次调用都等超时。"""
        if not self.api_key:
            return False, "未配置 RAGFLOW_API_KEY"
        if not self.dataset_id:
            return False, "未配置 RAGFLOW_DATASET_ID"
        try:
            data = self._get_json("/datasets", {"id": self.dataset_id, "page_size": 1})
        except Exception as exc:
            return False, f"连接 RAGFlow 失败: {exc}"
        code = (data or {}).get("code")
        if code not in (0, None):
            return False, f"RAGFlow 返回 code={code}"
        return True, "ok"

    # ------------------------------------------------------------ 索引
    def index(self, docs: Iterable[RetrievedDoc]) -> int:
        """把信号同步到 RAGFlow dataset（幂等）。

        1) 先列出现有文档名 → 已存在者跳过（按 `{signal_id}.txt` 比对）
        2) 逐个上传（内存文件，不落盘）
        3) 对新增文档触发解析，并在 `parse_wait_seconds` 内轮询状态
        """
        docs = list(docs)
        try:
            existing = self._doc_names()
        except Exception as exc:
            logger.warning("RAGFlow 列出文档失败，本次按全量上传处理: %s", exc)
            existing = set()

        pending: list[str] = []
        for doc in docs:
            self._meta[doc.signal_id] = {
                "source": doc.source,
                "signal_type": doc.signal_type,
                "title": doc.title,
                "url": doc.url,
            }
            if self._doc_name(doc.signal_id) in existing:
                continue
            doc_id = self._upload(doc)
            if doc_id:
                pending.append(doc_id)

        if pending:
            self._trigger_parse(pending)
            self._wait_parsed(pending)

        self._save_map()
        logger.info("RAGFlow 索引完成：信号 %d 条，本次新增文档 %d 篇", len(docs), len(pending))
        return len(self._meta)

    def _doc_names(self) -> set[str]:
        names: set[str] = set()
        page = 1
        while page <= 50:  # 上限保护，避免异常分页导致死循环
            data = self._get_json(f"/datasets/{self.dataset_id}/documents",
                                 {"page": page, "page_size": 200})
            rows = self._rows(data)
            if not rows:
                break
            for item in rows:
                if item.get("name"):
                    names.add(str(item["name"]))
            if len(rows) < 200:
                break
            page += 1
        return names

    def _upload(self, doc: RetrievedDoc) -> str | None:
        content = f"{doc.title}\n{doc.text}".strip() or doc.title or doc.signal_id
        files = {"file": (self._doc_name(doc.signal_id),
                          content.encode("utf-8"), "text/plain")}
        try:
            data = self._post_files(f"/datasets/{self.dataset_id}/documents", files)
        except Exception as exc:
            logger.warning("RAGFlow 上传失败 signal_id=%s: %s", doc.signal_id, exc)
            return None
        rows = self._rows(data)
        return str(rows[0].get("id")) if rows and rows[0].get("id") else None

    def _trigger_parse(self, document_ids: list[str]) -> None:
        try:
            self._post_json(f"/datasets/{self.dataset_id}/chunks",
                            {"document_ids": document_ids})
        except Exception as exc:
            logger.warning("RAGFlow 触发解析失败（文档将在后续轮询/重试中解析）: %s", exc)

    def _wait_parsed(self, document_ids: list[str]) -> None:
        deadline = time.time() + max(0, self.parse_wait_seconds)
        remaining = set(document_ids)
        while remaining and time.time() < deadline:
            time.sleep(2)
            for doc_id in list(remaining):
                try:
                    data = self._get_json(f"/datasets/{self.dataset_id}/documents",
                                          {"id": doc_id, "page_size": 1})
                except Exception:
                    continue
                rows = self._rows(data)
                run = str(rows[0].get("run")) if rows else ""
                if run in _RUN_DONE or run in _RUN_FAIL:
                    remaining.discard(doc_id)
        if remaining:
            logger.warning("RAGFlow 解析等待超时，%d 篇文档稍后才可检索", len(remaining))

    # ------------------------------------------------------------ 检索
    def retrieve(self, query: str, top_k: int = 8,
                 signal_type: str | None = None) -> list[RetrievedDoc]:
        if not self.dataset_id:
            return []
        self.last_error = ""
        # signal_type 过滤在 RAGFlow 侧无对应参数（需先给文档打 metadata），
        # 因此需要过滤时多取一些再本地筛。
        fetch = max(top_k * 3, 20) if signal_type else max(top_k, 8)
        payload = {
            "question": self._sanitize_query(query),
            "dataset_ids": [self.dataset_id],
            "page": 1,
            "page_size": fetch,
            "top_k": fetch,
            "similarity_threshold": self.similarity_threshold,
            "vector_similarity_weight": self.vector_weight,
            # 仅在租户的默认对话模型已注册时才开启（否则 RAGFlow 会整条检索失败）
            "keyword": bool(self.keyword_extraction),
            "highlight": False,
        }
        try:
            data = self._post_json("/retrieval", payload)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("RAGFlow 检索失败（本次返回空结果）: %s", exc)
            return []
        if (data or {}).get("code") not in (0, None):
            self.last_error = (f"code={(data or {}).get('code')}: "
                               f"{(data or {}).get('message')}")
            logger.warning("RAGFlow 检索返回 %s", self.last_error)
            return []
        chunks = ((data or {}).get("data") or {}).get("chunks") or []
        return self._to_docs(chunks, top_k=top_k, signal_type=signal_type)

    def _to_docs(self, chunks: list[dict], top_k: int,
                 signal_type: str | None) -> list[RetrievedDoc]:
        """chunk → RetrievedDoc，并按 signal_id 去重（同一文档可能命中多个 chunk）。"""
        best: dict[str, RetrievedDoc] = {}
        for ch in chunks:
            doc_name = str(ch.get("document_keyword") or "")
            signal_id = self._strip_ext(doc_name) or str(ch.get("document_id") or "")
            meta = self._meta.get(signal_id) or {}
            stype = str(meta.get("signal_type") or "")
            if signal_type and stype != signal_type:
                continue
            cand = RetrievedDoc(
                signal_id=signal_id,
                source=str(meta.get("source") or "RAGFlow"),
                signal_type=stype,
                title=str(meta.get("title") or doc_name),
                text=str(ch.get("content") or ""),
                url=meta.get("url"),
                score=round(float(ch.get("similarity") or 0.0), 6),
                vector_score=round(float(ch.get("vector_similarity") or 0.0), 4),
                keyword_score=round(float(ch.get("term_similarity") or 0.0), 4),
                metadata={"document_id": ch.get("document_id")},
            )
            prev = best.get(signal_id)
            if prev is None or cand.score > prev.score:
                best[signal_id] = cand
        ranked = sorted(best.values(), key=lambda d: d.score, reverse=True)
        return ranked[:top_k]

    def count_total(self) -> int:
        """已登记的信号数（映射表大小）。

        映射表为空时返回 0，上层会据此触发一次 `reindex()` 重建 —— 这对
        「RAGFlow 侧已有数据但本地映射丢失」的场景是自愈路径。
        """
        return len(self._meta)

    # ------------------------------------------------------------ 维护
    def drop(self, signal_ids: list[str]) -> int:
        """按 signal_id 删除 RAGFlow 文档（预留：信号下线时同步清理）。"""
        ids = [d for d in self._doc_ids({self._doc_name(s) for s in signal_ids}) if d]
        if ids:
            try:
                self._delete_json(f"/datasets/{self.dataset_id}/documents", {"ids": ids})
            except Exception as exc:
                logger.warning("RAGFlow 删除文档失败: %s", exc)
                return 0
        for s in signal_ids:
            self._meta.pop(s, None)
        self._save_map()
        return len(ids)

    def _doc_ids(self, names: set[str]) -> list[str]:
        out: list[str] = []
        page = 1
        while page <= 50:
            data = self._get_json(f"/datasets/{self.dataset_id}/documents",
                                  {"page": page, "page_size": 200})
            rows = self._rows(data)
            if not rows:
                break
            out.extend(str(r["id"]) for r in rows if r.get("name") in names and r.get("id"))
            if len(rows) < 200:
                break
            page += 1
        return out
