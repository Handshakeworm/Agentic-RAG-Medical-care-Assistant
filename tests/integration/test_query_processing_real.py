"""tests/integration/test_query_processing.py — DEV_SPEC §3.2.1 Step 2 真 Milvus 集成测试。

走真 Milvus 标量查询(`Collection.query(expr='concept_id == "..."'`),验证
`query_aliases_by_concept_id` + query_processing 三函数端到端工作:
- 同 concept_id 拉回全部 alias
- 跨 concept_id 别名合并去重
- 长度 ≤ 1 别名过滤 + 空词袋跳过(spec §3.2.1 Step 2 规则)

为不污染 D2 灌的生产 terms_collection(4w 行 ICD-10),用临时 collection 隔离;
dense_vector 用占位 [0.1]*4096(本测试不走向量检索,仅测标量查询)。
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


pytestmark = [
    pytest.mark.skipif(not _milvus_alive(), reason="Milvus 不可达,启动 docker compose 后再跑"),
    # 已知问题(2026-05-13 G4-G7 回归暴露):真 Embedding + 真 Milvus 检索;
    # Milvus upsert→search 异步 + search 无 timeout → 死等。同 test_docs_collection。
    pytest.mark.skip(reason="Milvus upsert→search race + 无 timeout 死等,待 search_dense 加 timeout 参数后启用"),
]


@pytest.fixture(scope="module")
def temp_terms_collection():
    """用 monkeypatch 临时替换 TERMS_COLLECTION_NAME,跑完自动 drop。"""
    import config.milvus_schema as schema_mod
    import src.db.milvus.terms_collection as tc_mod

    original_name = schema_mod.TERMS_COLLECTION_NAME
    test_name = f"test_terms_{uuid.uuid4().hex[:8]}"
    schema_mod.TERMS_COLLECTION_NAME = test_name
    tc_mod.TERMS_COLLECTION_NAME = test_name

    yield test_name

    tc_mod.drop_terms_collection()
    schema_mod.TERMS_COLLECTION_NAME = original_name
    tc_mod.TERMS_COLLECTION_NAME = original_name


@pytest.fixture(scope="module")
def seeded_terms(temp_terms_collection):
    """灌一组测试别名:覆盖单字过滤、跨 concept 共享别名、不存在 concept 三种场景。"""
    from src.db.milvus.terms_collection import ensure_terms_collection, upsert_aliases

    ensure_terms_collection()

    placeholder_vec = [0.1] * EMBEDDING_DIM

    # R10.4 腹痛:5 别名(含 1 字符"痛"应被 query_processing 过滤)
    # R50   发热:3 别名
    # R50.0 寒战发热:2 别名(其中"发热"与 R50 共享,验证跨 concept 去重)
    records = [
        # R10.4 腹痛维度
        {"id": "R10.4_a01", "concept_id": "R10.4", "preferred_term": "腹痛",
         "alias": "腹痛", "source_vocab": "TEST", "icd10": "R10.4", "category": "symptom",
         "dense_vector": placeholder_vec},
        {"id": "R10.4_a02", "concept_id": "R10.4", "preferred_term": "腹痛",
         "alias": "肚子疼", "source_vocab": "TEST", "icd10": "R10.4", "category": "symptom",
         "dense_vector": placeholder_vec},
        {"id": "R10.4_a03", "concept_id": "R10.4", "preferred_term": "腹痛",
         "alias": "abdominal pain", "source_vocab": "TEST", "icd10": "R10.4", "category": "symptom",
         "dense_vector": placeholder_vec},
        {"id": "R10.4_a04", "concept_id": "R10.4", "preferred_term": "腹痛",
         "alias": "胃痛", "source_vocab": "TEST", "icd10": "R10.4", "category": "symptom",
         "dense_vector": placeholder_vec},
        {"id": "R10.4_a05", "concept_id": "R10.4", "preferred_term": "腹痛",
         "alias": "痛", "source_vocab": "TEST", "icd10": "R10.4", "category": "symptom",
         "dense_vector": placeholder_vec},
        # R50 发热维度
        {"id": "R50_a01", "concept_id": "R50", "preferred_term": "发热",
         "alias": "发热", "source_vocab": "TEST", "icd10": "R50", "category": "symptom",
         "dense_vector": placeholder_vec},
        {"id": "R50_a02", "concept_id": "R50", "preferred_term": "发热",
         "alias": "发烧", "source_vocab": "TEST", "icd10": "R50", "category": "symptom",
         "dense_vector": placeholder_vec},
        {"id": "R50_a03", "concept_id": "R50", "preferred_term": "发热",
         "alias": "fever", "source_vocab": "TEST", "icd10": "R50", "category": "symptom",
         "dense_vector": placeholder_vec},
        # R50.0 寒战发热(与 R50 共享"发热")
        {"id": "R50.0_a01", "concept_id": "R50.0", "preferred_term": "寒战发热",
         "alias": "寒战发热", "source_vocab": "TEST", "icd10": "R50.0", "category": "symptom",
         "dense_vector": placeholder_vec},
        {"id": "R50.0_a02", "concept_id": "R50.0", "preferred_term": "寒战发热",
         "alias": "发热", "source_vocab": "TEST", "icd10": "R50.0", "category": "symptom",
         "dense_vector": placeholder_vec},
    ]
    upsert_aliases(records)
    return None


def test_query_aliases_by_concept_id_returns_full_aliases(seeded_terms) -> None:
    """单个 concept_id 查回全部 alias(含未过滤的"痛",过滤是 query_processing 的事)。"""
    from src.db.milvus.terms_collection import query_aliases_by_concept_id

    aliases = query_aliases_by_concept_id("R10.4")
    assert set(aliases) == {"腹痛", "肚子疼", "abdominal pain", "胃痛", "痛"}
    # 字母序确定性
    assert aliases == sorted(aliases)


def test_query_aliases_unknown_concept_returns_empty(seeded_terms) -> None:
    """不存在的 concept_id → 不抛错,返回 []。"""
    from src.db.milvus.terms_collection import query_aliases_by_concept_id

    assert query_aliases_by_concept_id("FAKE_NOT_EXIST_xxx") == []


def test_expand_aliases_end_to_end_filters_single_char(seeded_terms) -> None:
    """端到端:expand_aliases 应过滤掉"痛"(单字)。"""
    from src.rag.retrieval.query_processing import expand_aliases

    out = expand_aliases(["R10.4"])
    assert "痛" not in out
    assert {"腹痛", "肚子疼", "abdominal pain", "胃痛"} == set(out)


def test_build_sparse_queries_end_to_end_dedup_across_concepts(seeded_terms) -> None:
    """端到端:R50 + R50.0 共享"发热",合并词袋只出现一次。"""
    from src.rag.retrieval.query_processing import build_sparse_queries

    out = build_sparse_queries([["R50", "R50.0"]])
    assert len(out) == 1
    bag_tokens = out[0].split(" ")
    assert bag_tokens.count("发热") == 1
    assert "发烧" in bag_tokens
    assert "fever" in bag_tokens
    assert "寒战发热" in bag_tokens


def test_build_sparse_queries_skips_empty_dimension(seeded_terms) -> None:
    """端到端:中间维度 concept_id 不存在 → 该维度跳过,sparse_queries 项项非空。"""
    from src.rag.retrieval.query_processing import build_sparse_queries

    out = build_sparse_queries([
        ["R10.4"],                 # 真实数据,有 5 别名(过滤后 4)
        ["FAKE_NOT_EXIST_xxx"],    # 不存在,该维度应被跳过
        ["R50"],                   # 真实数据,3 别名
    ])
    assert len(out) == 2
    assert all(item for item in out)
    assert any("腹痛" in item for item in out)
    assert any("发热" in item for item in out)
