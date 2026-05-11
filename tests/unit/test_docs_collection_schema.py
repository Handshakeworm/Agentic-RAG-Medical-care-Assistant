"""tests/unit/test_docs_collection_schema.py — 锁住 docs_collection schema(DEV_SPEC §2.4.1)。

不连真 Milvus,只验证 schema / 索引参数 / Function 配置跟 spec 一致。
真起 collection / upsert / search 走 tests/integration/test_docs_collection.py。
"""

from __future__ import annotations

from pymilvus import DataType, FunctionType


def test_docs_collection_has_8_fields() -> None:
    """spec §2.4.1 列了 7 业务字段;pymilvus 2.5 BM25 实现额外多 1 个 SPARSE_FLOAT_VECTOR 字段。"""
    from config.milvus_schema import DOCS_FIELDS

    assert len(DOCS_FIELDS) == 8
    field_names = {f.name for f in DOCS_FIELDS}
    expected = {
        "id", "source_chunk_id", "vector_type", "dense_vector",
        "text_for_bm25", "bm25_sparse", "original_content", "source_id",
    }
    assert field_names == expected


def test_docs_collection_pk_is_id_varchar() -> None:
    """`id` 必须是 PK + VARCHAR + auto_id=False(我们用确定性 ID,不要 Milvus 生成)。"""
    from config.milvus_schema import DOCS_FIELDS

    pk = next(f for f in DOCS_FIELDS if f.is_primary)
    assert pk.name == "id"
    assert pk.dtype == DataType.VARCHAR
    assert pk.auto_id is False


def test_dense_vector_dim_matches_embedding_model() -> None:
    """dense_vector 维度必须是 4096(Qwen3-Embedding-8B 输出维)。"""
    from config.milvus_schema import DOCS_FIELDS, EMBEDDING_DIM

    dv = next(f for f in DOCS_FIELDS if f.name == "dense_vector")
    assert dv.dtype == DataType.FLOAT_VECTOR
    assert dv.params["dim"] == EMBEDDING_DIM == 4096


def test_text_for_bm25_has_chinese_analyzer() -> None:
    """text_for_bm25 必须启用中文 analyzer,否则 BM25 全文检索对中文教科书无效。"""
    import json

    from config.milvus_schema import DOCS_FIELDS

    text_field = next(f for f in DOCS_FIELDS if f.name == "text_for_bm25")
    assert text_field.dtype == DataType.VARCHAR
    # pymilvus 2.5 把 analyzer_params 序列化成 JSON 字符串后存入 params
    assert text_field.params.get("enable_analyzer") is True
    analyzer_cfg = json.loads(text_field.params["analyzer_params"])
    assert analyzer_cfg.get("type") == "chinese"


def test_bm25_function_wires_text_to_sparse() -> None:
    """BM25 Function 必须把 text_for_bm25 映射成 bm25_sparse,这是 pymilvus 2.5 BM25 启用方式。"""
    from config.milvus_schema import DOCS_SCHEMA

    assert len(DOCS_SCHEMA.functions) == 1
    fn = DOCS_SCHEMA.functions[0]
    assert fn.type == FunctionType.BM25
    assert fn.input_field_names == ["text_for_bm25"]
    assert fn.output_field_names == ["bm25_sparse"]


def test_dense_index_uses_hnsw_cosine() -> None:
    """dense 向量索引:HNSW + COSINE,跟 terms_collection 风格一致(§2.4.1 暗示语义检索)。"""
    from config.milvus_schema import DOCS_DENSE_INDEX

    assert DOCS_DENSE_INDEX["field_name"] == "dense_vector"
    p = DOCS_DENSE_INDEX["index_params"]
    assert p["index_type"] == "HNSW"
    assert p["metric_type"] == "COSINE"


def test_sparse_index_uses_bm25() -> None:
    """sparse 索引必须是 SPARSE_INVERTED_INDEX + BM25 metric,Milvus 才会走 BM25 评分。"""
    from config.milvus_schema import DOCS_SPARSE_INDEX

    assert DOCS_SPARSE_INDEX["field_name"] == "bm25_sparse"
    p = DOCS_SPARSE_INDEX["index_params"]
    assert p["index_type"] == "SPARSE_INVERTED_INDEX"
    assert p["metric_type"] == "BM25"


def test_scalar_indexes_cover_filter_fields() -> None:
    """scalar INVERTED 索引必须覆盖检索时常用的 pre-filter 字段。"""
    from config.milvus_schema import DOCS_SCALAR_INDEXES

    indexed_fields = {idx["field_name"] for idx in DOCS_SCALAR_INDEXES}
    # vector_type:常需要 only original 过滤
    # source_id:按文档过滤
    # source_chunk_id:多向量去重(同 source_chunk_id 的多条记录回归一个 chunk)
    assert indexed_fields == {"source_chunk_id", "vector_type", "source_id"}
    for idx in DOCS_SCALAR_INDEXES:
        assert idx["index_params"]["index_type"] == "INVERTED"


