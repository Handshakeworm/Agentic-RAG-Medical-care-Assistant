"""Milvus Collection Schema 定义。

按 DEV_SPEC 2.4.1 / 2.4.6 节的字段约定,集中维护 Collection 名称、字段、索引参数,
供 src/db/milvus/ 下各 *_collection.py 模块构建 / 校验 / 重建 Collection 时引用。

当前仅落地 terms_collection(2.4.6),docs_collection(2.4.1)留待 Ingestion Pipeline 阶段补齐。
"""

from pymilvus import CollectionSchema, DataType, FieldSchema


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
