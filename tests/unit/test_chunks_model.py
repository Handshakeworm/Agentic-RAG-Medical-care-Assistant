"""tests/unit/test_chunks_model.py — 锁住 Chunk ORM schema 与 spec §2.4.2 一致。

不连真 PG,只校验 SQLAlchemy 元数据。真 CRUD 走 tests/integration/test_chunks_crud.py。
"""

from __future__ import annotations

from sqlalchemy import ARRAY, String, Text

from src.db.postgres.models import Chunk


def test_chunks_table_name_and_pk() -> None:
    assert Chunk.__tablename__ == "chunks"
    pk_cols = [c.name for c in Chunk.__table__.primary_key.columns]
    assert pk_cols == ["chunk_id"]


def test_chunks_columns_match_spec_2_4_2() -> None:
    """spec §2.4.2(2026-05-12 单行多列重构):11 幂等/结构/内容 + 3 LLM 增强 + 3 运维 = 17 列。"""
    cols = {c.name for c in Chunk.__table__.columns}
    assert cols == {
        # 幂等字段(§3.1.4)
        "chunk_id",
        "source_id",
        "heading_path_id",
        "heading_path",
        "relative_chunk_index",
        "parent_chunk_id",
        # 结构字段(spec §2.4.2)
        "chunk_type",
        "image_path",
        "sub_type",
        # 内容字段
        "chunk_raw_text",
        "medical_statement",
        "content_hash",
        # LLM 增强字段(§3.1.3)
        "title",
        "summary",
        "hypothetical_questions",
        # 运维字段
        "embedding_status",
        "created_at",
        "updated_at",
    }


def test_chunks_required_text_fields_not_null() -> None:
    """幂等/内容字段中除 parent_chunk_id / medical_statement 外全为 NOT NULL(spec §2.4.2)。"""
    table = Chunk.__table__
    for name in ("source_id", "heading_path_id", "heading_path",
                 "chunk_raw_text", "content_hash"):
        assert isinstance(table.c[name].type, Text)
        assert not table.c[name].nullable, f"{name} 应 NOT NULL"
    # spec §3.1.4.2:relative_chunk_index 是 TEXT(子块 "0/1/2..." / 父块 "parent" / 图表 "table:p43_b3"|"figure:p63_b7")
    assert isinstance(table.c["relative_chunk_index"].type, Text)
    assert not table.c["relative_chunk_index"].nullable


def test_parent_chunk_id_is_nullable_self_fk() -> None:
    """parent_chunk_id 自引用 chunks.chunk_id,允许 NULL(顶层父块标识)。"""
    table = Chunk.__table__
    assert table.c["parent_chunk_id"].nullable
    fks = list(table.c["parent_chunk_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "chunks"
    assert fks[0].column.name == "chunk_id"


def test_source_id_fk_to_sources() -> None:
    """source_id FK→sources.source_id(spec §2.4.2)。"""
    fks = list(Chunk.__table__.c["source_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "sources"


def test_chunk_type_is_varchar20_with_default_child() -> None:
    """chunk_type:VARCHAR(20) NOT NULL DEFAULT 'child';枚举 parent/child/table/figure(spec §2.4.2)。"""
    col = Chunk.__table__.c["chunk_type"]
    assert isinstance(col.type, String)
    assert col.type.length == 20
    assert not col.nullable
    assert "child" in str(col.server_default.arg)


def test_image_path_and_sub_type_are_nullable() -> None:
    """image_path / sub_type:仅图表 chunk 用,普通文本 chunk 为 NULL(spec §2.4.2)。"""
    table = Chunk.__table__
    assert isinstance(table.c["image_path"].type, Text)
    assert table.c["image_path"].nullable
    assert isinstance(table.c["sub_type"].type, String)
    assert table.c["sub_type"].type.length == 20
    assert table.c["sub_type"].nullable


def test_medical_statement_is_nullable_text() -> None:
    """medical_statement:table / figure 装 LLM 医学陈述,child / parent 为 NULL(spec §2.4.2)。"""
    table = Chunk.__table__
    assert isinstance(table.c["medical_statement"].type, Text)
    assert table.c["medical_statement"].nullable


def test_llm_fields_are_nullable_text_arrays_or_text() -> None:
    """LLM 增强字段初次 upsert 时可全空,C4 enrichment 后填充。"""
    table = Chunk.__table__
    for name in ("title", "summary"):
        assert isinstance(table.c[name].type, Text)
        assert table.c[name].nullable
    assert isinstance(table.c["hypothetical_questions"].type, ARRAY)
    assert isinstance(table.c["hypothetical_questions"].type.item_type, Text)
    assert table.c["hypothetical_questions"].nullable


def test_embedding_status_is_varchar20_with_default() -> None:
    """embedding_status:VARCHAR(20),默认 'pending';枚举 pending/done/failed/skip(spec §2.4.2)。"""
    col = Chunk.__table__.c["embedding_status"]
    assert isinstance(col.type, String)
    assert col.type.length == 20
    assert not col.nullable
    assert "pending" in str(col.server_default.arg)
