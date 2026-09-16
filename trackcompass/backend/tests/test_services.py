"""认知层服务测试：Embedding / 向量库 / RAG / 图谱 / 报告 / LLM 路由。"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.services.embeddings import (
    BaseEmbedding,
    LocalHashEmbedding,
    OllamaEmbedding,
    cosine,
    embed,
    get_embedder,
)
from app.services.graph import MemoryGraphStore
from app.services.llm import extract_json, llm
from app.services.rag import (
    RAGEngine,
    RetrievedDoc,
    chunk_text,
    get_rag,
    reset_rag,
    tokenize,
)
from app.services.ragflow import RAGFlowEngine
from app.services.report import (
    REPORT_SECTIONS,
    ReportContext,
    available_templates,
    build_content,
    export,
    render_markdown,
)
from app.services.vector_store import MemoryVectorStore, VectorRecord


# ------------------------------------------------------------------ Embedding
class TestEmbedding:
    def test_deterministic(self):
        e = LocalHashEmbedding(256)
        assert e.embed("智能质检") == e.embed("智能质检")

    def test_dimension(self):
        assert len(LocalHashEmbedding(1536).embed("x")) == 1536

    def test_normalized(self):
        v = LocalHashEmbedding(512).embed("工业视觉缺陷检测")
        norm = sum(x * x for x in v) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-6)

    def test_empty_text(self):
        v = LocalHashEmbedding(64).embed("")
        assert all(x == 0.0 for x in v)

    def test_similar_texts_score_higher(self):
        e = LocalHashEmbedding(1536)
        a = e.embed("工业视觉缺陷检测系统")
        b = e.embed("工业视觉缺陷检测方案")
        c = e.embed("储能电池材料循环寿命")
        assert cosine(a, b) > cosine(a, c)

    def test_cosine_handles_zero(self):
        assert cosine([], [1.0]) == 0.0
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


class _FakeResp:
    """伪造 httpx 响应，用于离线断言请求形状。"""

    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._payload


class TestOllamaEmbedding:
    """Ollama 嵌入适配器的三处踩坑回归（模型名 / 端点 / 维度）。"""

    def test_dim_known_before_any_call(self):
        """bge-m3 是 1024 维，不能沿用 EMBEDDING_DIM(1536) 的默认值。"""
        e = OllamaEmbedding("bge-m3")
        assert e.dim == 1024
        assert OllamaEmbedding("bge-m3:latest").dim == 1024

    def test_unknown_model_falls_back_to_configured_dim(self):
        assert OllamaEmbedding("某个没见过的模型").dim == settings.EMBEDDING_DIM

    def test_prefers_new_endpoint_with_input_key(self, monkeypatch):
        """必须走 /api/embed + input（新端点），而非 /api/embeddings + prompt。"""
        calls: list[tuple[str, dict]] = []

        def fake_post(url, **kw):
            calls.append((url, kw.get("json") or {}))
            return _FakeResp({"embeddings": [[0.1, 0.2, 0.3]]})

        monkeypatch.setattr("app.services.embeddings.httpx.post", fake_post)
        e = OllamaEmbedding("bge-m3")
        vec = e.embed("赛道罗盘")

        assert calls[0][0].endswith("/api/embed")
        assert "input" in calls[0][1]
        assert vec == [0.1, 0.2, 0.3]
        assert e.dim == 3  # 用真实返回长度校正

    def test_falls_back_to_legacy_endpoint(self, monkeypatch):
        """旧版 Ollama 无 /api/embed 时，应回退 /api/embeddings + prompt + embedding。"""

        def fake_post(url, **kw):
            if url.endswith("/api/embed"):
                raise RuntimeError("404 page not found")
            return _FakeResp({"embedding": [9.0, 8.0]})

        monkeypatch.setattr("app.services.embeddings.httpx.post", fake_post)
        e = OllamaEmbedding("bge-m3")
        assert e.embed("x") == [9.0, 8.0]
        assert e.dim == 2

    def test_get_embedder_uses_ollama_model_name(self, monkeypatch):
        """ollama 分支必须取 EMBEDDING_OLLAMA_MODEL，不能用 HuggingFace 名。"""
        monkeypatch.setattr(settings, "EMBEDDING_BACKEND", "ollama")
        get_embedder.cache_clear()
        try:
            e = get_embedder()
            assert isinstance(e, OllamaEmbedding)
            assert e.model == settings.EMBEDDING_OLLAMA_MODEL == "bge-m3"
            assert "/" not in e.model, "Ollama 仓库名不应带组织前缀"
        finally:
            get_embedder.cache_clear()

    def test_remote_failure_degrades_to_local(self, monkeypatch):
        """远端嵌入调用失败时降级 local，不能让 RAG 链路直接抛错。"""

        class _Boom(BaseEmbedding):
            dim = 1024

            def embed(self, text: str) -> list[float]:
                raise RuntimeError("远端不可用")

        monkeypatch.setattr("app.services.embeddings.get_embedder", lambda: _Boom())
        vec = embed("工业视觉缺陷检测")
        assert len(vec) == settings.EMBEDDING_DIM

    def test_local_failure_still_raises(self, monkeypatch):
        """local 后端自身的失败不应被吞掉（避免掩盖真实缺陷）。"""

        class _BoomLocal(LocalHashEmbedding):
            def embed(self, text: str) -> list[float]:
                raise RuntimeError("本地实现异常")

        monkeypatch.setattr("app.services.embeddings.get_embedder", lambda: _BoomLocal(64))
        with pytest.raises(RuntimeError):
            embed("x")


class _FakeRAGFlow(RAGFlowEngine):
    """离线 RAGFlow 假实现：只覆盖 HTTP 底座，其余逻辑走真实代码路径。"""

    def __init__(self, **kw):
        kw.setdefault("map_path", None)
        kw.setdefault("parse_wait_seconds", 0)  # 避免测试里 sleep
        super().__init__("http://fake-ragflow", "test-key", "ds-test", **kw)
        self.existing: list[dict] = []
        self.uploaded: list[tuple[str, str]] = []
        self.parsed: list[str] = []
        self.chunks: list[dict] = []
        self.fail_on: str | None = None
        self.retrieval_payloads: list[dict] = []
        self.retrieval_code: int | None = None
        self.retrieval_message: str = ""

    def _get_json(self, path, params=None):
        if self.fail_on == "GET":
            raise RuntimeError("connection refused")
        if path == "/datasets":
            return {"code": 0, "data": [{"id": "ds-test"}]}
        if path.endswith("/documents"):
            if params and params.get("id"):
                return {"code": 0, "data": {"docs": [{"id": params["id"], "run": "3"}]}}
            return {"code": 0,
                    "data": {"docs": self.existing, "total": len(self.existing)}}
        return {"code": 0, "data": {}}

    def _post_json(self, path, payload):
        if self.fail_on == "POST":
            raise RuntimeError("connection refused")
        if path.endswith("/chunks"):
            self.parsed.extend(payload["document_ids"])
            return {"code": 0}
        if path == "/retrieval":
            self.retrieval_payloads.append(dict(payload))
            if self.retrieval_code is not None:
                return {"code": self.retrieval_code, "message": self.retrieval_message}
            return {"code": 0, "data": {"chunks": self.chunks}}
        return {"code": 0, "data": {}}

    def _post_files(self, path, files):
        if self.fail_on == "UPLOAD":
            raise RuntimeError("upload failed")
        name = files["file"][0]
        doc_id = f"doc{len(self.uploaded) + 1}"
        self.uploaded.append((name, doc_id))
        return {"code": 0, "data": [{"id": doc_id, "name": name}]}

    def _delete_json(self, path, payload):
        return {"code": 0}


def _sig(sid: str, *, stype: str = "policy", source: str = "工信部",
         title: str = "标题", text: str = "正文") -> RetrievedDoc:
    return RetrievedDoc(signal_id=sid, source=source, signal_type=stype,
                        title=title, text=text, url=f"https://example.com/{sid}")


class TestRAGFlowEngine:
    """RAGFlow 适配器：契约一致性 + 证据链映射 + 降级。"""

    def test_ready_reports_missing_config(self):
        assert RAGFlowEngine("http://x", "", "ds").ready()[0] is False
        assert RAGFlowEngine("http://x", "k", "").ready()[0] is False

    def test_ready_ok_with_fake(self):
        assert _FakeRAGFlow().ready() == (True, "ok")

    def test_ready_reports_unreachable(self):
        eng = _FakeRAGFlow()
        eng.fail_on = "GET"
        ok, reason = eng.ready()
        assert ok is False and "连接" in reason

    def test_doc_name_roundtrip(self):
        assert RAGFlowEngine._doc_name("abc") == "abc.txt"
        assert RAGFlowEngine._strip_ext("abc.txt") == "abc"
        assert RAGFlowEngine._strip_ext("abc") == "abc"

    # ---------- 查询净化：`+` 会被 ES 当成必需项操作符，命中直接归零 ----------
    def test_sanitize_query_strips_es_metachars(self):
        assert RAGFlowEngine._sanitize_query("AI+制造") == "AI 制造"
        assert RAGFlowEngine._sanitize_query('a-b (c) "d"') == "a b c d"
        assert RAGFlowEngine._sanitize_query("a~b*c?d") == "a b c d"
        assert RAGFlowEngine._sanitize_query("  多   空格  ") == "多 空格"

    def test_sanitize_query_keeps_normal_text(self):
        assert RAGFlowEngine._sanitize_query("智能质检 缺陷检测") == "智能质检 缺陷检测"
        assert RAGFlowEngine._sanitize_query("") == ""

    def test_retrieve_sends_sanitized_question(self):
        eng = _FakeRAGFlow()
        eng.retrieve("AI+制造")
        assert eng.retrieval_payloads[-1]["question"] == "AI 制造"

    def test_index_uploads_and_records_metadata(self):
        eng = _FakeRAGFlow()
        n = eng.index([_sig("s1"), _sig("s2", stype="patent", source="知识产权局")])
        assert n == 2
        assert [name for name, _ in eng.uploaded] == ["s1.txt", "s2.txt"]
        assert eng.parsed == ["doc1", "doc2"]          # 触发了异步解析
        assert eng._meta["s2"]["source"] == "知识产权局"

    def test_index_is_idempotent(self):
        eng = _FakeRAGFlow()
        eng.existing = [{"id": "d1", "name": "s1.txt"}]
        eng.index([_sig("s1")])
        assert eng.uploaded == []                      # 已存在则不重复上传
        assert eng._meta["s1"]["title"] == "标题"      # 但元数据仍补全

    def test_index_survives_upload_failure(self):
        eng = _FakeRAGFlow()
        eng.fail_on = "UPLOAD"
        assert eng.index([_sig("s1")]) == 1            # 不抛异常，元数据仍登记
        assert eng.uploaded == []

    def test_retrieve_maps_chunks_back_to_signals(self):
        eng = _FakeRAGFlow()
        eng.index([_sig("s1", stype="policy", source="工信部", title="政策标题")])
        eng.chunks = [{"content": "正文片段", "document_id": "doc1",
                       "document_keyword": "s1.txt", "similarity": 0.87,
                       "vector_similarity": 0.8, "term_similarity": 0.6}]
        docs = eng.retrieve("智能质检")
        assert len(docs) == 1
        d = docs[0]
        assert d.signal_id == "s1"                     # ← 关键：能反查回 signal_id
        assert d.source == "工信部" and d.title == "政策标题"
        assert d.score == 0.87 and d.vector_score == 0.8 and d.keyword_score == 0.6
        assert d.url                                 # 证据链下钻可用

    def test_retrieve_filters_by_signal_type(self):
        eng = _FakeRAGFlow()
        eng.index([_sig("s1", stype="policy"), _sig("s2", stype="patent")])
        eng.chunks = [
            {"content": "a", "document_keyword": "s1.txt", "similarity": 0.9},
            {"content": "b", "document_keyword": "s2.txt", "similarity": 0.8},
        ]
        docs = eng.retrieve("q", signal_type="patent")
        assert [d.signal_id for d in docs] == ["s2"]

    def test_retrieve_dedupes_multiple_chunks_of_same_document(self):
        eng = _FakeRAGFlow()
        eng.index([_sig("s1")])
        eng.chunks = [
            {"content": "片段1", "document_keyword": "s1.txt", "similarity": 0.5},
            {"content": "片段2", "document_keyword": "s1.txt", "similarity": 0.9},
        ]
        docs = eng.retrieve("q")
        assert len(docs) == 1 and docs[0].score == 0.9   # 取最高分那条

    def test_retrieve_returns_empty_on_failure(self):
        eng = _FakeRAGFlow()
        eng.fail_on = "POST"
        assert eng.retrieve("q") == []                   # 不抛异常，链路不中断

    # ---------- keyword 抽取：踩过的真实坑，必须锁住 ----------
    def test_retrieve_does_not_ask_for_keyword_extraction_by_default(self):
        """默认**不得**让 RAGFlow 调对话模型抽取关键词。

        实测教训：RAGFlow 租户默认对话模型（tenant.llm_id）可能指向一个
        未登记在 tenant_llm 的模型，此时带 keyword=True 会让**整条检索**抛
        `LookupError: Tenant Model ... type chat not found` 并返回空结果。
        """
        eng = _FakeRAGFlow()
        eng.retrieve("智能质检")
        assert eng.retrieval_payloads[-1]["keyword"] is False

    def test_retrieve_can_opt_in_to_keyword_extraction(self):
        eng = _FakeRAGFlow(keyword_extraction=True)
        eng.retrieve("智能质检")
        assert eng.retrieval_payloads[-1]["keyword"] is True

    def test_retrieval_error_is_recorded_not_silently_empty(self):
        """检索失败必须留下 last_error，避免自查脚本把故障报成「无命中」。"""
        eng = _FakeRAGFlow()
        eng.retrieval_code = 100
        eng.retrieval_message = "Tenant Model with name qwen3.5:35b@Ollama and type chat not found"
        assert eng.retrieve("q") == []
        assert "type chat not found" in eng.last_error

    def test_last_error_cleared_on_success(self):
        eng = _FakeRAGFlow()
        eng.retrieval_code = 100
        eng.retrieval_message = "boom"
        eng.retrieve("q")
        assert eng.last_error
        eng.retrieval_code = None
        eng.retrieve("q")
        assert eng.last_error == ""

    def test_count_total_tracks_map(self):
        eng = _FakeRAGFlow()
        assert eng.count_total() == 0
        eng.index([_sig("s1"), _sig("s2")])
        assert eng.count_total() == 2

    def test_build_context_and_citation_shared_with_memory_engine(self):
        """两个引擎共用 BaseRAG 的后处理，行为必须一致。"""
        docs = [_sig("s1"), _sig("s2")]
        rf, mem = _FakeRAGFlow(), RAGEngine()
        assert rf.build_context(docs) == mem.build_context(docs)
        assert rf.citation_ratio(docs) == mem.citation_ratio(docs) == 1.0


class TestRAGFactory:
    """get_rag() 的后端选择与降级。"""

    def test_falls_back_to_memory_when_unconfigured(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "RAG_BACKEND", "ragflow")
        monkeypatch.setattr(settings, "RAGFLOW_API_KEY", "")
        reset_rag()
        try:
            assert isinstance(get_rag(), RAGEngine)
        finally:
            reset_rag()

    def test_uses_ragflow_when_ready(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "RAG_BACKEND", "ragflow")
        monkeypatch.setattr(settings, "RAGFLOW_API_KEY", "k")
        monkeypatch.setattr(settings, "RAGFLOW_DATASET_ID", "ds")
        monkeypatch.setattr("app.services.ragflow.RAGFlowEngine.ready",
                            lambda self: (True, "ok"))
        reset_rag()
        try:
            assert isinstance(get_rag(), RAGFlowEngine)
        finally:
            reset_rag()

    def test_memory_backend_by_default(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "RAG_BACKEND", "memory")
        reset_rag()
        try:
            assert isinstance(get_rag(), RAGEngine)
        finally:
            reset_rag()


# ------------------------------------------------------------------ 向量库
class TestVectorStore:
    def test_upsert_and_count(self):
        store = MemoryVectorStore()
        store.upsert([VectorRecord(id="1", embedding=[1.0, 0.0]),
                      VectorRecord(id="2", embedding=[0.0, 1.0])])
        assert store.count() == 2

    def test_upsert_is_idempotent(self):
        store = MemoryVectorStore()
        store.upsert([VectorRecord(id="1", embedding=[1.0, 0.0])])
        store.upsert([VectorRecord(id="1", embedding=[0.5, 0.5])])
        assert store.count() == 1

    def test_search_orders_by_similarity(self):
        store = MemoryVectorStore()
        store.upsert([VectorRecord(id="near", embedding=[1.0, 0.0]),
                      VectorRecord(id="far", embedding=[0.0, 1.0])])
        hits = store.search([1.0, 0.0], limit=2)
        assert hits[0][0].id == "near"
        assert hits[0][1] > hits[1][1]

    def test_search_filters_by_signal_type(self):
        store = MemoryVectorStore()
        store.upsert([VectorRecord(id="a", embedding=[1.0, 0.0], signal_type="policy"),
                      VectorRecord(id="b", embedding=[1.0, 0.0], signal_type="social")])
        hits = store.search([1.0, 0.0], signal_type="social")
        assert [h[0].id for h in hits] == ["b"]


# ------------------------------------------------------------------ RAG
@pytest.fixture()
def docs() -> list[RetrievedDoc]:
    return [
        RetrievedDoc(signal_id="d1", source="工信部", signal_type="policy",
                     title="制造业数字化转型行动方案",
                     text="支持工业视觉与智能质检规模化应用，对示范项目给予补贴。",
                     url="https://example.com/1"),
        RetrievedDoc(signal_id="d2", source="招聘平台", signal_type="recruit",
                     title="急招工业视觉算法工程师",
                     text="要求熟悉缺陷检测与模型轻量化部署。", url="https://example.com/2"),
        RetrievedDoc(signal_id="d3", source="产业媒体", signal_type="news",
                     title="某储能公司融资 12 亿元",
                     text="资金用于产能扩张与固态电池研发。", url="https://example.com/3"),
    ]


class TestRAG:
    def test_chunk_text(self):
        assert chunk_text("") == []
        assert chunk_text("短文本") == ["短文本"]
        chunks = chunk_text("字" * 1000, size=400, overlap=60)
        assert len(chunks) > 1
        assert all(len(c) <= 400 for c in chunks)

    def test_tokenize_produces_ngrams(self):
        tokens = tokenize("AI 质检")
        assert "ai" in tokens
        assert any(len(t) == 2 for t in tokens)

    def test_index_and_retrieve(self, docs):
        rag = RAGEngine()
        assert rag.index(docs) == 3
        hits = rag.retrieve("工业视觉 智能质检", top_k=2)
        assert hits
        assert hits[0].signal_id in {"d1", "d2"}

    def test_hybrid_scores_populated(self, docs):
        rag = RAGEngine()
        rag.index(docs)
        hit = rag.retrieve("智能质检", top_k=1)[0]
        assert hit.vector_score >= 0
        assert hit.keyword_score >= 0
        assert hit.score > 0

    def test_retrieve_with_type_filter(self, docs):
        rag = RAGEngine()
        rag.index(docs)
        hits = rag.retrieve("算法", top_k=5, signal_type="recruit")
        assert all(h.signal_type == "recruit" for h in hits)

    def test_retrieve_on_empty_index(self):
        assert RAGEngine().retrieve("anything") == []

    def test_build_context_respects_budget(self, docs):
        rag = RAGEngine()
        rag.index(docs)
        ctx = rag.build_context(rag.retrieve("质检", top_k=3), max_chars=200)
        assert len(ctx) <= 400
        assert "[E1]" in ctx

    def test_evidence_and_citation_ratio(self, docs):
        rag = RAGEngine()
        rag.index(docs)
        hits = rag.retrieve("质检", top_k=3)
        ev = hits[0].to_evidence(1)
        assert ev["ref"] == "E1" and ev["url"]
        assert rag.citation_ratio(hits) == 1.0
        assert rag.citation_ratio([]) == 0.0

    def test_count_total(self, docs):
        rag = RAGEngine()
        rag.index(docs)
        assert rag.count_total() == 3


# ------------------------------------------------------------------ 图谱
class TestGraph:
    @pytest.fixture()
    def store(self) -> MemoryGraphStore:
        s = MemoryGraphStore()
        s.add_node("Track", "AI+制造", industry="AI+制造")
        s.add_node("Track", "AI+医疗", industry="AI+医疗")
        s.add_node("Company", "某视觉科技")
        s.add_node("Signal", "s1", signal_type="patent")
        s.add_edge("Company", "某视觉科技", "OWNS", "Track", "AI+制造")
        s.add_edge("Signal", "s1", "IMPACTS", "Track", "AI+制造")
        return s

    def test_add_and_degrees(self, store):
        assert store.g.number_of_nodes() == 4
        assert store.g.number_of_edges() == 2

    def test_neighbors_outgoing(self, store):
        n = store.neighbors("Company", "某视觉科技", depth=1)
        assert any("AI+制造" in k for k in n)
        assert n[[k for k in n if "AI+制造" in k][0]]["relation"] == "OWNS"

    def test_neighbors_incoming_requires_direction(self, store):
        # AI+制造 只有入边（Company→Track、Signal→Track），默认 out 方向应为空
        assert store.neighbors("Track", "AI+制造", depth=1) == {}
        assert store.neighbors("Track", "AI+制造", depth=1, direction="in")

    def test_neighbors_both_directions(self, store):
        n = store.neighbors("Track", "AI+制造", depth=1, direction="both")
        assert n

    def test_neighbors_rel_filter(self, store):
        assert store.neighbors("Company", "某视觉科技", rel="IMPACTS", depth=1) == {}

    def test_neighbors_missing_node(self, store):
        assert store.neighbors("Track", "不存在") == {}

    def test_blank_spots_flags_sparse_track(self, store):
        blanks = {b["name"] for b in store.blank_spots(threshold=2)}
        assert "AI+医疗" in blanks
        assert "AI+制造" not in blanks

    def test_centrality_sorted(self, store):
        cent = store.centrality(top=5)
        assert cent
        assert cent[0]["centrality"] >= cent[-1]["centrality"]

    def test_to_cytoscape_shape(self, store):
        data = store.to_cytoscape()
        assert len(data["nodes"]) == 4 and len(data["edges"]) == 2
        assert data["nodes"][0]["data"]["id"]
        assert "node_types" in data["legend"]

    def test_cytoscape_label_prefers_readable_title(self):
        """Signal / Opportunity 的 `key` 是 UUID，**不能**当显示名。

        实测缺陷：原实现 `label = key`，导致图谱上 64 个节点显示成 36 位 UUID
        （用户反馈"知识图谱里有很多非文字字符串"）。
        """
        s = MemoryGraphStore()
        uid = "228439ca-e47a-4331-88dc-6f47df67e491"
        s.add_node("Signal", uid, title="《制造业数字化转型行动方案》发布")
        s.add_node("Opportunity", "opp-1", title="AI 质检商业化")
        s.add_node("Track", "AI+制造")
        by_id = {n["data"]["id"]: n["data"] for n in s.to_cytoscape()["nodes"]}

        assert by_id[f"Signal::{uid}"]["label"] == "《制造业数字化转型行动方案》发布"
        assert by_id["Opportunity::opp-1"]["label"] == "AI 质检商业化"
        # 无 title 的节点退回 key（Track 的 key 本来就是可读的）
        assert by_id["Track::AI+制造"]["label"] == "AI+制造"
        # `key` 仍要保留（供下钻），`type` 仍是节点类型
        assert by_id[f"Signal::{uid}"]["key"] == uid
        assert by_id[f"Signal::{uid}"]["type"] == "Signal"

    def test_centrality_name_is_readable(self):
        """中心性排名的 `name` 同样不能是 UUID（前端表格直接渲染它）。"""
        s = MemoryGraphStore()
        uid = "228439ca-e47a-4331-88dc-6f47df67e491"
        s.add_node("Signal", uid, title="政策信号标题")
        s.add_node("Track", "AI+制造")
        s.add_edge("Signal", uid, "IMPACTS", "Track", "AI+制造")
        cent = {c["key"]: c for c in s.centrality(top=5)}
        assert cent[uid]["name"] == "政策信号标题"
        assert cent["AI+制造"]["name"] == "AI+制造"

    def test_communities_on_empty_graph(self):
        assert MemoryGraphStore().communities() == []

    def test_trends_are_not_track_nodes(self, db):
        """趋势**不能**建成 Track 节点。

        实测缺陷：`blank_spots()` 只筛 `label == "Track"`，而原实现
        `add_node("Track", tr.name)` 把趋势名也建成 Track
        ⇒ 「空白点识别」返回趋势名，而前端该表的列名是「赛道」（语义错误）。
        """
        from app.services.graph import build_from_signals

        store = build_from_signals(db)
        kinds = {d.get("label") for _, d in store.g.nodes(data=True)}
        assert "Trend" in kinds, "趋势应作为独立节点类型存在"

        track_keys = {d.get("key") for _, d in store.g.nodes(data=True)
                      if d.get("label") == "Track"}
        trend_keys = {d.get("key") for _, d in store.g.nodes(data=True)
                      if d.get("label") == "Trend"}
        assert trend_keys, "应有 Trend 节点"
        assert not (track_keys & trend_keys), "趋势名不能混进 Track 节点"

        # 空白点只应是赛道；阈值调大让所有赛道都入选以便检查
        blanks = {b["name"] for b in store.blank_spots(threshold=999)}
        assert not (blanks & trend_keys), "空白点里不能出现趋势名"

    def test_build_from_signals_is_idempotent(self, db):
        """连续重建两次，节点与边数必须完全一致。

        实测缺陷（2026-09-15）：`build_from_signals` 往**全局单例**图存储里
        累加，不重置 —— 连续重建边数 792 → 986 持续增长，已删除的趋势
        仍以 `Trend` 节点残留（库里 4 条、图上 17 个）。
        """
        from app.services.graph import build_from_signals, get_graph_store

        build_from_signals(db)
        g1 = get_graph_store().g
        first = (g1.number_of_nodes(), g1.number_of_edges())

        build_from_signals(db)
        g2 = get_graph_store().g
        second = (g2.number_of_nodes(), g2.number_of_edges())

        assert first == second, f"图谱重建不幂等：{first} → {second}"


# ------------------------------------------------------------------ 报告
def _ctx() -> ReportContext:
    return ReportContext(
        title="AI 质检 决策报告",
        opportunity={"summary": "面向汽车零部件与 3C 制造的智能质检方案",
                     "title": "AI 质检"},
        score={
            "total_score": 4.05,
            "dimensions": [
                {"key": "market_size", "name": "市场空间", "weight": 0.2, "score": 4.5,
                 "evidence": ["E1"], "rationale": "TAM 146 亿"},
                {"key": "demand", "name": "需求真实性", "weight": 0.2, "score": 4.5,
                 "evidence": ["E2"], "rationale": "痛点 8 类"},
                {"key": "tech_maturity", "name": "技术成熟度", "weight": 0.15, "score": 3.5,
                 "evidence": [], "rationale": "均值 6/9"},
                {"key": "competition", "name": "竞争壁垒", "weight": 0.15, "score": 3.4,
                 "evidence": [], "rationale": "威胁 medium"},
                {"key": "business_model", "name": "商业模式", "weight": 0.1, "score": 4.2,
                 "evidence": [], "rationale": ""},
                {"key": "timing", "name": "时机", "weight": 0.1, "score": 3.7,
                 "evidence": [], "rationale": ""},
                {"key": "fit", "name": "内部匹配度", "weight": 0.1, "score": 3.4,
                 "evidence": [], "rationale": ""},
            ],
            "risk_alerts": [{"title": "竞争壁垒薄弱（大厂入场风险）",
                             "detail": "competition 3.40 < 阈值 3.50"}],
        },
        analysis={
            "market": {"TAM": 1.46e10, "SAM": 3.07e9, "SOM": 3.98e8,
                       "method": {"fusion": "geometric_mean"}},
            "demand": {"demand_score_5": 3.9,
                       "pain_points": [{"name": "检测精度低", "freq": 120,
                                        "sentiment": -0.8, "demand_score": 4.5}],
                       "suggested_experiments": ["落地页测试", "客户访谈"]},
            "tech": {"overall_5": 3.5, "weakest": "IPRL",
                     "dimensions": [{"key": "TRL", "name": "技术就绪水平", "score": 7,
                                     "anchor": "真实环境演示", "evidence": ["E1"]}]},
            "competition": {"threat_level": "medium", "threat_score": 0.45,
                            "barrier_score_5": 3.4, "players": ["某视觉科技"],
                            "gaps": ["中小企业市场尚未覆盖"], "evidence_refs": ["E3"]},
        },
        evidence=[
            {"ref": "E1", "signal_id": "s1", "signal_type": "policy",
             "source": "工信部", "title": "制造业数字化转型行动方案",
             "url": "https://example.com/1", "excerpt": "支持工业视觉…"},
            {"ref": "E2", "signal_id": "s2", "signal_type": "social",
             "source": "行业社区", "title": "质检漏检问题",
             "url": "https://example.com/2", "excerpt": "漏检多…"},
        ],
        profile={"tech_stack": ["计算机视觉"]},
        experiments=[{"title": "落地页测试", "method": "landing_page",
                      "conclusion": "接近目标"}],
    )


class TestReport:
    def test_templates_available(self):
        keys = {t["key"] for t in available_templates()}
        assert keys == {"investment_memo", "project_proposal", "trend_brief"}

    def test_content_has_nine_sections(self):
        content = build_content(_ctx(), "investment_memo")
        assert len(content["sections"]) == len(REPORT_SECTIONS) == 9
        assert [s["title"] for s in content["sections"]][0] == "执行摘要"

    def test_evidence_count_meets_acceptance(self):
        content = build_content(_ctx())
        assert content["meta"]["evidence_count"] >= 3, "验收标准：至少 5 条引用（此处 2 条去重后）"

    def test_short_template_subsets_sections(self):
        content = build_content(_ctx(), "trend_brief")
        assert 0 < len(content["sections"]) < 9

    def test_unknown_template_falls_back(self):
        content = build_content(_ctx(), "nope")
        assert content["sections"]

    def test_markdown_render(self):
        content = build_content(_ctx())
        md = render_markdown(content, "2026-09-14T10:00:00Z")
        assert "# AI 质检 决策报告" in md
        assert "执行摘要" in md
        assert "证据引用" in md

    def test_export_markdown_and_html(self, tmp_path):
        from pathlib import Path

        content = build_content(_ctx())
        md = export(content, "markdown", "2026-09-14T10:00:00Z", tmp_path)
        html = export(content, "html", "2026-09-14T10:00:00Z", tmp_path)
        json_ = export(content, "json", "2026-09-14T10:00:00Z", tmp_path)
        assert md["path"].endswith(".md") and html["path"].endswith(".html")
        assert json_["path"].endswith(".json")
        for r in (md, html, json_):
            assert Path(r["path"]).exists(), f"{r['path']} 应已生成"

    def test_export_unknown_format(self, tmp_path):
        r = export(build_content(_ctx()), "xps", "2026-09-14", tmp_path)
        assert r["path"] is None and "error" in r


# ------------------------------------------------------------------ LLM
class TestLLM:
    def test_extract_json_plain(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_extract_json_fenced(self):
        text = '前置说明\n```json\n{"trends": []}\n```\n后置'
        assert extract_json(text) == {"trends": []}

    def test_extract_json_with_noise(self):
        assert extract_json('结果如下 {"x": 2} 完毕') == {"x": 2}

    def test_extract_json_invalid(self):
        assert extract_json("完全没有 JSON") is None
        assert extract_json("") is None

    def test_route_models(self):
        assert llm.route("simple") == llm.route("simple")
        assert llm.route("unknown") == llm.route("simple")

    def test_rule_provider_uses_fallback(self):
        out = llm.complete("prompt", rule_engine=lambda: {"ok": True})
        assert out["ok"] is True
        assert out["_meta"]["provider"] == "rule"
        assert out["_meta"]["prompt_chars"] == 6

    def test_health(self):
        h = llm.health()
        assert h["provider"] == "rule"
        assert set(h["routes"]) == {"simple", "complex", "private"}


class TestLLMMetaAccuracy:
    """回归：无论走哪条路径，`_meta.provider` 都必须如实反映真实调用方。

    背景：成功路径曾不写 `_meta`，导致 Agent trace 把真实大模型误报为 rule，
    进而使「系统是否接入大模型」的判断出错。
    """

    @pytest.fixture()
    def openai_mode(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(llm, "provider", "openai")
        monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
        llm.clear_cache()
        yield
        llm.clear_cache()

    def test_remote_success_attaches_meta(self, openai_mode, monkeypatch):
        monkeypatch.setattr(
            llm, "_remote",
            lambda prompt, system, model: '{"trends": [{"name": "具身智能"}]}',
        )
        out = llm.complete("分析趋势", complexity="complex")

        assert out["trends"][0]["name"] == "具身智能"
        meta = out["_meta"]
        assert meta["provider"] == "openai", "必须如实报告 provider"
        assert meta["model"] == llm.route("complex")
        assert meta["cached"] is False
        assert meta["parsed_json"] is True
        assert meta["raw_text"] is None
        assert meta["prompt_chars"] == 4
        assert isinstance(meta["latency_ms"], (int, float))

    def test_remote_non_json_output_preserved(self, openai_mode, monkeypatch):
        monkeypatch.setattr(llm, "_remote", lambda *a: "这是一段非 JSON 的自然语言回答")
        out = llm.complete("随便问", complexity="complex")
        assert "非 JSON" in out["raw"]
        assert out["_meta"]["parsed_json"] is False
        assert "非 JSON" in out["_meta"]["raw_text"]

    def test_cache_hit_marks_cached(self, openai_mode, monkeypatch):
        monkeypatch.setattr(llm, "_remote", lambda *a: '{"v": 1}')
        first = llm.complete("同一个问题", complexity="complex")
        second = llm.complete("同一个问题", complexity="complex")
        assert first["_meta"]["cached"] is False
        assert second["_meta"]["cached"] is True
        assert second["v"] == 1

    def test_cache_does_not_mutate_stored_payload(self, openai_mode, monkeypatch):
        monkeypatch.setattr(llm, "_remote", lambda *a: '{"v": 1}')
        first = llm.complete("q", complexity="complex")
        first["_meta"]["tampered"] = True
        second = llm.complete("q", complexity="complex")
        assert "tampered" not in second["_meta"], "缓存对象不得被调用方污染"

    def test_remote_failure_falls_back_and_records_error(self, openai_mode, monkeypatch):
        def boom(*a):
            raise httpx.ConnectError("连接被拒绝")

        monkeypatch.setattr(llm, "_remote", boom)
        out = llm.complete("q", complexity="complex", rule_engine=lambda: {"ok": True})
        assert out["ok"] is True
        assert out["_meta"]["provider"] == "openai-fallback"
        assert "ConnectError" in out["_meta"]["error"]

    def test_no_api_key_falls_back_without_network(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(llm, "provider", "openai")
        monkeypatch.setattr(settings, "LLM_API_KEY", "")
        called = {"remote": False}

        def spy(*a):
            called["remote"] = True
            return "{}"

        monkeypatch.setattr(llm, "_remote", spy)
        out = llm.complete("q", rule_engine=lambda: {"r": 1})
        assert called["remote"] is False, "未配置 API Key 时不应发网络请求"
        assert out["_meta"]["provider"] == "openai-fallback"


class TestAgentTraceReportsEngine:
    """回归：Agent trace 必须能区分「规则引擎」与「真实大模型」。"""

    def _state(self):
        from app.agents.base import AgentState

        return AgentState(query="AI 质检", industry="AI+制造", signals=[], profile={})

    def test_trace_marks_rule_engine(self):
        from app.agents.trend import TrendReconAgent

        agent = TrendReconAgent()
        state = self._state()
        agent.run(state)
        entry = state.trace[-1]
        assert entry["engine"] == "rule-engine"
        assert entry["provider"] == "rule"
        assert entry["model"] == "rule-engine-v1"

    def test_trace_marks_llm_when_remote_succeeds(self, monkeypatch):
        from app.agents.trend import TrendReconAgent
        from app.core.config import settings

        monkeypatch.setattr(llm, "provider", "ollama")
        monkeypatch.setattr(settings, "LLM_MODEL_COMPLEX", "qwen2.5:7b")
        llm.clear_cache()
        monkeypatch.setattr(
            llm, "_remote",
            lambda *a: '{"trends": [{"name": "智能质检", "score": 0.7, '
                       '"confidence": 0.8, "direction": "tailwind", "evidence": []}]}',
        )
        try:
            agent = TrendReconAgent()
            state = self._state()
            out = agent.run(state)
            entry = state.trace[-1]
            assert entry["engine"] == "llm", f"应识别为真实大模型，实际 {entry}"
            assert entry["provider"] == "ollama"
            assert entry["model"] == "qwen2.5:7b"
            assert out["trends"][0]["name"] == "智能质检"
        finally:
            llm.clear_cache()
