"""全局配置。

对齐《技术文档》§8.1 环境分层：dev / staging / prod 通过 APP_ENV 切换。
所有外部重型依赖（PostgreSQL / Neo4j / Milvus / Redis / Kafka）都以
「开关 + 适配器」形式暴露，未配置时自动降级为本地零依赖实现，
保证开发机无需任何中间件即可跑通端到端工作流。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # ---------- 基础 ----------
    APP_NAME: str = "赛道罗盘 TrackCompass"
    APP_ENV: str = "dev"  # dev | staging | prod
    API_PREFIX: str = "/api/v1"
    DEBUG: bool = True

    # ---------- 安全 ----------
    SECRET_KEY: str = "trackcompass-dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12

    # ---------- 存储 ----------
    # 空字符串 => 使用 SQLite（本地零依赖）；填 postgresql+psycopg://... 即切生产库
    DATABASE_URL: str = ""
    SQLITE_PATH: str = str(BASE_DIR / "trackcompass.db")

    # ---------- 认知层适配器 ----------
    VECTOR_BACKEND: str = "memory"  # memory | milvus
    MILVUS_URI: str = "http://localhost:19530"

    GRAPH_BACKEND: str = "memory"  # memory | neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "neo4j"

    EMBEDDING_BACKEND: str = "local"  # local(确定性哈希) | openai | ollama
    # 维度口径：local 与 openai 用 EMBEDDING_DIM；ollama 按模型自动识别（见 embeddings.py）
    EMBEDDING_DIM: int = 1536
    # ⚠️ 二者模型名体系不同，不可混用：
    #   EMBEDDING_MODEL        —— HuggingFace / OpenAI 兼容接口的名字（含组织前缀）
    #   EMBEDDING_OLLAMA_MODEL —— Ollama 仓库名，无组织前缀（bge-m3:latest）
    #   实测：向 Ollama 传 "BAAI/bge-m3" 会直接报 model not found
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_OLLAMA_MODEL: str = "bge-m3"

    # ---------- RAG（检索增强）----------
    # memory  —— 自建：进程内向量 + BM25 + RRF（零依赖，亚毫秒，可断言）
    # ragflow —— 外部 RAGFlow 服务（真语义嵌入，文档级切块，需要 API Key 与 dataset）
    # 全量替换已启用为默认；RAGFlow 未配置或不可达时自动降级 memory 并告警。
    RAG_BACKEND: str = "ragflow"
    RAGFLOW_BASE_URL: str = "http://localhost:9380"
    RAGFLOW_API_KEY: str = ""
    RAGFLOW_DATASET_ID: str = ""
    RAGFLOW_TIMEOUT_SECONDS: int = 30
    # 检索参数：similarity_threshold 过低会引入噪声，vector_similarity_weight 为
    # 向量分在融合中的权重（其余给关键词分）
    RAGFLOW_SIMILARITY_THRESHOLD: float = 0.2
    RAGFLOW_VECTOR_WEIGHT: float = 0.3
    # 信号元数据旁路映射表（RAGFlow 检索只返回正文，需本地映射回来源/类型/标题）
    RAGFLOW_MAP_PATH: str = str(BASE_DIR / "ragflow_index_map.json")
    # 上传后等待异步解析的最长秒数（超时不报错，仅告警，稍后可检索）
    RAGFLOW_PARSE_WAIT_SECONDS: int = 90
    # 是否让 RAGFlow 用**大模型抽取关键词**再检索（提升短查询召回）。
    # ⚠️ 开启前提：该 RAGFlow 租户的**默认对话模型**必须已注册在 tenant_llm 中，
    #    否则 RAGFlow 会抛 `LookupError: Tenant Model ... type chat not found`，
    #    导致检索整体失败。默认关闭 —— 只用向量 + BM25，无对话模型依赖。
    RAGFLOW_KEYWORD_EXTRACTION: bool = False

    # ---------- LLM ----------
    # provider: rule(离线规则引擎, 默认) | openai(兼容接口, 含 Qwen/DeepSeek/本地 vLLM) | ollama
    LLM_PROVIDER: str = "rule"
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL_SIMPLE: str = "Qwen2.5-7B"
    LLM_MODEL_COMPLEX: str = "gpt-4o"
    LLM_MODEL_PRIVATE: str = "Qwen2.5-72B"
    LLM_CACHE_TTL_HOURS: int = 24
    LLM_TIMEOUT_SECONDS: int = 60

    # ---------- Ollama 专用（provider=ollama 时生效）----------
    # 关闭 thinking：qwen3.8 等模型默认先输出一段推理再给答案。实测同一请求
    # think=false 0.9s vs 默认 2.6s（快 2.9×），而 Agent 任务是「按给定 Schema 抽结构」，
    # 不需要链式推理。
    LLM_OLLAMA_THINK: bool = False
    # 强制 JSON 输出（Ollama 的 structured output）：既避免解析失败，也抑制跑题长文。
    LLM_OLLAMA_JSON_FORMAT: bool = True
    # 单次生成上限。模型自带 num_predict=8192，Agent 的结构化输出远用不到 ——
    # 不封顶会放大最坏情况延迟（实测出现过单 Agent 129s）。
    LLM_OLLAMA_NUM_PREDICT: int = 2048

    # ---------- 功能开关 ----------
    ENABLE_MONITOR_SCHEDULER: bool = False  # 对应 service/scheduler.py
    ENABLE_LLM_REPORT_POLISH: bool = False

    # ---------- 前端静态托管 ----------
    # 开启后，后端在 / 直接托管 frontend/dist 构建产物，
    # 实现「单端口同时服务 UI 与 API」，无需再单独跑 Vite dev server。
    SERVE_FRONTEND: bool = True
    FRONTEND_DIST: str = str(Path(BASE_DIR).parent / "frontend" / "dist")

    @property
    def sqlalchemy_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"sqlite:///{self.SQLITE_PATH}"

    @property
    def is_sqlite(self) -> bool:
        return self.sqlalchemy_url.startswith("sqlite")

    @property
    def frontend_dist(self) -> Path:
        return Path(self.FRONTEND_DIST)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
