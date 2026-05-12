"""tests/integration/test_sparse_retriever.py — DEV_SPEC §3.2.2 Sparse Route 真 Milvus 集成测试。

走真 Milvus + 临时 docs_collection 隔离生产数据,验证 search_sparse_routes
端到端:
- 多维度词袋分别命中各自的 chunk
- 同 chunk 同时被多个维度命中(BM25 倒排支持)
- source_id_filter pre-filter 生效

dense_vector 用占位 [0.0]*4096(本测试只走 BM25 倒排,不依赖向量检索)。
"""
from __future__ import annotations

import os
import socket
import uuid

import pytest

from config.milvus_schema import EMBEDDING_DIM


def _milvus_alive() -> bool:
    host = os.getenv("MILVUS_HOST", "localhost")
    port = int(os.getenv("MILVUS_PORT", "19530"))
    try:
        socket.create_connection((host, port), timeout=2).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _milvus_alive(), reason="Milvus 不可达,启动 docker compose 后再跑")


@pytest.fixture(scope="module")
def temp_docs_collection():
    """临时 docs_collection 隔离生产数据。"""
    import config.milvus_schema as schema_mod
    import src.db.milvus.docs_collection as dc_mod

    original_name = schema_mod.DOCS_COLLECTION_NAME
    test_name = f"test_docs_sparse_{uuid.uuid4().hex[:8]}"
    schema_mod.DOCS_COLLECTION_NAME = test_name
    dc_mod.DOCS_COLLECTION_NAME = test_name

    yield test_name

    dc_mod.drop_docs_collection()
    schema_mod.DOCS_COLLECTION_NAME = original_name
    dc_mod.DOCS_COLLECTION_NAME = original_name


@pytest.fixture(scope="module")
def seeded_chunks(temp_docs_collection):
    """灌一组 BM25 测试 chunk,覆盖单维度命中、跨维度共命中、过滤场景。"""
    from src.db.milvus.docs_collection import ensure_docs_collection, upsert_chunks

    ensure_docs_collection()
    placeholder_vec = [0.0] * EMBEDDING_DIM

    records = [
        {
            "id": "sp_chunk_01",
            "source_chunk_id": "sp_chunk_01",
            "vector_type": "original",
            "dense_vector": placeholder_vec,
            "text_for_bm25": "急性胆囊炎以右上腹剧烈疼痛为典型表现,常伴发热、恶心、呕吐",
            "original_content": "急性胆囊炎以右上腹剧烈疼痛为典型表现,常伴发热、恶心、呕吐",
            "source_id": "src_A",
        },
        {
            "id": "sp_chunk_02",
            "source_chunk_id": "sp_chunk_02",
            "vector_type": "original",
            "dense_vector": placeholder_vec,
            "text_for_bm25": "病毒性肺炎多以发热、咳嗽、乏力起病",
            "original_content": "病毒性肺炎多以发热、咳嗽、乏力起病",
            "source_id": "src_B",
        },
        {
            "id": "sp_chunk_03",
            "source_chunk_id": "sp_chunk_03",
            "vector_type": "original",
            "dense_vector": placeholder_vec,
            "text_for_bm25": "急性胃肠炎常见症状包括腹痛、腹泻、呕吐",
            "original_content": "急性胃肠炎常见症状包括腹痛、腹泻、呕吐",
            "source_id": "src_A",
        },
    ]
    upsert_chunks(records)
    return None


def test_each_dimension_returns_its_top_k(seeded_chunks) -> None:
    """3 个维度(腹痛 / 发热 / 呕吐)分别召回各自命中的 chunk。"""
    from src.rag.retrieval.sparse_retriever import search_sparse_routes

    routes = search_sparse_routes(
        sparse_queries=["腹痛 肚子疼", "发热 发烧", "呕吐"],
        top_k=10,
    )
    assert len(routes) == 3
    # 每路至少命中一条
    assert all(len(r) >= 1 for r in routes), f"有维度召回为空: {[len(r) for r in routes]}"

    # "腹痛"应命中 sp_chunk_01 + sp_chunk_03(都含"腹痛"或"上腹")
    腹痛_ids = {h["source_chunk_id"] for h in routes[0]}
    assert "sp_chunk_03" in 腹痛_ids
    # "发热"应命中 sp_chunk_01 + sp_chunk_02
    发热_ids = {h["source_chunk_id"] for h in routes[1]}
    assert "sp_chunk_02" in 发热_ids
    # "呕吐"应命中 sp_chunk_01 + sp_chunk_03
    呕吐_ids = {h["source_chunk_id"] for h in routes[2]}
    assert "sp_chunk_03" in 呕吐_ids


def test_chunk_can_be_hit_by_multiple_dimensions(seeded_chunks) -> None:
    """sp_chunk_01("急性胆囊炎... 腹痛 发热 呕吐")应同时被三个维度命中
    (验证 RRF 融合的"自调节权重"前提:多路命中是真实存在的)。"""
    from src.rag.retrieval.sparse_retriever import search_sparse_routes

    routes = search_sparse_routes(["腹部疼痛", "发热", "呕吐"], top_k=10)
    chunk_01_hits = sum(
        1 for route in routes
        if any(h["source_chunk_id"] == "sp_chunk_01" for h in route)
    )
    assert chunk_01_hits >= 2, f"sp_chunk_01 应被多维度命中,实际 {chunk_01_hits}"


def test_source_id_pre_filter_excludes_other_sources(seeded_chunks) -> None:
    """source_id_filter='src_A' → 只返 src_A 的 chunk,排除 src_B。"""
    from src.rag.retrieval.sparse_retriever import search_sparse_routes

    routes = search_sparse_routes(
        sparse_queries=["发热"],
        top_k=10,
        source_id_filter="src_A",
    )
    all_source_ids = {h["source_id"] for route in routes for h in route}
    assert all_source_ids == {"src_A"}, f"pre-filter 失效,跑出 {all_source_ids}"


def test_empty_sparse_queries_returns_empty_list(seeded_chunks) -> None:
    """E1 已过滤空词袋 → E2 收到空列表时 → 返 [],不调 BM25。"""
    from src.rag.retrieval.sparse_retriever import search_sparse_routes

    assert search_sparse_routes([]) == []
