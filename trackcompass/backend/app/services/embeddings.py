"""Embedding 服务。

三种后端：
  local  —— 确定性哈希嵌入（零依赖、可离线、结果稳定可复现），
            用于开发/测试与 CI；语义能力弱于真实模型，但保证流程可跑通。
  openai —— OpenAI 兼容接口（含 Qwen / DeepSeek / 本地 vLLM）。
  ollama —— 本地 Ollama（用户环境已有 qwen3.8:27b / bge-m3）。
"""

from __future__ import annotations

import hashlib
import logging
import math
from functools import lru_cache

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class BaseEmbedding:
    dim: int = 1536

    def embed(self, text: str) -> list[float]:  # pragma: no cover - interface
        raise NotImplementedError

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class LocalHashEmbedding(BaseEmbedding):
    """确定性哈希嵌入：把文本切分为中英文字符/词片段，投影到固定维度。

    实现要点：使用字符 1/2/3-gram + 英文词干哈希到桶，再做 L2 归一化。
    同一文本恒得同一向量，便于断言与回归；相似文本因共享 n-gram 而余弦相似度高。
    """

    def __init__(self, dim: int = 1536):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        if not text:
            return vec
        norm_text = text.lower()
        tokens: list[str] = []
        buf: list[str] = []
        for ch in norm_text:
            if ch.isascii() and ch.isalnum():
                buf.append(ch)
                continue
            if buf:
                tokens.append("".join(buf))
                buf = []
            if not ch.isspace():
                tokens.append(ch)
        if buf:
            tokens.append("".join(buf))

        grams: list[str] = list(tokens)
        for n in (2, 3):
            for i in range(len(tokens) - n + 1):
                grams.append("".join(tokens[i : i + n]))

        for gram in grams:
            digest = hashlib.md5(gram.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            weight = 1.0 / (1.0 + len(gram) * 0.1)
            vec[idx] += sign * weight

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class OpenAICompatibleEmbedding(BaseEmbedding):
    def __init__(self, base_url: str, api_key: str, model: str, dim: int):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        resp = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": text},
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()["data"][0]["embedding"]
        self.dim = len(data)
        return data


class OllamaEmbedding(BaseEmbedding):
    """本地 Ollama 嵌入（对应 settings.EMBEDDING_BACKEND=ollama）。

    三处与 OpenAI 兼容接口的差异（均为本机实测踩坑）：

      1) **模型名不同体系**：必须用 Ollama 仓库名 `bge-m3`，
         不能用 HuggingFace 名 `BAAI/bge-m3` —— 后者直接报
         `model "BAAI/bge-m3" not found, try pulling it first`。
         故此处取 `EMBEDDING_OLLAMA_MODEL`，不复用 `EMBEDDING_MODEL`。

      2) **端点不同**：新端点 `/api/embed`（入参 `input`，返回 `embeddings` 数组）；
         旧端点 `/api/embeddings`（入参 `prompt`，返回 `embedding`）仍可用但已弃用。
         实现按「新端点优先、旧端点兜底」，兼容不同 Ollama 版本。

      3) **维度不同**：bge-m3 是 1024 维，而 `EMBEDDING_DIM` 默认 1536
         （那是 OpenAI text-embedding-3-small 的维度）。若沿用配置值会导致
         向量与 Milvus schema / 既有入库向量不一致，故按模型名预先给出正确维度，
         并在每次调用后用真实返回长度校正。
    """

    #: Ollama 常见嵌入模型的输出维度（用于首次调用前就给出正确维度）
    KNOWN_DIMS: dict[str, int] = {
        "bge-m3": 1024,
        "bge-large": 1024,
        "mxbai-embed-large": 1024,
        "snowflake-arctic-embed": 1024,
        "nomic-embed-text": 768,
        "qwen3-embedding": 1024,
    }

    def __init__(self, model: str = "bge-m3", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")
        base = model.split(":")[0]
        self.dim = self.KNOWN_DIMS.get(base, settings.EMBEDDING_DIM)

    def embed(self, text: str) -> list[float]:
        # ---- 新端点优先 ----
        try:
            resp = httpx.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": text},
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            batch = resp.json().get("embeddings") or []
            if batch and batch[0]:
                self.dim = len(batch[0])
                return list(batch[0])
        except Exception as exc:  # 端点不存在 / 版本较旧 → 走兜底
            logger.debug("Ollama /api/embed 调用未成功，改用旧端点 /api/embeddings: %s", exc)

        # ---- 旧端点兜底 ----
        resp = httpx.post(
            f"{self.base_url}/api/embeddings",
            json={"model": self.model, "prompt": text},
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        vec = resp.json()["embedding"]
        self.dim = len(vec)
        return vec


@lru_cache
def get_embedder() -> BaseEmbedding:
    backend = settings.EMBEDDING_BACKEND.lower()
    try:
        if backend == "openai" and settings.LLM_API_KEY:
            return OpenAICompatibleEmbedding(
                settings.LLM_BASE_URL, settings.LLM_API_KEY,
                settings.EMBEDDING_MODEL, settings.EMBEDDING_DIM,
            )
        if backend == "ollama":
            # 注意用 Ollama 仓库名，不是 HuggingFace 名
            emb = OllamaEmbedding(model=settings.EMBEDDING_OLLAMA_MODEL)
            if emb.dim != settings.EMBEDDING_DIM:
                logger.info(
                    "Ollama 嵌入模型 %s 维度为 %d，与 EMBEDDING_DIM=%d 不同；"
                    "该后端按模型实际维度工作（切换后端会使既有向量失效，需重建索引）",
                    emb.model, emb.dim, settings.EMBEDDING_DIM,
                )
            return emb
    except Exception as exc:  # pragma: no cover
        logger.warning("embedding backend %s 初始化失败，降级为 local: %s", backend, exc)
    return LocalHashEmbedding(settings.EMBEDDING_DIM)


@lru_cache
def _local_fallback() -> LocalHashEmbedding:
    return LocalHashEmbedding(settings.EMBEDDING_DIM)


def embed(text: str) -> list[float]:
    """对外唯一嵌入入口。

    远端后端（ollama / openai）在**调用期**失败时降级为 local，而不是抛出 ——
    否则嵌入不可用会让整条 RAG / 画像链路直接中断。
    降级会打 WARNING 并标注维度，便于发现「后端其实没生效」。
    """
    embedder = get_embedder()
    try:
        return embedder.embed(text)
    except Exception as exc:
        if isinstance(embedder, LocalHashEmbedding):
            raise
        logger.warning(
            "嵌入后端 %s 调用失败，本次降级为 local(dim=%d)：%s",
            type(embedder).__name__, settings.EMBEDDING_DIM, exc,
        )
        return _local_fallback().embed(text)


def active_backend() -> str:
    """返回当前实际生效的嵌入后端名（供健康检查 / 管理接口如实上报）。"""
    return type(get_embedder()).__name__


def active_dim() -> int:
    """返回当前嵌入后端的向量维度（供健康检查 / 管理接口如实上报）。"""
    return get_embedder().dim


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(v * v for v in a[:n]))
    nb = math.sqrt(sum(v * v for v in b[:n]))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
