"""config/settings.py — Pydantic 模块级单例,所有运行时配置的权威入口。

业务代码强制约定:
    from config.settings import settings
    settings.agent_limits.MAX_FOLLOWUP_ROUNDS  # ✅
    MAX_FOLLOWUP_ROUNDS = 8                    # ❌ 模块级 hardcode 视为违规(DEV_SPEC §9.7.4)

所有段都按 .env 字段前缀分组(POSTGRES_*, LLM_*, AGENT_* 等),
缺项走默认值,可通过 .env 单独覆盖任一字段。
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ────────────────────────────────────────────────────────────────────────────
# §9.7 运行时常量(硬性上限 + 阈值,业务节点 import settings.agent_limits.X)
# ────────────────────────────────────────────────────────────────────────────


class AgentLimitsSettings(BaseSettings):
    """DEV_SPEC §9.7 七个权威常量。.env 用 AGENT_ 前缀覆盖。"""

    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")

    MAX_FOLLOWUP_ROUNDS: int = Field(8, description="追问轮次硬性兜底上限(信息增益正常收敛通常 3-5 轮)")
    MAX_EXAM_ROUNDS: int = Field(3, description="检查循环硬性上限")
    MAX_FOLLOWUP_QUESTIONS: int = Field(5, description="单轮追问问题条数上限(症状级 + 维度级合计)")
    RETRIEVE_TOP_N: int = Field(200, description="RRF 融合后 Top-N 截断")
    ASKABLE_GAIN_THRESHOLD: float = Field(0.15, description="可问症状信息增益阈值")
    ENTITY_LINKING_TIER2_THRESHOLD: float = Field(0.92, description="terms_collection 向量检索 Cosine 截断")
    RERANKER_CUTOFF_LAYERS: int | None = Field(None, description="layerwise early-exit 层数,None=全 28 层")


# ────────────────────────────────────────────────────────────────────────────
# 数据库连接
# ────────────────────────────────────────────────────────────────────────────


class PostgresSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POSTGRES_", env_file=".env", extra="ignore")

    HOST: str = "localhost"
    PORT: int = 5432
    DB: str = "medical_rag"
    USER: str = "admin"
    PASSWORD: str = "admin123"

    @property
    def dsn(self) -> str:
        return f"postgresql+psycopg://{self.USER}:{self.PASSWORD}@{self.HOST}:{self.PORT}/{self.DB}"


class MilvusSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MILVUS_", env_file=".env", extra="ignore")

    HOST: str = "localhost"
    PORT: int = 19530


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_", env_file=".env", extra="ignore")

    URL: str = "redis://localhost:6379"
    CONFIG_CACHE_TTL: int = 60
    RAG_CACHE_TTL: int = 3600  # 注:CLAUDE.md 明确 RAG 响应缓存当前未实现,此项预留


# ────────────────────────────────────────────────────────────────────────────
# 模型(本地 GPU 推理 + 云端 LLM)
# ────────────────────────────────────────────────────────────────────────────


class EmbeddingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", env_file=".env", extra="ignore")

    MODEL_PATH: str = "/data/embedding-model/Qwen--Qwen3-Embedding-8B"
    DEVICE: str = "cuda"
    DTYPE: str = "int8"  # bf16 / int8;int8 走 bitsandbytes 8bit,16GB 卡必需


class RerankerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RERANKER_", env_file=".env", extra="ignore")

    MODEL_PATH: str = "/data/reranker-model/BAAI--bge-reranker-v2-minicpm-layerwise"
    DEVICE: str = "cuda"
    CUTOFF_LAYER: int = 28  # 全 40 层,28 为质量/速度均衡点
    TIMEOUT_SECONDS: int = 5


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", extra="ignore")

    BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    API_KEY: str = Field(..., description="LLM API 密钥,必须由 .env 提供;缺失时立即抛 ValidationError(DEV_SPEC §8.3 A3)")
    MODEL_NAME: str = "qwen-max"


# ────────────────────────────────────────────────────────────────────────────
# RAG 超参(检索 / 切分,§3.x / §4.x 章节用)
# ────────────────────────────────────────────────────────────────────────────


class RetrievalSettings(BaseSettings):
    """注:RETRIEVE_TOP_N(§9.7)是融合后截断,与下面 SPARSE/DENSE/RERANK_TOP_K 是不同语义,不冲突。"""

    model_config = SettingsConfigDict(env_prefix="RETRIEVAL_", env_file=".env", extra="ignore")

    SPARSE_TOP_K: int = 20
    DENSE_TOP_K: int = 20
    RERANK_TOP_K: int = 5


class ChunkingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHUNK_", env_file=".env", extra="ignore")

    SIZE: int = 512
    OVERLAP: int = 64


# ────────────────────────────────────────────────────────────────────────────
# API / 安全 / 数据路径
# ────────────────────────────────────────────────────────────────────────────


class JWTSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JWT_", env_file=".env", extra="ignore")

    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60


class APISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_", env_file=".env", extra="ignore")

    HOST: str = "0.0.0.0"
    PORT: int = 8000
    RATE_LIMIT_PER_MINUTE: int = 30


class PathsSettings(BaseSettings):
    """数据 / 模型路径,无统一前缀,字段名直接对应 .env key。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PDF_INPUT_DIR: str = "/data/medical-resources/raw-pdf"
    MINERU_OUTPUT_DIR: str = "/data/medical-resources/mineru-output"
    MINERU_MODEL_SOURCE: str = "local"  # local / huggingface / modelscope
    MODELSCOPE_CACHE: str = "/data/mineru-models"


# ────────────────────────────────────────────────────────────────────────────
# 顶层聚合 + 模块级单例
# ────────────────────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    """顶层聚合,业务代码统一通过 settings.<段>.<字段> 读取。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENV: str = Field("development", description="development / production")

    agent_limits: AgentLimitsSettings = Field(default_factory=AgentLimitsSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    milvus: MilvusSettings = Field(default_factory=MilvusSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    jwt: JWTSettings = Field(default_factory=JWTSettings)
    api: APISettings = Field(default_factory=APISettings)
    paths: PathsSettings = Field(default_factory=PathsSettings)


settings = Settings()
"""模块级单例。业务代码:`from config.settings import settings`。"""
