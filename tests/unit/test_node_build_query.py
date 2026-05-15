"""tests/unit/test_node_build_query.py — F3 ② build_query 单元测试(DEV_SPEC §4.1.2 ②)。

Mock LLM(两处:NER / Query)+ Mock terms_collection(query_term_by_alias_exact +
search_aliases)+ Mock embedding。Step 2 EL 不调 LLM,走三层归一化(Tier 1 精确 /
Tier 2 向量阈值 / Tier 3 占位)。

覆盖:
- 首轮:四步全跑(EL Tier 1 命中)+ chief 中已链接症状写入 confirmed/denied
- 后续轮(非检查路径):对 followup_answer NER + last_nlu_round 推进
- 检查路径(followup_round == last_nlu_round 但非首轮):跳过 Step 1/2,只跑 Step 4
- standardized_entities 按 preferred_term 去重(不重复追加)
"""
from __future__ import annotations

from unittest.mock import patch

from src.agent.schemas.ner import NEREntity, NERResult
from src.agent.schemas.query_construction import QueryConstructionOutput
from src.agent.state import create_initial_state


def _setup_llm_mocks(mock_llm_factory, ner_entities, qc_dense):
    """组装两个 LLM 调用返回:NER → QueryConstruction(Step 2 EL 已无 LLM)。"""
    mock_chain = mock_llm_factory.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.side_effect = [
        NERResult(entities=ner_entities),
        QueryConstructionOutput(dense_query=qc_dense),
    ]


def _alias_table(mapping: dict[str, dict]) -> callable:
    """生成 query_term_by_alias_exact 的 side_effect:dict 命中即 Tier 1。"""
    def _lookup(alias: str):
        return mapping.get(alias)
    return _lookup


@patch("src.agent.nodes.build_query.search_aliases", return_value=[])
@patch("src.agent.nodes.build_query.query_term_by_alias_exact")
@patch("src.agent.nodes.build_query.get_embedding_model")
@patch("src.agent.nodes.build_query.build_sparse_queries", return_value=["腹痛 肚子疼"])
@patch("src.agent.nodes.build_query.get_llm")
def test_first_round_full_pipeline(
    mock_llm_factory, mock_sparse_build, mock_embed, mock_alias_exact, _aliases
):
    """首轮:NER → EL(Tier 1 精确)→ Step3 → Step4;chief 中症状自动入 confirmed_symptoms。"""
    from src.agent.nodes.build_query import build_query

    mock_embed.return_value.encode_one.return_value = [0.1] * 4096
    mock_alias_exact.side_effect = _alias_table({
        "肚子疼": {"concept_id": "R10.4", "preferred_term": "腹痛"},
        "发烧":   {"concept_id": "R50",   "preferred_term": "发热"},
    })

    _setup_llm_mocks(
        mock_llm_factory,
        ner_entities=[
            NEREntity(text="肚子疼", entity_type="symptom", negation=False),
            NEREntity(text="发烧", entity_type="symptom", negation=True),
        ],
        qc_dense="持续3天的中等程度腹痛",
    )

    s = create_initial_state(patient_id="P1", patient_input="肚子疼3天没发烧")
    s.chief_complaint = "腹痛 3 天"
    s.present_illness = "肚子疼 3 天,没发烧"
    update = build_query(s)

    assert "腹痛" in update["confirmed_symptoms"]
    assert "发热" in update["denied_symptoms"]
    assert update["dense_query"] == "持续3天的中等程度腹痛"
    assert update["sparse_queries"] == ["腹痛 肚子疼"]
    assert update["last_nlu_round"] == 0  # followup_round 初始为 0
    assert len(update["standardized_entities"]) == 2


@patch("src.agent.nodes.build_query.search_aliases", return_value=[])
@patch("src.agent.nodes.build_query.query_term_by_alias_exact", return_value=None)
@patch("src.agent.nodes.build_query.get_embedding_model")
@patch("src.agent.nodes.build_query.build_sparse_queries", return_value=[])
@patch("src.agent.nodes.build_query.get_llm")
def test_check_path_skips_ner_and_linking(
    mock_llm_factory, _sparse, _embed, _alias_exact, _aliases
):
    """检查路径(followup_round == last_nlu_round 且非首轮)只跑 Step 4。"""
    from src.agent.nodes.build_query import build_query

    # 只准备 1 个 invoke 返回 — Step 4 唯一 LLM 调用
    mock_chain = mock_llm_factory.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.return_value = QueryConstructionOutput(
        dense_query="附加证据后的复合 query",
    )

    s = create_initial_state(patient_id="P", patient_input="x")
    s.followup_round = 2
    s.last_nlu_round = 2  # 检查路径标志
    s.chief_complaint = "腹痛"
    s.standardized_entities = [
        {
            "raw_text": "肚子疼",
            "entity_type": "symptom",
            "negation": False,
            "temporality": "current",
            "numeric_value": None,
            "concept_id": "R10.4",
            "preferred_term": "腹痛",
            "confidence": 0.9,
        }
    ]
    update = build_query(s)

    # 只调过 1 次 LLM(Step 4)
    assert mock_chain.invoke.call_count == 1
    assert update["dense_query"] == "附加证据后的复合 query"
    # last_nlu_round 不前进(NER 没跑)
    assert "last_nlu_round" not in update


