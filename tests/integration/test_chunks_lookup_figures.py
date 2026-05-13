"""tests/integration/test_chunks_lookup_figures.py — 真起 PG 验证
`lookup_figures_by_heading_path` 接口(DEV_SPEC §3.2.3 规则 3)。

覆盖:
- 按 heading_path_id 拉同节所有 table/figure chunk
- 按 relative_chunk_index 升序返回
- cap 封顶生效(超过 cap 张时只返前 cap 张)
- 跨 heading_path 隔离
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

    sid = f"test_lookup_fig_{uuid.uuid4().hex[:8]}"
    upsert_source(source_id=sid, file_name=f"{sid}.pdf", doc_type="textbook")
    yield sid
    with session_scope() as s:
        s.execute(text("DELETE FROM chunks WHERE source_id = :sid"), {"sid": sid})
        s.execute(text("DELETE FROM sources WHERE source_id = :sid"), {"sid": sid})


def _make_parent(sid: str, hp_id: str, suffix: str) -> dict:
    return {
        "chunk_id": f"parent_{sid}_{suffix}",
        "source_id": sid,
        "heading_path_id": hp_id,
        "heading_path": f"第{suffix}章",
        "relative_chunk_index": "parent",
        "parent_chunk_id": None,
        "chunk_type": "parent",
        "chunk_raw_text": f"父块 {suffix}",
        "content_hash": f"hash_p_{suffix}",
        "embedding_status": "skip",
    }


def _make_figure(sid: str, hp_id: str, page_idx: int, block_idx: int) -> dict:
    # chunk_id 含 hp_id 后缀避免跨 hp 同 (page,block) 时冲突(测试场景)
    cid = f"fig_{hp_id}_p{page_idx}_b{block_idx}"
    return {
        "chunk_id": cid,
        "source_id": sid,
        "heading_path_id": hp_id,
        "heading_path": "figure 节",
        "relative_chunk_index": f"figure:p{page_idx}_b{block_idx}",
        "parent_chunk_id": None,
        "chunk_type": "figure",
        "image_path": f"/imgs/{cid}.jpg",
        "chunk_raw_text": f"图 {page_idx}-{block_idx} caption",
        "medical_statement": "应仅作召回辅助,但 lookup 函数仍返回字段供其他用途",
        "content_hash": f"hash_fig_{hp_id}_{page_idx}_{block_idx}",
        "embedding_status": "done",
    }


def _make_table(sid: str, hp_id: str, page_idx: int, block_idx: int) -> dict:
    cid = f"tbl_{hp_id}_p{page_idx}_b{block_idx}"
    return {
        "chunk_id": cid,
        "source_id": sid,
        "heading_path_id": hp_id,
        "heading_path": "table 节",
        "relative_chunk_index": f"table:p{page_idx}_b{block_idx}",
        "parent_chunk_id": None,
        "chunk_type": "table",
        "image_path": f"/imgs/{cid}.jpg",
        "chunk_raw_text": f"<table>page {page_idx} block {block_idx}</table>",
        "medical_statement": "table medical statement",
        "content_hash": f"hash_tbl_{hp_id}_{page_idx}_{block_idx}",
        "embedding_status": "done",
    }


def test_returns_figures_and_tables_under_same_heading_path(fresh_source_id):
    from src.agent.utils.chunks_lookup import lookup_figures_by_heading_path
    from src.db.postgres.models import bulk_upsert_chunks

    hp = f"hp_{fresh_source_id}_A"
    bulk_upsert_chunks([
        _make_parent(fresh_source_id, hp, "A"),
        _make_figure(fresh_source_id, hp, 1, 1),
        _make_table(fresh_source_id, hp, 1, 2),
        _make_figure(fresh_source_id, hp, 2, 1),
    ])

    result = lookup_figures_by_heading_path([hp], cap=5)
    assert hp in result
    chunk_types = {item["chunk_type"] for item in result[hp]}
    assert chunk_types == {"figure", "table"}
    assert len(result[hp]) == 3


def test_cap_truncates_to_first_k_by_relative_index(fresh_source_id):
    from src.agent.utils.chunks_lookup import lookup_figures_by_heading_path
    from src.db.postgres.models import bulk_upsert_chunks

    hp = f"hp_{fresh_source_id}_B"
    bulk_upsert_chunks([
        _make_parent(fresh_source_id, hp, "B"),
        # 故意倒序灌库,验证 cap 截断仍按 relative_chunk_index 升序生效
        _make_figure(fresh_source_id, hp, 9, 9),
        _make_figure(fresh_source_id, hp, 5, 5),
        _make_figure(fresh_source_id, hp, 3, 3),
        _make_figure(fresh_source_id, hp, 1, 1),
        _make_figure(fresh_source_id, hp, 2, 2),
        _make_figure(fresh_source_id, hp, 4, 4),
    ])

    result = lookup_figures_by_heading_path([hp], cap=3)
    assert len(result[hp]) == 3
    rel_indices = [item["relative_chunk_index"] for item in result[hp]]
    # 字符串升序:"figure:p1_b1" < "figure:p2_b2" < "figure:p3_b3" < ...
    assert rel_indices == sorted(rel_indices)
    assert rel_indices[0] == "figure:p1_b1"


def test_isolates_across_heading_paths(fresh_source_id):
    from src.agent.utils.chunks_lookup import lookup_figures_by_heading_path
    from src.db.postgres.models import bulk_upsert_chunks

    hp1 = f"hp_{fresh_source_id}_X"
    hp2 = f"hp_{fresh_source_id}_Y"
    bulk_upsert_chunks([
        _make_parent(fresh_source_id, hp1, "X"),
        _make_parent(fresh_source_id, hp2, "Y"),
        _make_figure(fresh_source_id, hp1, 1, 1),
        _make_figure(fresh_source_id, hp2, 1, 1),
    ])

    result = lookup_figures_by_heading_path([hp1, hp2], cap=5)
    assert len(result[hp1]) == 1
    assert len(result[hp2]) == 1
    assert result[hp1][0]["chunk_id"].startswith("fig_") and hp1 in str(result)


def test_empty_input_returns_empty():
    from src.agent.utils.chunks_lookup import lookup_figures_by_heading_path
    assert lookup_figures_by_heading_path([]) == {}
    assert lookup_figures_by_heading_path([""]) == {}


def test_nonexistent_heading_path_returns_empty():
    from src.agent.utils.chunks_lookup import lookup_figures_by_heading_path
    result = lookup_figures_by_heading_path(["hp_does_not_exist"])
    assert result == {}
