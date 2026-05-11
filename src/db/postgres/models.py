"""PostgreSQL ORM 模型 + 幂等 upsert 接口(DEV_SPEC §2.4.2 / §2.4.4)。

本文件涵盖:
- `Source`     ORM 类(§2.4.2 sources 表,raw_documents 的外键依赖)
- `RawDocument` ORM 类(§2.4.4 raw_documents 表,MinerU 解析产物)
- `upsert_raw_document(...)` 幂等写入接口(`INSERT ... ON CONFLICT (source_id) DO UPDATE`)

幂等约定:重跑 mineru 解析 / 重灌库不会因主键冲突报错(MEMORY: idempotency 是项目核心准则)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.db.postgres.connection import session_scope


class Base(DeclarativeBase):
    pass


class Source(Base):
    """sources 表(§2.4.2)— source_id 的权威注册表,raw_documents 通过 FK 引用。"""

    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RawDocument(Base):
    """raw_documents 表(§2.4.4)— MinerU 解析产物原样存储。

    4 个 JSONB 字段全 NOT NULL(spec §2.4.4 修订版):mineru 必产出 4 个文件,
    upsert 接口签名固定无可空分支。体积大头是 `middle_data`(典型 16-84MB,
    极端 300MB+,PG TOAST 自动行外存储)。
    """

    __tablename__ = "raw_documents"

    source_id: Mapped[str] = mapped_column(
        Text, ForeignKey("sources.source_id", ondelete="CASCADE"), primary_key=True
    )
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    stored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # MinerU 文本产物
    markdown_content: Mapped[str] = mapped_column(Text, nullable=False)

    # MinerU JSON 产物(jsonb 原样存)— 详见 §2.4.4.1 真实嵌套结构
    content_list: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSONB, nullable=False)
    middle_data: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSONB, nullable=False)
    model_data: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSONB, nullable=False)

    # 原始文件引用
    pdf_path: Mapped[str] = mapped_column(Text, nullable=False)


def upsert_raw_document(
    *,
    source_id: str,
    file_name: str,
    markdown_content: str,
    content_list: list | dict,
    middle_data: list | dict,
    model_data: list | dict,
    pdf_path: str,
) -> None:
    """幂等 upsert 一行 raw_documents。

    前置:`sources` 表必须已有对应 `source_id` 记录(FK 约束),否则会报
    `ForeignKeyViolation`。调用方负责先 upsert sources 行(典型由 C1 mineru_loader
    在解析阶段统一管理)。

    冲突策略:`ON CONFLICT (source_id) DO UPDATE`,所有内容字段全部覆盖,
    `stored_at` 刷新为当前时间。
    """
    stmt = pg_insert(RawDocument).values(
        source_id=source_id,
        file_name=file_name,
        markdown_content=markdown_content,
        content_list=content_list,
        middle_data=middle_data,
        model_data=model_data,
        pdf_path=pdf_path,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[RawDocument.source_id],
        set_={
            "file_name": stmt.excluded.file_name,
            "markdown_content": stmt.excluded.markdown_content,
            "content_list": stmt.excluded.content_list,
            "middle_data": stmt.excluded.middle_data,
            "model_data": stmt.excluded.model_data,
            "pdf_path": stmt.excluded.pdf_path,
            "stored_at": func.now(),
        },
    )
    with session_scope() as s:
        s.execute(stmt)


class Chunk(Base):
    """chunks 表(§2.4.2)— Chunk 元数据核心表。

    幂等约定:`chunk_id` 由 §3.1.4 规则确定性派生(C3 `compute_chunk_id`),
    重跑 chunking 同一份文档命中同一行,upsert 覆盖内容字段。

    父子关系(spec §3.1.2):
    - `parent_chunk_id IS NULL` → 顶层父块(整章节全文,`embedding_status='skip'`)
    - `parent_chunk_id` 非空 → 子块(被 splitter 切出来的小块,会向量化)
    - 写入时必须先父后子(self-FK 非 deferrable);`bulk_upsert_chunks` 自动分两批。
    """

    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_id: Mapped[str] = mapped_column(
        Text, ForeignKey("sources.source_id"), nullable=False
    )
    heading_path_id: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[str] = mapped_column(Text, nullable=False)
    # spec §3.1.4.2:子块用 "0/1/2...",特殊块用约定字符串("parent" / "table:p43_b3" / "figure:p63_b7")
    relative_chunk_index: Mapped[str] = mapped_column(Text, nullable=False)
    parent_chunk_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("chunks.chunk_id"), nullable=True
    )
    # spec §2.4.2:parent / child / table / figure(figure 涵盖原 mineru chart + flowchart)
    chunk_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="child"
    )
    # 图表截图相对路径(table / figure chunk 用);非图表 chunk 为 NULL
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # mineru sub_type(figure chunk 用,记录 'flowchart' 或原 chart 子类 'line'/'bar')
    sub_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    chunk_raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    # table / figure 专用:LLM 100-300 字医学陈述,dense `original` 向量来源(替代 chunk_raw_text)
    # spec §3.1.2 / §3.1.5;child / parent 此列为 NULL
    medical_statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)

    # LLM 增强字段(C4 enrichment 阶段填充,初次 upsert 可全空)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    hypothetical_questions: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )

    # 运维状态:pending / done / failed / skip(spec §2.4.2)
    embedding_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def _chunk_upsert_stmt(records: list[dict]):
    """构造 chunks 表 ON CONFLICT DO UPDATE 语句。

    覆盖所有内容字段 + 刷 updated_at;不动 created_at。
    """
    stmt = pg_insert(Chunk).values(records)
    return stmt.on_conflict_do_update(
        index_elements=[Chunk.chunk_id],
        set_={
            "source_id": stmt.excluded.source_id,
            "heading_path_id": stmt.excluded.heading_path_id,
            "heading_path": stmt.excluded.heading_path,
            "relative_chunk_index": stmt.excluded.relative_chunk_index,
            "parent_chunk_id": stmt.excluded.parent_chunk_id,
            "chunk_type": stmt.excluded.chunk_type,
            "image_path": stmt.excluded.image_path,
            "sub_type": stmt.excluded.sub_type,
            "chunk_raw_text": stmt.excluded.chunk_raw_text,
            "medical_statement": stmt.excluded.medical_statement,
            "content_hash": stmt.excluded.content_hash,
            "title": stmt.excluded.title,
            "summary": stmt.excluded.summary,
            "hypothetical_questions": stmt.excluded.hypothetical_questions,
            "embedding_status": stmt.excluded.embedding_status,
            "updated_at": func.now(),
        },
    )


# PG 单条 prepared statement 参数 ≤ 65535(uint16)。chunks 表 15 列 × N records,
# 安全阈值留 50% 余量 → 3000 records/批。
_CHUNK_INSERT_BATCH_SIZE = 3000


def bulk_upsert_chunks(records: list[dict], batch_size: int = _CHUNK_INSERT_BATCH_SIZE) -> int:
    """批量幂等 upsert chunks。返回处理的记录数。

    自动分两批:先 `parent_chunk_id IS NULL` 的父块,再子块——
    self-referential FK 非 deferrable,父块必须先存在。
    每批再按 `batch_size` 切片避免触发 PG 65535 参数上限。

    每条 record 必含 9 个核心字段(chunk_id / source_id / heading_path_id /
    heading_path / relative_chunk_index / parent_chunk_id / chunk_type /
    chunk_raw_text / content_hash);LLM 增强字段(title / summary /
    hypothetical_questions / medical_statement)、图表字段(image_path / sub_type)
    与 embedding_status 可缺省,DB 默认值或 NULL 兜底。
    """
    if not records:
        return 0

    parents = [r for r in records if r.get("parent_chunk_id") is None]
    children = [r for r in records if r.get("parent_chunk_id") is not None]

    with session_scope() as s:
        for i in range(0, len(parents), batch_size):
            s.execute(_chunk_upsert_stmt(parents[i : i + batch_size]))
        for i in range(0, len(children), batch_size):
            s.execute(_chunk_upsert_stmt(children[i : i + batch_size]))
    return len(records)


def upsert_source(
    *,
    source_id: str,
    file_name: str,
    file_path: str | None = None,
    doc_type: str | None = None,
) -> None:
    """幂等 upsert 一行 sources。raw_documents upsert 前必须先调它。"""
    stmt = pg_insert(Source).values(
        source_id=source_id,
        file_name=file_name,
        file_path=file_path,
        doc_type=doc_type,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Source.source_id],
        set_={
            "file_name": stmt.excluded.file_name,
            "file_path": stmt.excluded.file_path,
            "doc_type": stmt.excluded.doc_type,
            "updated_at": func.now(),
        },
    )
    with session_scope() as s:
        s.execute(stmt)
