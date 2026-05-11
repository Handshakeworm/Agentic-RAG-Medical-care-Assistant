"""tests/integration/test_docs_collection.py — 真起 docs_collection 验证 ensure / upsert / search 闭环。

需要 Milvus 真服务在跑(`docker compose up -d milvus-standalone`)。
跳过条件:Milvus 不可达 → skip。

为了不污染生产 collection,本测试用临时 collection 名(test_docs_*),
跑完后自动 drop_collection 清理。
"""

from __future__ import annotations

import os
import socket
import uuid

import pytest


def _milvus_alive() -> bool:
    host = os.getenv("MILVUS_HOST", "localhost")
    port = int(os.getenv("MILVUS_PORT", "19530"))
    try:
        socket.create_connection((host, port), timeout=2).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _milvus_alive(), reason="Milvus 不可达,启动 docker compose 后再跑")


# 运行时把 collection 名替换成临时名,跑完清理
@pytest.fixture(scope="module")
def temp_docs_collection():
    """用 monkeypatch 临时替换 DOCS_COLLECTION_NAME,避免污染生产 collection。"""
    import config.milvus_schema as schema_mod
    import src.db.milvus.docs_collection as dc_mod

    original_name = schema_mod.DOCS_COLLECTION_NAME
    test_name = f"test_docs_{uuid.uuid4().hex[:8]}"
    schema_mod.DOCS_COLLECTION_NAME = test_name
    dc_mod.DOCS_COLLECTION_NAME = test_name

    yield test_name

    # 清理
    dc_mod.drop_docs_collection()
    schema_mod.DOCS_COLLECTION_NAME = original_name
    dc_mod.DOCS_COLLECTION_NAME = original_name


def test_ensure_create_then_idempotent(temp_docs_collection) -> None:
    """ensure_docs_collection 第一次建表,第二次幂等返回同一 collection。"""
    from src.db.milvus.docs_collection import ensure_docs_collection

    coll1 = ensure_docs_collection()
    coll2 = ensure_docs_collection()
    assert coll1.name == coll2.name == temp_docs_collection


def test_upsert_and_dense_search_roundtrip(temp_docs_collection) -> None:
    """upsert 一条 original 记录 → dense_vector 搜回该记录。"""
    from src.db.milvus.docs_collection import (
        ensure_docs_collection,
        search_dense,
        upsert_chunks,
    )

    ensure_docs_collection()

    # 构造一条假记录:dense_vector 用确定性向量便于搜索回它
    fake_dense = [0.1] * 4096
    record = {
        "id": "test_chunk_001",
        "source_chunk_id": "test_chunk_001",
        "vector_type": "original",
        "dense_vector": fake_dense,
        "text_for_bm25": "腹痛是消化系统疾病常见症状",
        "original_content": "腹痛是消化系统疾病常见症状,可由胃炎、阑尾炎等多种原因引起",
        "source_id": "test_source_001",
    }
    n = upsert_chunks([record])
    assert n == 1

    # 用相同向量搜索,应该 Top-1 命中自己
    results = search_dense(query_vector=fake_dense, top_k=5)
    assert results, "search_dense 没召回任何结果"
    assert results[0]["id"] == "test_chunk_001"
    assert results[0]["score"] > 0.99  # COSINE 自相似 ≈ 1.0
    assert results[0]["original_content"].startswith("腹痛")


def test_bm25_full_text_search_finds_keyword(temp_docs_collection) -> None:
    """upsert 一批 original 记录,用关键词 BM25 检索能命中含该词的记录。"""
    from src.db.milvus.docs_collection import (
        ensure_docs_collection,
        search_sparse_bm25,
        upsert_chunks,
    )

    ensure_docs_collection()

    records = [
        {
            "id": f"bm25_test_{i}",
            "source_chunk_id": f"bm25_test_{i}",
            "vector_type": "original",
            "dense_vector": [0.0] * 4096,
            "text_for_bm25": text,
            "original_content": text,
            "source_id": "bm25_test_source",
        }
        for i, text in enumerate([
            "急性胆囊炎以右上腹剧烈疼痛为典型表现",
            "高血压患者应控制钠盐摄入并定期监测血压",
            "肺炎链球菌是社区获得性肺炎最常见病原体",
        ])
    ]
    upsert_chunks(records)

    # BM25 检索"胆囊炎",应命中第一条
    results = search_sparse_bm25(query_text="胆囊炎", top_k=3)
    assert results, "BM25 搜索无结果"
    top_contents = [r["original_content"] for r in results]
    assert any("胆囊炎" in c for c in top_contents), f"BM25 没命中胆囊炎,实际 top: {top_contents}"


def test_summary_record_has_empty_text_for_bm25(temp_docs_collection) -> None:
    """summary / question 记录的 text_for_bm25 必须空串(不进 BM25 倒排)。

    这是 §2.4.1 设计要求:summary/question 是 LLM 改写文本,不参与 BM25 关键词检索。
    本测试只验证 schema 接受空串字段(BM25 索引应该过滤空文本)。
    """
    from src.db.milvus.docs_collection import (
        ensure_docs_collection,
        search_dense,
        upsert_chunks,
    )

    ensure_docs_collection()

    summary_record = {
        "id": "test_chunk_002_summary",
        "source_chunk_id": "test_chunk_002",
        "vector_type": "summary",
        "dense_vector": [0.2] * 4096,
        "text_for_bm25": "",  # 关键:空串,不参与 BM25
        "original_content": "本节讲述心律失常的诊断流程",
        "source_id": "test_source_002",
    }
    n = upsert_chunks([summary_record])
    assert n == 1

    # dense 仍能搜到
    results = search_dense(query_vector=[0.2] * 4096, top_k=3)
    assert any(r["id"] == "test_chunk_002_summary" for r in results)


def test_count_chunks_reflects_upserts(temp_docs_collection) -> None:
    """count_chunks 应反映前面几个测试的累计 upsert 数(不是严格计数,验证 > 0)。"""
    from src.db.milvus.docs_collection import count_chunks

    n = count_chunks()
    assert n >= 5  # 1 + 3 + 1 = 至少 5 条
