"""src/agent/utils/chunks_lookup.py — chunks 表批量回查(DEV_SPEC §3.2.2 / §3.2.3)。

提供两个 PG 查询 helper:
- `lookup_chunk_summary_question(chunk_ids)` → fusion 用,补齐 summary/question matched_text
- `lookup_chunk_content(chunk_ids)`         → diagnose Step 0.5 父块扩展用,取
   chunk_raw_text / medical_statement / parent_chunk_id

设计原则:
- 一次查询,batch 友好(IN 子句),避免 N+1
- 缺 chunk 不抛错,返回空字段(下游用 `or ""` / `or []` 兜底)
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select

from src.db.postgres.connection import session_scope
from src.db.postgres.models import Chunk


def lookup_chunk_summary_question(chunk_ids: Iterable[str]) -> dict[str, dict]:
    """fusion.fuse_routes 的 pg_chunk_lookup 注入函数。

    Returns:
        {chunk_id: {"summary": str | None, "hypothetical_questions": list[str] | None}}

    缺 chunk_id 不在返回 dict 里,fusion 层会兜底为 ""。
    """
    ids = [cid for cid in chunk_ids if cid]
    if not ids:
        return {}

    stmt = select(
        Chunk.chunk_id, Chunk.summary, Chunk.hypothetical_questions
    ).where(Chunk.chunk_id.in_(ids))

    with session_scope() as s:
        rows = s.execute(stmt).all()

    return {
        row.chunk_id: {
            "summary": row.summary,
            "hypothetical_questions": row.hypothetical_questions,
        }
        for row in rows
    }


def lookup_chunk_content(chunk_ids: Iterable[str]) -> dict[str, dict]:
    """diagnose Step 0 / 0.5 用:批量取 chunk 文本 + 父块 id。

    Returns:
        {chunk_id: {
            "chunk_raw_text":     str,
            "medical_statement":  str | None,    # 图表 chunk 才非空
            "parent_chunk_id":    str | None,
            "summary":            str | None,
            "title":              str | None,
        }}
    """
    ids = [cid for cid in chunk_ids if cid]
    if not ids:
        return {}

    stmt = select(
        Chunk.chunk_id,
        Chunk.chunk_raw_text,
        Chunk.medical_statement,
        Chunk.parent_chunk_id,
        Chunk.summary,
        Chunk.title,
    ).where(Chunk.chunk_id.in_(ids))

    with session_scope() as s:
        rows = s.execute(stmt).all()

    return {
        row.chunk_id: {
            "chunk_raw_text": row.chunk_raw_text,
            "medical_statement": row.medical_statement,
            "parent_chunk_id": row.parent_chunk_id,
            "summary": row.summary,
            "title": row.title,
        }
        for row in rows
    }
