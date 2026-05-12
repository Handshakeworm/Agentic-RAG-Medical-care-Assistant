"""PostgreSQL ORM 模型 — 会话与对话(DEV_SPEC §2.4.3)。

`sessions`     串联同一患者的一次完整问诊过程(active / closed / archived)
`conversations` 每条代表一次用户-系统交互,`rag_context` 存检索快照(chunk_id 列表 + 分数)

设计取舍:
- `conversations.user_id` 冗余存(spec §2.4.3 注:避免跨表 JOIN)
- 不写 ORM relationship —— spec §9.6 / §9.1 一致风格"裸代码不绕弯",查关联用显式 join
- LangGraph checkpointer 写不写这两张表是另一回事 —— G4 endpoint 在 graph.invoke 完成
  后从 final_state 摘要写入(每会话 1 行 sessions、每轮 1 行 conversations),与
  rag_trace(per-trace 1 行)并行存在,粒度不同
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.postgres.models import Base


class Session(Base):
    """sessions 表(§2.4.3)— 会话管理。

    `status`: `active` / `closed` / `archived`(用 VARCHAR 不收窄,§9.2 兼容)
    `title`: 可由 LLM 后处理生成摘要;G4 落库时可暂为 NULL
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Conversation(Base):
    """conversations 表(§2.4.3)— 每条对话一行。

    `rag_context`: 快照,典型 `{"chunk_ids": [...], "scores": [...]}`,JSONB 不预设结构
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    session_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("sessions.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    user_input: Mapped[str] = mapped_column(Text, nullable=False)
    llm_output: Mapped[str] = mapped_column(Text, nullable=False)
    rag_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
