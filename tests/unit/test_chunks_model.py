"""tests/unit/test_chunks_model.py — 锁住 Chunk ORM schema 与 spec §2.4.2 一致。

不连真 PG,只校验 SQLAlchemy 元数据。真 CRUD 走 tests/integration/test_chunks_crud.py。
"""

from __future__ import annotations

from sqlalchemy import ARRAY, Integer, String, Text

from src.db.postgres.models import Chunk


def test_chunks_table_name_and_pk() -> None:
    assert Chunk.__tablename__ == "chunks"
    pk_cols = [c.name for c in Chunk.__table__.primary_key.columns]
    assert pk_cols == ["chunk_id"]


def test_chunks_has_15_columns_per_spec_2_4_2() -> None:
    """spec §2.4.2:8 幂等字段 + 4 LLM 增强字段 + 3 运维字段 = 15 列。"""
    cols = {c.name for c in Chunk.__table__.columns}
    assert cols == {
        # 幂等字段(§3.1.4)
        "chunk_id",
        "source_id",
        "heading_path_id",
        "heading_path",
        "relative_chunk_index",
        "parent_chunk_id",
        "chunk_raw_text",
        "content_hash",
        # LLM 增强字段(§3.1.3)
        "title",
        "summary",
        "tags",
        "hypothetical_questions",
        # 运维字段
        "embedding_status",
        "created_at",
        "updated_at",
    }


def test_chunks_required_text_fields_not_null() -> None:
    """8 个幂等字段中除 parent_chunk_id 外全为 NOT NULL(spec §2.4.2)。"""
    table = Chunk.__table__
    for name in ("source_id", "heading_path_id", "heading_path",
                 "chunk_raw_text", "content_hash"):
        assert isinstance(table.c[name].type, Text)
        assert not table.c[name].nullable, f"{name} 应 NOT NULL"
    assert isinstance(table.c["relative_chunk_index"].type, Integer)
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


def test_llm_fields_are_nullable_text_arrays_or_text() -> None:
    """LLM 增强字段初次 upsert 时可全空,C4 enrichment 后填充。"""
    table = Chunk.__table__
    for name in ("title", "summary"):
        assert isinstance(table.c[name].type, Text)
        assert table.c[name].nullable
    for name in ("tags", "hypothetical_questions"):
        assert isinstance(table.c[name].type, ARRAY)
        assert isinstance(table.c[name].type.item_type, Text)
        assert table.c[name].nullable


def test_embedding_status_is_varchar20_with_default() -> None:
    """embedding_status:VARCHAR(20),默认 'pending';枚举值 pending/done/failed/skip(spec §2.4.2)。"""
    col = Chunk.__table__.c["embedding_status"]
    assert isinstance(col.type, String)
    assert col.type.length == 20
    assert not col.nullable
    # SQLAlchemy 把 server_default 包成 DefaultClause,值在 .arg.text 里
    assert "pending" in str(col.server_default.arg)
