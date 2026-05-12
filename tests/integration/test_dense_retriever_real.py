"""tests/integration/test_dense_retriever.py — DEV_SPEC §3.2.2 Dense Route 真链路集成测试。

走真 Qwen3-Embedding-8B + 真 Milvus(临时 docs_collection 隔离),验证
search_dense_route 的端到端语义命中:
- 灌 3 条样本 chunk(各对应不同临床主题)→ 真 embedding 编码灌库
- 用一个语义相近的 query 跑 search_dense_route → Top-K 命中预期主题 chunk
- source_id_filter pre-filter 真起效

资源:Embedding 8B INT8 ≈ 9.3GB GPU 显存;**与 mineru 不能并发**(会 OOM)。
默认 skip(EMBEDDING_MODEL_PATH 未设置或 Milvus 不可达即 skip)。
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


pytestmark = [
    pytest.mark.skipif(
        not os.path.isdir(os.getenv("EMBEDDING_MODEL_PATH", "")),
        reason="EMBEDDING_MODEL_PATH 未指向已下载的 Qwen3-Embedding-8B 权重目录",
    ),
    pytest.mark.skipif(not _milvus_alive(), reason="Milvus 不可达,启动 docker compose 后再跑"),
]


@pytest.fixture(scope="module")
def temp_docs_collection():
    """临时 docs_collection 隔离生产数据。"""
    import config.milvus_schema as schema_mod
    import src.db.milvus.docs_collection as dc_mod

    original_name = schema_mod.DOCS_COLLECTION_NAME
    test_name = f"test_docs_dense_{uuid.uuid4().hex[:8]}"
    schema_mod.DOCS_COLLECTION_NAME = test_name
    dc_mod.DOCS_COLLECTION_NAME = test_name

    yield test_name

    dc_mod.drop_docs_collection()
    schema_mod.DOCS_COLLECTION_NAME = original_name
    dc_mod.DOCS_COLLECTION_NAME = original_name


@pytest.fixture(scope="module")
def seeded_with_real_embeddings(temp_docs_collection):
    """用真 embedding 编码 3 条临床主题 chunk 灌库。"""
    from src.db.milvus.docs_collection import ensure_docs_collection, upsert_chunks
    from src.models.embedding_model import get_embedding_model

    ensure_docs_collection()
    model = get_embedding_model()

    samples = [
        ("dense_chunk_01", "src_A",
         "急性胆囊炎以右上腹剧烈疼痛为典型表现,常伴发热、恶心、呕吐,Murphy 征阳性"),
        ("dense_chunk_02", "src_B",
         "社区获得性肺炎多以发热、咳嗽、咳痰起病,听诊可闻及湿啰音"),
        ("dense_chunk_03", "src_A",
         "糖尿病酮症酸中毒患者出现多饮多尿、深大呼吸、意识障碍,血糖显著升高"),
    ]
    vectors = model.encode([text for _, _, text in samples])
    records = [
        {
            "id": cid,
            "source_chunk_id": cid,
            "vector_type": "original",
            "dense_vector": vec,
            "text_for_bm25": text,
            "original_content": text,
            "source_id": src,
        }
        for (cid, src, text), vec in zip(samples, vectors, strict=True)
    ]
    upsert_chunks(records)
    return None


def test_dense_query_finds_semantically_closest_chunk(seeded_with_real_embeddings) -> None:
    """语义最近的主题 chunk 应排在 Top-1。"""
    from src.rag.retrieval.dense_retriever import search_dense_route

    # 与 dense_chunk_01(胆囊炎) 同主题的 query
    hits = search_dense_route("右上腹突然剧痛、发烧、恶心想吐", top_k=3)
    assert hits, "Dense ANN 无召回"
    top_ids = [h["source_chunk_id"] for h in hits]
    assert top_ids[0] == "dense_chunk_01", f"期待胆囊炎 chunk 排第一,实际 {top_ids}"


def test_dense_query_distinguishes_themes(seeded_with_real_embeddings) -> None:
    """换个主题 query → 命中对应的 chunk(肺炎主题应命中 dense_chunk_02)。"""
    from src.rag.retrieval.dense_retriever import search_dense_route

    hits = search_dense_route("咳嗽咳痰、肺部听诊有湿啰音", top_k=3)
    assert hits
    top_id = hits[0]["source_chunk_id"]
    assert top_id == "dense_chunk_02", f"期待肺炎 chunk 排第一,实际 {top_id}"


def test_source_id_pre_filter_excludes_other_sources(seeded_with_real_embeddings) -> None:
    """source_id_filter='src_A' → 只返 src_A(胆囊炎 + 酮症),排除 src_B(肺炎)。"""
    from src.rag.retrieval.dense_retriever import search_dense_route

    hits = search_dense_route(
        "咳嗽咳痰、肺部听诊有湿啰音",   # 与 src_B(肺炎)语义最近
        top_k=5,
        source_id_filter="src_A",
    )
    sources = {h["source_id"] for h in hits}
    assert sources <= {"src_A"}, f"pre-filter 失效,出现非 src_A: {sources}"
    chunk_ids = {h["source_chunk_id"] for h in hits}
    assert "dense_chunk_02" not in chunk_ids
