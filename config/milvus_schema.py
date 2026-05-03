"""Milvus Collection Schema 定义。

按 DEV_SPEC 2.4.1 / 2.4.6 节的字段约定,集中维护 Collection 名称、字段、索引参数,
供 src/db/milvus/ 下各 *_collection.py 模块构建 / 校验 / 重建 Collection 时引用。

**与 spec §2.4.1 的字段数差异**:
spec §2.4.1 列了 docs_collection 8 字段,但 pymilvus 2.5 BM25 内置全文检索要求**额外的
`SPARSE_FLOAT_VECTOR` 字段** + 一个 `Function` 把 `text_for_bm25` 映射成 sparse 向量。
所以本文件实现 9 字段(8 spec 字段 + `bm25_sparse`)。这是 pymilvus 实现细节,
spec 抽象描述里把 "BM25 内置" 简化为 `text_for_bm25` 一个字段;`bm25_sparse` 由
Function 自动维护,**业务代码 upsert / search 时不需要直接操作此字段**。
"""

from pymilvus import CollectionSchema, DataType, FieldSchema, Function, FunctionType


# Qwen3-Embedding-8B 输出维度
EMBEDDING_DIM = 4096


# ───────────────────────────────────────────────────────────────────────────
# terms_collection — 术语向量库(DEV_SPEC 2.4.6)
# ───────────────────────────────────────────────────────────────────────────
TERMS_COLLECTION_NAME = "terms_collection"

TERMS_FIELDS = [
    FieldSchema(
        name="id",
        dtype=DataType.VARCHAR,
        max_length=128,
        is_primary=True,
        auto_id=False,
        description="记录唯一 ID,格式 {concept_id}_{alias_index}",
    ),
    FieldSchema(
        name="concept_id",
        dtype=DataType.VARCHAR,
        max_length=64,
        description="概念主键。优先 ICD-10-CN(R10.4),其次 CMeSH ID,均无则 PROJECT_<hash>",
    ),
    FieldSchema(
        name="preferred_term",
        dtype=DataType.VARCHAR,
        max_length=256,
        description="该 concept 的标准首选术语,如「腹痛」",
    ),
    FieldSchema(
        name="alias",
        dtype=DataType.VARCHAR,
        max_length=256,
        description="本条记录的别名文本(被向量化的字段),如「肚子疼」/「abdominal pain」",
    ),
    FieldSchema(
        name="source_vocab",
        dtype=DataType.VARCHAR,
        max_length=16,
        description="别名来源:PROJECT / ICD10CN / CMESH / CHIP",
    ),
    FieldSchema(
        name="icd10",
        dtype=DataType.VARCHAR,
        max_length=16,
        description="ICD-10-CN 编码,无映射时为空字符串",
    ),
    FieldSchema(
        name="category",
        dtype=DataType.VARCHAR,
        max_length=16,
        description="概念类型:symptom / disease / anatomy / drug",
    ),
    FieldSchema(
        name="dense_vector",
        dtype=DataType.FLOAT_VECTOR,
        dim=EMBEDDING_DIM,
        description="alias 的 Qwen3-Embedding-8B 向量",
    ),
]

TERMS_SCHEMA = CollectionSchema(
    fields=TERMS_FIELDS,
    description="术语向量库 - 别名归一化检索(DEV_SPEC 2.4.6)",
    enable_dynamic_field=False,
)

TERMS_DENSE_INDEX = {
    "field_name": "dense_vector",
    "index_params": {
        "index_type": "HNSW",
        "metric_type": "COSINE",
        "params": {"M": 16, "efConstruction": 256},
    },
}

TERMS_SCALAR_INDEXES = [
    {"field_name": "concept_id", "index_params": {"index_type": "INVERTED"}},
    {"field_name": "category", "index_params": {"index_type": "INVERTED"}},
]


# ───────────────────────────────────────────────────────────────────────────
# docs_collection — 医学文献 chunk 多向量库(DEV_SPEC 2.4.1)
#
# 每个原始 chunk 在此对应 4~5 条记录(1 original + 1 summary + 2~3 question);
# 仅 original 记录的 `text_for_bm25` 有真实文本(参与 BM25 全文检索),
# summary / question 记录的 `text_for_bm25` 存空串(不参与 BM25,避免 LLM 改写文本带来语义漂移)。
# ───────────────────────────────────────────────────────────────────────────
DOCS_COLLECTION_NAME = "docs_collection"

