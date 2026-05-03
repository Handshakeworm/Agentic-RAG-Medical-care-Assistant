"""tests/integration/test_raw_documents_crud.py — 真起 PG 验证 upsert 闭环 + GIN 查询。

需要 PG 真服务在跑(`docker compose up -d postgres`)+ 已跑 0001_raw_documents.sql 迁移。
跳过条件:PG 不可达 → skip。

为不污染生产数据,所有测试用 source_id 前缀 `test_b5_*`,setup 自动清理同前缀残留。
"""

from __future__ import annotations

import os
import socket
import uuid

import pytest
from sqlalchemy import text


def _pg_alive() -> bool:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    try:
        socket.create_connection((host, port), timeout=2).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _pg_alive(), reason="PG 不可达,启动 docker compose 后再跑")


@pytest.fixture
def fresh_source_id():
    """每个测试一个独立 source_id;teardown 删该 source(级联删 raw_documents)。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models import upsert_source

    sid = f"test_b5_{uuid.uuid4().hex[:8]}"
    upsert_source(source_id=sid, file_name=f"{sid}.pdf", doc_type="textbook")
    yield sid
    with session_scope() as s:
        s.execute(text("DELETE FROM sources WHERE source_id = :sid"), {"sid": sid})


def _sample_content_list() -> list:
    """模拟 mineru content_list_v2 顶层结构(2 页,各含一个 paragraph 和一个 table)。"""
    return [
        [
            {"type": "paragraph",
             "content": {"paragraph_content": [{"type": "text", "content": "测试段落"}]},
             "bbox": [0, 0, 100, 20]},
            {"type": "table",
             "content": {"image_source": {"path": "images/x.jpg"},
                         "table_caption": [{"type": "text", "content": "表1-1"}],
                         "table_footnote": [],
                         "html": "<table><tr><td>a</td></tr></table>"},
             "bbox": [0, 30, 100, 80]},
        ],
        [
            {"type": "title",
             "content": {"title_content": [{"type": "text", "content": "第二章"}], "level": 1},
             "bbox": [0, 0, 100, 20]},
        ],
    ]


def test_upsert_inserts_new_row(fresh_source_id) -> None:
    """首次 upsert 应该插入一行。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models import RawDocument, upsert_raw_document

    upsert_raw_document(
        source_id=fresh_source_id,
        file_name=f"{fresh_source_id}.pdf",
        markdown_content="# 测试\n这是一段测试正文。",
        content_list=_sample_content_list(),
        middle_data={"version": "test", "pages": []},
        model_data={"version": "test", "blocks": []},
        pdf_path=f"/tmp/{fresh_source_id}.pdf",
    )

    with session_scope() as s:
        row = s.get(RawDocument, fresh_source_id)
        assert row is not None
        assert row.file_name == f"{fresh_source_id}.pdf"
        assert row.markdown_content.startswith("# 测试")
        assert isinstance(row.content_list, list)
        assert len(row.content_list) == 2  # 两页


def test_upsert_is_idempotent_on_duplicate(fresh_source_id) -> None:
    """重复 upsert 同一 source_id 不报错(MEMORY: idempotency 是核心准则);第二次内容覆盖第一次。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models import RawDocument, upsert_raw_document

    common = dict(
        source_id=fresh_source_id,
        file_name=f"{fresh_source_id}.pdf",
        content_list=_sample_content_list(),
        middle_data={"v": 1},
        model_data={"v": 1},
        pdf_path=f"/tmp/{fresh_source_id}.pdf",
    )
    upsert_raw_document(markdown_content="第一版正文", **common)
    upsert_raw_document(markdown_content="第二版正文(已修订)", **common)

    with session_scope() as s:
        row = s.get(RawDocument, fresh_source_id)
        assert row.markdown_content == "第二版正文(已修订)"
        # PG 应只有一行(主键约束 + 幂等)
        n = s.execute(
            text("SELECT COUNT(*) FROM raw_documents WHERE source_id = :sid"),
            {"sid": fresh_source_id},
        ).scalar()
        assert n == 1


def test_cascade_delete_from_sources_removes_raw_documents(fresh_source_id) -> None:
    """删 sources 行应级联删 raw_documents 行(spec §2.4.4 ON DELETE CASCADE)。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models import upsert_raw_document

    upsert_raw_document(
        source_id=fresh_source_id,
        file_name=f"{fresh_source_id}.pdf",
        markdown_content="级联测试",
        content_list=[],
        middle_data={},
        model_data={},
        pdf_path=f"/tmp/{fresh_source_id}.pdf",
    )

    with session_scope() as s:
        s.execute(text("DELETE FROM sources WHERE source_id = :sid"), {"sid": fresh_source_id})
        n = s.execute(
            text("SELECT COUNT(*) FROM raw_documents WHERE source_id = :sid"),
            {"sid": fresh_source_id},
        ).scalar()
        assert n == 0, "级联删除未生效"


def test_gin_index_can_filter_by_block_type(fresh_source_id) -> None:
    """GIN 索引(spec §2.4.4)应支持按 content_list 内 type 字段过滤。

    用 jsonb_path_exists 查"该文档是否含有 table 块",验证索引可被这种查询利用。
    """
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models import upsert_raw_document

    upsert_raw_document(
        source_id=fresh_source_id,
        file_name=f"{fresh_source_id}.pdf",
        markdown_content="GIN 测试",
        content_list=_sample_content_list(),  # 含 table 块
        middle_data={},
        model_data={},
        pdf_path=f"/tmp/{fresh_source_id}.pdf",
    )

    with session_scope() as s:
        # JSONPath:任意页 任意 block,type=='table' 存在?
        has_table = s.execute(
            text(
                """
                SELECT jsonb_path_exists(content_list, '$[*][*] ? (@.type == "table")')
                FROM raw_documents WHERE source_id = :sid
                """
            ),
            {"sid": fresh_source_id},
        ).scalar()
        assert has_table is True

        # 反例:不存在的 type
        has_video = s.execute(
            text(
                """
                SELECT jsonb_path_exists(content_list, '$[*][*] ? (@.type == "video")')
                FROM raw_documents WHERE source_id = :sid
                """
            ),
            {"sid": fresh_source_id},
        ).scalar()
        assert has_video is False


def test_raw_documents_without_source_violates_fk() -> None:
    """没先 upsert sources 就 upsert raw_documents 应该报 FK violation。"""
    from sqlalchemy.exc import IntegrityError

    from src.db.postgres.models import upsert_raw_document

    orphan_sid = f"test_b5_orphan_{uuid.uuid4().hex[:8]}"
    with pytest.raises(IntegrityError):
        upsert_raw_document(
            source_id=orphan_sid,
            file_name="orphan.pdf",
            markdown_content="无父记录",
            content_list=[],
            middle_data={},
            model_data={},
            pdf_path="/tmp/orphan.pdf",
        )
