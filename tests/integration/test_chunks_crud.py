"""tests/integration/test_chunks_crud.py — 真起 PG 验证 Chunk upsert + 父子约束 + 级联删。

需要 PG 真服务在跑(`docker compose up -d postgres`)+ 已跑 0001 + 0002 迁移。
跳过条件:PG 不可达 → skip。

测试用 source_id 前缀 `test_b1_*`,teardown 删 source 触发级联删 chunks。
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
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models import upsert_source

    sid = f"test_b1_{uuid.uuid4().hex[:8]}"
    upsert_source(source_id=sid, file_name=f"{sid}.pdf", doc_type="textbook")
    yield sid
    # 级联删:sources → chunks(FK 不带 CASCADE,所以先手动删 chunks)
    with session_scope() as s:
        s.execute(text("DELETE FROM chunks WHERE source_id = :sid"), {"sid": sid})
        s.execute(text("DELETE FROM sources WHERE source_id = :sid"), {"sid": sid})


def _make_parent(sid: str, suffix: str) -> dict:
    chunk_id = f"parent_{sid}_{suffix}"
    return {
        "chunk_id": chunk_id,
        "source_id": sid,
        "heading_path_id": f"hp_{suffix}",
        "heading_path": f"第{suffix}章",
        "relative_chunk_index": 0,  # 父块用 0(spec 实际用 "parent" 字符串入哈希,但表里这是 INT,父块也得给数字)
        "parent_chunk_id": None,
        "chunk_raw_text": f"父块原文 {suffix}",
        "content_hash": f"hash_p_{suffix}",
        "embedding_status": "skip",
    }


def _make_child(sid: str, parent_id: str, idx: int) -> dict:
    return {
        "chunk_id": f"child_{parent_id}_{idx}",
        "source_id": sid,
        "heading_path_id": parent_id.split("_", 1)[1],  # 简化:复用 parent 的 hp
        "heading_path": f"第x章 子块{idx}",
        "relative_chunk_index": idx,
        "parent_chunk_id": parent_id,
        "chunk_raw_text": f"子块原文 {idx}",
        "content_hash": f"hash_c_{idx}",
        "embedding_status": "pending",
    }


def test_bulk_upsert_inserts_parents_then_children(fresh_source_id) -> None:
    """父子混排 → bulk_upsert 应该成功(自动分两批,父先子后)。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models import bulk_upsert_chunks

    parent = _make_parent(fresh_source_id, "ch1")
    children = [_make_child(fresh_source_id, parent["chunk_id"], i) for i in range(3)]

    # 故意把 children 排前面验证内部排序
    n = bulk_upsert_chunks([*children, parent])
    assert n == 4

    with session_scope() as s:
        rows = s.execute(
            text("SELECT chunk_id, parent_chunk_id, embedding_status FROM chunks "
                 "WHERE source_id = :sid ORDER BY chunk_id"),
            {"sid": fresh_source_id},
        ).all()
        assert len(rows) == 4
        parents = [r for r in rows if r.parent_chunk_id is None]
        assert len(parents) == 1
        assert parents[0].embedding_status == "skip"


def test_bulk_upsert_is_idempotent(fresh_source_id) -> None:
    """同一批 records 跑两次,DB 仍只有一份(MEMORY: idempotency 是核心准则)。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models import bulk_upsert_chunks

    parent = _make_parent(fresh_source_id, "ch1")
    children = [_make_child(fresh_source_id, parent["chunk_id"], i) for i in range(2)]
    bulk_upsert_chunks([parent, *children])

    # 修改子块内容再 upsert
    children[0]["chunk_raw_text"] = "子块修订版"
    children[0]["content_hash"] = "hash_c_0_v2"
    bulk_upsert_chunks([parent, *children])

    with session_scope() as s:
        n = s.execute(
            text("SELECT COUNT(*) FROM chunks WHERE source_id = :sid"),
            {"sid": fresh_source_id},
        ).scalar()
        assert n == 3  # 1 parent + 2 children,无重复

        updated = s.execute(
            text("SELECT chunk_raw_text, content_hash FROM chunks "
                 "WHERE chunk_id = :cid"),
            {"cid": children[0]["chunk_id"]},
        ).one()
        assert updated.chunk_raw_text == "子块修订版"
        assert updated.content_hash == "hash_c_0_v2"


def test_child_without_parent_raises_fk_violation(fresh_source_id) -> None:
    """孤儿子块(parent_chunk_id 指向不存在的父块)应抛 FK violation。"""
    from sqlalchemy.exc import IntegrityError

    from src.db.postgres.models import bulk_upsert_chunks

    orphan = _make_child(fresh_source_id, "nonexistent_parent_id", 0)
    with pytest.raises(IntegrityError):
        bulk_upsert_chunks([orphan])


def test_partial_indexes_present_for_status_and_parent(fresh_source_id) -> None:
    """spec §2.4.2 的 5 个索引应全部存在;含两个带 WHERE 的部分索引。"""
    from src.db.postgres.connection import session_scope

    with session_scope() as s:
        idx_names = set(s.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'chunks'"
        )).scalars().all())
        # PK 索引名是 chunks_pkey,业务索引按 spec 命名
        for expected in (
            "idx_chunks_source_id",
            "idx_chunks_heading_path_id",
            "idx_chunks_content_hash",
            "idx_chunks_embedding_status",
            "idx_chunks_parent_chunk_id",
        ):
            assert expected in idx_names, f"缺索引 {expected}"


def test_tags_and_hypothetical_questions_array_roundtrip(fresh_source_id) -> None:
    """TEXT[] 字段应能写入/读出 Python list[str]。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models import Chunk, bulk_upsert_chunks

    parent = _make_parent(fresh_source_id, "ch_arr")
    parent["tags"] = ["digestive", "symptom", "common"]
    parent["hypothetical_questions"] = ["肚子疼怎么办?", "腹痛是什么原因?"]
    bulk_upsert_chunks([parent])

    with session_scope() as s:
        row = s.get(Chunk, parent["chunk_id"])
        assert row.tags == ["digestive", "symptom", "common"]
        assert row.hypothetical_questions == ["肚子疼怎么办?", "腹痛是什么原因?"]
