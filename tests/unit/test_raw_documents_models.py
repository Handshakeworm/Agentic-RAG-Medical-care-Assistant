"""tests/unit/test_raw_documents_models.py — 锁住 ORM schema 与 spec §2.4.4 一致。

不连真 PG,只校验 SQLAlchemy 元数据(列名、类型、可空性、外键、主键)。
真 CRUD 走 tests/integration/test_raw_documents_crud.py。
"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB

from src.db.postgres.models import RawDocument, Source


def test_sources_table_name_and_pk() -> None:
    assert Source.__tablename__ == "sources"
    pk_cols = [c.name for c in Source.__table__.primary_key.columns]
    assert pk_cols == ["source_id"]


def test_raw_documents_table_name_and_pk() -> None:
    assert RawDocument.__tablename__ == "raw_documents"
    pk_cols = [c.name for c in RawDocument.__table__.primary_key.columns]
    assert pk_cols == ["source_id"]


def test_raw_documents_has_8_columns_per_spec_2_4_4() -> None:
    """spec §2.4.4 列了 8 列(source_id 兼 PK + 7 业务字段)。"""
    cols = {c.name for c in RawDocument.__table__.columns}
    assert cols == {
        "source_id",
        "file_name",
        "stored_at",
        "markdown_content",
        "content_list",
        "middle_data",
        "model_data",
        "pdf_path",
    }


def test_raw_documents_jsonb_fields_all_not_null() -> None:
    """spec §2.4.4 修订版:4 个 JSONB 字段全 NOT NULL,upsert 签名无可空分支。"""
    table = RawDocument.__table__
    for name in ("content_list", "middle_data", "model_data"):
        col = table.c[name]
        assert isinstance(col.type, JSONB), f"{name} 不是 JSONB"
        assert not col.nullable, f"{name} 应为 NOT NULL"


def test_raw_documents_text_fields_not_null() -> None:
    table = RawDocument.__table__
    for name in ("file_name", "markdown_content", "pdf_path"):
        col = table.c[name]
        assert isinstance(col.type, Text), f"{name} 不是 Text"
        assert not col.nullable, f"{name} 应为 NOT NULL"


def test_raw_documents_fk_to_sources_with_cascade() -> None:
    """source_id 必须 FK→sources.source_id,且 ON DELETE CASCADE(spec §2.4.4)。"""
    fks = list(RawDocument.__table__.c.source_id.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.column.table.name == "sources"
    assert fk.column.name == "source_id"
    assert fk.ondelete == "CASCADE"
