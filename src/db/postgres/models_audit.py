"""PostgreSQL ORM 模型 — 审计系统 + 动态配置(DEV_SPEC §5.2.3 + §5.3)。

5 张表:
- `RagTrace`         per-session RAG 链路(§5.2.3.1)— G4 endpoint 写入(§9.6)
- `KbChangeLog`      知识库变更(§5.2.3.2)— G6 admin 上传/更新触发
- `ConfigChangeLog`  配置变更(§5.2.3.3)— admin 改 system_config 时同事务写
- `DiagnosisFeedback`反馈与标注(§5.2.3.4)— admin/审核员标注 trace
- `SystemConfig`     动态配置(§5.3)— LLM 温度 / Reranker 开关等运营软参

强约束(对齐 §9.6.5 / §9.1):
- 不写"AuditWriter 装饰器 / helper 类"。所有写入路径在 G4/G6 endpoint 视图内裸写
- `rag_trace.final_prompt` / `llm_raw_output` 正常路径全 NULL,只在 ⑩ diagnose
  失败兜底时填(§9.6.2 表)
- `rag_trace` 保留 90 天(§5.2.3.5),其余 audit/config 表永久保留 → 不在此处定义
  归档逻辑,见 §5.2.3.5(独立运维任务)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.postgres.models import Base


# ────────────────────────────────────────────────────────────────────────────
# §5.2.3.1 rag_trace — 链路追踪(15 字段,G4 写入)
# ────────────────────────────────────────────────────────────────────────────


class RagTrace(Base):
    """rag_trace 表(§5.2.3.1)— per-session 完整链路。

    15 字段对应 §9.6.2 数据来源对照表:
    - `intent_result` / `retrieved_chunks` / `reranked_chunks` / `token_usage` /
      `latency_ms` / `error_info` 全是 JSONB
    - `final_prompt` / `llm_raw_output` 仅 ⑩ 失败兜底填值,正常路径 NULL
    - `model_name` 来自 `settings.llm.MODEL_NAME`,MVP 全流程共用单模型(§9.6.2 注)

    索引(§5.2.3.1):
    - `idx_rag_trace_session  (session_id, created_at DESC)` — 按会话查链路
    - `idx_rag_trace_user     (user_id, created_at DESC)`    — 按患者查历史
    - `idx_rag_trace_created  (created_at)`                  — 按时间范围筛选

    上述 3 个索引在迁移脚本(0006)里建,ORM 不重复声明(SQLAlchemy 会自动同步)。
    """

    __tablename__ = "rag_trace"

    trace_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    session_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("sessions.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    raw_query: Mapped[str] = mapped_column(Text, nullable=False)
    intent_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    retrieved_chunks: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    reranked_chunks: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    final_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_response: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ────────────────────────────────────────────────────────────────────────────
# §5.2.3.2 kb_change_log — 知识库变更
# ────────────────────────────────────────────────────────────────────────────


class KbChangeLog(Base):
    """kb_change_log 表(§5.2.3.2)— admin 改知识库的变更历史。

    `operation`: `UPLOAD` / `UPDATE` / `DELETE` / `RECHUNK`
    """

    __tablename__ = "kb_change_log"

    change_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    operator_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prev_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_strategy: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    affected_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ────────────────────────────────────────────────────────────────────────────
# §5.2.3.3 config_change_log — 配置变更
# ────────────────────────────────────────────────────────────────────────────


class ConfigChangeLog(Base):
    """config_change_log 表(§5.2.3.3)— admin 修改 system_config 的变更历史。

    G6 admin 改配置时,同事务写一行(spec §5.3.1 末)。
    """

    __tablename__ = "config_change_log"

    change_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    operator_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    config_key: Mapped[str] = mapped_column(String(255), nullable=False)
    old_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ────────────────────────────────────────────────────────────────────────────
# §5.2.3.4 diagnosis_feedback — 反馈与标注
# ────────────────────────────────────────────────────────────────────────────


class DiagnosisFeedback(Base):
    """diagnosis_feedback 表(§5.2.3.4)— admin / 模拟审核员标注 trace。

    `rating`: `ACCURATE` / `INACCURATE` / `HALLUCINATION` / `TRIAGE_ERROR` / `PARTIAL`
    `expected_response`: 用于构建 golden dataset(I 阶段评估)
    """

    __tablename__ = "diagnosis_feedback"

    feedback_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    trace_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("rag_trace.trace_id"), nullable=False
    )
    reviewer_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    rating: Mapped[str] = mapped_column(String(32), nullable=False)
    rating_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ────────────────────────────────────────────────────────────────────────────
# §5.3 system_config — 动态配置(运行时运营可调软参)
# ────────────────────────────────────────────────────────────────────────────


class SystemConfig(Base):
    """system_config 表(§5.3)— 运营端可在线调优的软参。

    `value_type`: 供前端校验,取值 `INT` / `FLOAT` / `STRING` / `BOOL` / `JSON`
    缓存机制:应用读 Redis `config:<key>`(60s TTL),未命中回源本表(§5.1)。
    管理员改值 → 同事务写本表 + config_change_log(§5.3.1 末)。

    **不进本表的配置**(spec §5.3 列表):
    - `agent_limits` 7 个常量(§9.7)— 走 .env / settings.py
    - 基础设施连接串(PG/Milvus/Redis/DashScope/JWT secret)— 走 .env
    - Prompt 模板 — 走 src/prompts/ 文件 + 版本管理
    """

    __tablename__ = "system_config"

    key_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    value_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
