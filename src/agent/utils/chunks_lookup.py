"""src/agent/utils/chunks_lookup.py — chunks 表批量回查(DEV_SPEC §3.2.2 / §3.2.3)。

提供三个 PG 查询 helper:
- `lookup_chunk_summary_question(chunk_ids)` → fusion 用,补齐 summary/question matched_text
- `lookup_chunk_content(chunk_ids)`         → diagnose Step 0.5 父块扩展用,取
   chunk_raw_text / medical_statement / parent_chunk_id
- `lookup_figures_by_heading_path(heading_path_ids, cap)` → diagnose Context 扩展规则 3
   用,按 heading_path_id 批量查同节内的 figure/table chunk,按 relative_chunk_index
   升序、每节封顶 cap 条

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
    """diagnose Step 0.5 / Context 扩展用:批量取 chunk 文本 + 父块 id + 节标识。

    Returns:
        {chunk_id: {
            "chunk_raw_text":     str,
            "medical_statement":  str | None,    # 图表 chunk 才非空
            "parent_chunk_id":    str | None,
            "heading_path_id":    str,           # spec §3.2.3 规则 3 用于同节图表反查
            "chunk_type":         str,           # child / parent / table / figure
            "image_path":         str | None,    # table / figure 才非空
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
        Chunk.heading_path_id,
        Chunk.chunk_type,
        Chunk.image_path,
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
            "heading_path_id": row.heading_path_id,
            "chunk_type": row.chunk_type,
            "image_path": row.image_path,
            "summary": row.summary,
            "title": row.title,
        }
        for row in rows
    }


def lookup_figures_by_heading_path(
    heading_path_ids: Iterable[str],
    *,
    cap: int = 5,
) -> dict[str, list[dict]]:
    """diagnose Context 扩展规则 3 用:按 heading_path_id 批量查同节图表 chunk。

    spec §3.2.3 规则 3:父块进 prompt 时,把同节(`heading_path_id` 一致)所有
    `chunk_type ∈ {table, figure}` 的 chunk 拉出来,按 `relative_chunk_index` 升序
    保留前 `cap` 条(确定性顺序,无优先级判断;`cap` 来自
    `settings.agent_limits.RETRIEVE_PARENT_FIGURE_CAP`,默认 5)。

    Args:
        heading_path_ids: 父块的 heading_path_id 列表。
        cap: 每个 heading_path 保留的图表 chunk 数上限。

    Returns:
        {heading_path_id: [
            {
                "chunk_id":             str,
                "chunk_type":           "table" | "figure",
                "chunk_raw_text":       str,    # table=html+caption+footnote / figure=caption+footnote
                "image_path":           str | None,  # 截图路径(table / figure 都有)
                "title":                str | None,
                "relative_chunk_index": str,    # e.g. "figure:p343_b2",仅作调试/排序追溯
            },
            ...(最多 cap 条)
        ]}

    缺 heading_path_id 不在返回 dict 里(节内无图表的情况),下游遍历应兜底为空。
    """
    ids = [hpid for hpid in heading_path_ids if hpid]
    if not ids:
        return {}

    stmt = (
        select(
            Chunk.chunk_id,
            Chunk.heading_path_id,
            Chunk.chunk_type,
            Chunk.chunk_raw_text,
            Chunk.image_path,
            Chunk.title,
            Chunk.relative_chunk_index,
        )
        .where(Chunk.heading_path_id.in_(ids))
        .where(Chunk.chunk_type.in_(("table", "figure")))
        .order_by(Chunk.heading_path_id, Chunk.relative_chunk_index)
    )

    with session_scope() as s:
        rows = s.execute(stmt).all()

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        bucket = grouped.setdefault(row.heading_path_id, [])
        if len(bucket) >= cap:
            continue  # 节内封顶,前 cap 条已存(SQL ORDER BY 保证确定性)
        bucket.append(
            {
                "chunk_id": row.chunk_id,
                "chunk_type": row.chunk_type,
                "chunk_raw_text": row.chunk_raw_text,
                "image_path": row.image_path,
                "title": row.title,
                "relative_chunk_index": row.relative_chunk_index,
            }
        )
    return grouped