# Milvus VARCHAR 单字段最大长度
_MAX_VARCHAR = 65535

DOCS_FIELDS = [
    FieldSchema(
        name="id",
        dtype=DataType.VARCHAR,
        max_length=128,
        is_primary=True,
        auto_id=False,
        description="本条记录唯一 ID:original={chunk_id};summary={chunk_id}_summary;question={chunk_id}_q{n}",
    ),
    FieldSchema(
        name="source_chunk_id",
        dtype=DataType.VARCHAR,
        max_length=128,
        description="所属原始 chunk_id(original 记录与 id 相同),命中后回查 PG chunks 表取展示字段",
    ),
    FieldSchema(
        name="vector_type",
        dtype=DataType.VARCHAR,
        max_length=16,
        description="original / summary / question",
    ),
    FieldSchema(
        name="dense_vector",
        dtype=DataType.FLOAT_VECTOR,
        dim=EMBEDDING_DIM,
        description="Qwen3-Embedding-8B 4096 维语义向量,所有记录均有",
    ),
    FieldSchema(
        name="text_for_bm25",
        dtype=DataType.VARCHAR,
        max_length=_MAX_VARCHAR,
        enable_analyzer=True,
        analyzer_params={"type": "chinese"},
        description="BM25 全文检索字段;仅 original 存原文,summary/question 存空串",
    ),
    FieldSchema(
        name="bm25_sparse",
        dtype=DataType.SPARSE_FLOAT_VECTOR,
        description="BM25 自动派生的稀疏向量(由下方 Function 维护,业务代码不直接写)",
    ),
    FieldSchema(
        name="original_content",
        dtype=DataType.VARCHAR,
        max_length=_MAX_VARCHAR,
        description="原始 chunk 文本,冗余存储,命中后无需回查 PG 即可拼上下文",
    ),
    FieldSchema(
        name="source_id",
        dtype=DataType.VARCHAR,
        max_length=128,
        description="Pre-filter 字段:按来源文档过滤(对应 §2.4.2 sources 表)",
    ),
    FieldSchema(
        name="tags",
        dtype=DataType.ARRAY,
        element_type=DataType.VARCHAR,
        max_capacity=20,
        max_length=64,
        description="Pre-filter 字段:LLM enrichment 阶段填充的主题标签",
    ),
]

# BM25 Function:把 text_for_bm25 字段自动派生成 bm25_sparse 稀疏向量
DOCS_BM25_FUNCTION = Function(
    name="bm25_text_to_sparse",
    function_type=FunctionType.BM25,
    input_field_names=["text_for_bm25"],
    output_field_names=["bm25_sparse"],
)

DOCS_SCHEMA = CollectionSchema(
    fields=DOCS_FIELDS,
    description="医学文献 chunk 多向量库(DEV_SPEC §2.4.1)",
    enable_dynamic_field=False,
    functions=[DOCS_BM25_FUNCTION],
)

DOCS_DENSE_INDEX = {
    "field_name": "dense_vector",
    "index_params": {
        "index_type": "HNSW",
        "metric_type": "COSINE",
        "params": {"M": 16, "efConstruction": 256},
    },
}

# BM25 稀疏索引(Milvus 2.4+ 内置;BM25 参数 k1/b 用经典默认值)
DOCS_SPARSE_INDEX = {
    "field_name": "bm25_sparse",
    "index_params": {
        "index_type": "SPARSE_INVERTED_INDEX",
        "metric_type": "BM25",
        "params": {"bm25_k1": 1.2, "bm25_b": 0.75},
    },
}

DOCS_SCALAR_INDEXES = [
    {"field_name": "source_chunk_id", "index_params": {"index_type": "INVERTED"}},
    {"field_name": "vector_type", "index_params": {"index_type": "INVERTED"}},
    {"field_name": "source_id", "index_params": {"index_type": "INVERTED"}},
]
