"""terms_collection — Milvus 术语向量库操作(DEV_SPEC 2.4.6)。

提供建库 / 写入 / 检索三组最小操作,供 terms/build_icd10.py(及未来其他 build_*.py)
灌库与 Agent Node ② / ④ 检索调用。schema 与索引参数集中在 config/milvus_schema.py。

幂等约定:写入路径用 upsert(基于确定性主键 id={concept_id}_{alias_index}),
重跑 ETL 不会产生重复记录。
"""

from __future__ import annotations

import os

from pymilvus import Collection, connections, utility

from config.milvus_schema import (
    TERMS_COLLECTION_NAME,
    TERMS_DENSE_INDEX,
    TERMS_SCALAR_INDEXES,
    TERMS_SCHEMA,
)


def _ensure_connection(alias: str = "default") -> None:
    if connections.has_connection(alias):
        return
    connections.connect(
        alias=alias,
        host=os.getenv("MILVUS_HOST", "localhost"),
        port=int(os.getenv("MILVUS_PORT", "19530")),
    )


def ensure_terms_collection(drop_existing: bool = False) -> Collection:
    """建表 + 建索引,幂等。drop_existing=True 时先删后建(重灌库用)。"""
    _ensure_connection()

    if utility.has_collection(TERMS_COLLECTION_NAME):
        if not drop_existing:
            return Collection(TERMS_COLLECTION_NAME)
        utility.drop_collection(TERMS_COLLECTION_NAME)

    coll = Collection(name=TERMS_COLLECTION_NAME, schema=TERMS_SCHEMA)
    coll.create_index(**TERMS_DENSE_INDEX)
    for idx in TERMS_SCALAR_INDEXES:
        coll.create_index(**idx)
    return coll


def upsert_aliases(records: list[dict]) -> int:
    """幂等批量写入。records 每项需含 schema 全部 8 字段。

    用 Milvus upsert(基于主键 `id`):同 id 的记录会被覆盖而非追加,
    重跑 ETL 不会产生重复(DEV_SPEC 项目级幂等约定)。
    """
    coll = ensure_terms_collection()
    result = coll.upsert(records)
    coll.flush()
    return result.upsert_count


def search_aliases(
    query_vector: list[float],
    top_k: int = 5,
    category_filter: str | None = None,
) -> list[dict]:
    """向量检索 Top-K,按 preferred_term 去重。

    同一 preferred_term 在 ICD-10 里常对应多个编码(如 R05 / R05.x / R05xx01
    都叫"咳嗽")。直接返回 Milvus Top-K 会让候选池被同概念的多码占满,
    下游 Agent ② 拿到的 Top-K 实际只代表 1-2 个独立概念,信息冗余。

    去重规则(确定性,保证幂等):
    - 同 preferred_term 只保留一条
    - 优先取 score 最高的(Milvus 已按 score 降序)
    - 当 score 几乎相等(差距 < 1e-6)时,取 concept_id 更短的(类目级 > 亚目级 > 临床扩展);
      长度相同则取字母序更小的
    - 最终 Top-K 是 K 个独立概念,信息密度最大化

    候选池放大 5 倍以保证去重后仍有 K 条结果。
    """
    coll = ensure_terms_collection()
    coll.load()

    expr = f'category == "{category_filter}"' if category_filter else None
    candidate_size = max(top_k * 5, 20)

    raw = coll.search(
        data=[query_vector],
        anns_field="dense_vector",
        param={"metric_type": "COSINE", "params": {"ef": 64}},
        limit=candidate_size,
        expr=expr,
        output_fields=[
            "concept_id", "preferred_term", "alias",
            "source_vocab", "icd10", "category",
        ],
    )[0]

    deduped: dict[str, dict] = {}
    for hit in raw:
        rec = {
            "concept_id": hit.entity.get("concept_id"),
            "preferred_term": hit.entity.get("preferred_term"),
            "alias": hit.entity.get("alias"),
            "source_vocab": hit.entity.get("source_vocab"),
            "icd10": hit.entity.get("icd10"),
            "category": hit.entity.get("category"),
            "score": hit.score,
        }
        pt = rec["preferred_term"]
        existing = deduped.get(pt)
        if existing is None:
            deduped[pt] = rec
            continue
        # 同 preferred_term 已存在;仅当 score 几乎相等且 concept_id 更权威时替换
        if abs(rec["score"] - existing["score"]) < 1e-6 and (
            len(rec["concept_id"]),
            rec["concept_id"],
        ) < (len(existing["concept_id"]), existing["concept_id"]):
            deduped[pt] = rec

    # Milvus 已按 score 降序;dict 保留插入顺序;替换不影响顺序
    return list(deduped.values())[:top_k]


def count_aliases() -> int:
    coll = ensure_terms_collection()
    coll.flush()
    return coll.num_entities


def drop_terms_collection() -> None:
    _ensure_connection()
    if utility.has_collection(TERMS_COLLECTION_NAME):
        utility.drop_collection(TERMS_COLLECTION_NAME)