@patch("src.agent.nodes.build_query.search_aliases", return_value=[])
@patch("src.agent.nodes.build_query.query_term_by_alias_exact")
@patch("src.agent.nodes.build_query.get_embedding_model")
@patch("src.agent.nodes.build_query.build_sparse_queries", return_value=[])
@patch("src.agent.nodes.build_query.get_llm")
def test_dedup_appends_only_new_preferred_terms(
    mock_llm_factory, _sparse, _embed, mock_alias_exact, _aliases
):
    """已有 standardized_entities 中相同 preferred_term 的实体不重复追加。"""
    from src.agent.nodes.build_query import build_query

    mock_alias_exact.side_effect = _alias_table({
        "腹痛": {"concept_id": "R10.4", "preferred_term": "腹痛"},
    })
    _setup_llm_mocks(
        mock_llm_factory,
        ner_entities=[
            NEREntity(text="腹痛", entity_type="symptom", negation=False),
        ],
        qc_dense="x",
    )

    s = create_initial_state(patient_id="P", patient_input="x")
    s.chief_complaint = "腹痛"
    s.standardized_entities = [
        {
            "raw_text": "肚子疼",
            "entity_type": "symptom",
            "negation": False,
            "temporality": "current",
            "numeric_value": None,
            "concept_id": "R10.4",
            "preferred_term": "腹痛",
            "confidence": 0.9,
        }
    ]
    update = build_query(s)
    assert len(update["standardized_entities"]) == 1  # 没有重复追加


@patch("src.agent.nodes.build_query.query_term_by_alias_exact", return_value=None)
@patch("src.agent.nodes.build_query.search_aliases", return_value=[
    {"concept_id": "R10.4", "preferred_term": "腹痛", "alias": "腹痛", "score": 0.97}
])
@patch("src.agent.nodes.build_query.get_embedding_model")
@patch("src.agent.nodes.build_query.build_sparse_queries", return_value=[])
@patch("src.agent.nodes.build_query.get_llm")
def test_el_tier2_vector_threshold_above_cutoff(
    mock_llm_factory, _sparse, mock_embed, _aliases, _alias_exact
):
    """Tier 1 不命中时,Tier 2 向量 Top-1 cosine ≥ 0.92 直接采纳。"""
    from src.agent.nodes.build_query import build_query

    mock_embed.return_value.encode_one.return_value = [0.1] * 4096
    _setup_llm_mocks(
        mock_llm_factory,
        ner_entities=[NEREntity(text="肚子隐痛", entity_type="symptom", negation=False)],
        qc_dense="x",
    )

    s = create_initial_state(patient_id="P", patient_input="x")
    s.chief_complaint = "肚子隐痛"
    update = build_query(s)
    assert "腹痛" in update["confirmed_symptoms"]


@patch("src.agent.nodes.build_query.query_term_by_alias_exact", return_value=None)
@patch("src.agent.nodes.build_query.search_aliases", return_value=[
    {"concept_id": "R10.4", "preferred_term": "腹痛", "alias": "腹痛", "score": 0.80}
])
@patch("src.agent.nodes.build_query.get_embedding_model")
@patch("src.agent.nodes.build_query.build_sparse_queries", return_value=[])
@patch("src.agent.nodes.build_query.get_llm")
def test_el_tier2_below_cutoff_falls_to_tier3(
    mock_llm_factory, _sparse, mock_embed, _aliases, _alias_exact
):
    """Tier 2 cosine 0.80 < 0.92 阈值 → Tier 3 占位,preferred_term=None,confirmed_symptoms 空。"""
    from src.agent.nodes.build_query import build_query

    mock_embed.return_value.encode_one.return_value = [0.1] * 4096
    _setup_llm_mocks(
        mock_llm_factory,
        ner_entities=[NEREntity(text="肚子隐痛", entity_type="symptom", negation=False)],
        qc_dense="x",
    )

    s = create_initial_state(patient_id="P", patient_input="x")
    s.chief_complaint = "肚子隐痛"
    update = build_query(s)
    assert update["confirmed_symptoms"] == []
    # 实体仍然进 standardized_entities,但 preferred_term=None
    assert len(update["standardized_entities"]) == 1
    assert update["standardized_entities"][0]["preferred_term"] is None
