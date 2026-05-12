"""tests/unit/test_node_extract_symptoms.py — F5 ④ extract_symptoms 单元测试。

零 LLM,只 mock terms_collection / embedding。验证:
- 空 candidate_chunks → 空 extracted_symptoms
- Tier 1 命中 → linked=True + preferred_term
- Tier 2 阈值通过 → linked=True
- Tier 2 阈值未达 → Tier 3 保留原文 linked=False
- 输出按 (linked desc, text asc) 排序
"""
from __future__ import annotations

from unittest.mock import patch

from config.settings import settings
from src.agent.state import create_initial_state


def _make_state_with_chunks(matched_texts: list[str]):
    s = create_initial_state(patient_id="P", patient_input="x")
    s.candidate_chunks = [
        {
            "source_chunk_id": f"c{i}",
            "rrf_score": 0.1,
            "vector_hits": [{"vector_type": "original", "rank": i, "matched_text": t}],
        }
        for i, t in enumerate(matched_texts)
    ]
    return s


def test_empty_chunks_returns_empty():
    from src.agent.nodes.extract_symptoms import extract_symptoms

    s = create_initial_state(patient_id="P", patient_input="x")
    assert extract_symptoms(s) == {"extracted_symptoms": []}


@patch("src.agent.nodes.extract_symptoms.search_aliases", return_value=[])
@patch("src.agent.nodes.extract_symptoms.query_term_by_alias_exact")
@patch("src.agent.nodes.extract_symptoms.get_embedding_model")
def test_tier1_exact_match(mock_embed, mock_alias_exact, _search):
    from src.agent.nodes.extract_symptoms import extract_symptoms

    # 让所有候选关键词都 Tier 1 命中"腹痛"
    mock_alias_exact.return_value = {"concept_id": "R10.4", "preferred_term": "腹痛"}

    s = _make_state_with_chunks(["腹痛 胃痛 持续 反复"])
    result = extract_symptoms(s)
    syms = result["extracted_symptoms"]
    assert len(syms) > 0
    assert all(r["linked"] is True for r in syms)
    assert all(r["preferred_term"] == "腹痛" for r in syms)


@patch("src.agent.nodes.extract_symptoms.query_term_by_alias_exact", return_value=None)
@patch("src.agent.nodes.extract_symptoms.search_aliases")
@patch("src.agent.nodes.extract_symptoms.get_embedding_model")
def test_tier2_threshold_pass_and_fail(mock_embed, mock_search, _alias):
    from src.agent.nodes.extract_symptoms import extract_symptoms

    mock_embed.return_value.encode_one.return_value = [0.1] * 4096
    th = settings.agent_limits.ENTITY_LINKING_TIER2_THRESHOLD

    # 一半关键词 score 高于阈值(linked=True),一半低于(Tier 3 保留 linked=False)
    call_idx = {"i": 0}

    def fake_search(query_vector, top_k=1):
        i = call_idx["i"]
        call_idx["i"] += 1
        # 偶数关键词高分,奇数低分
        score = th + 0.01 if i % 2 == 0 else th - 0.5
        return [
            {"concept_id": "X", "preferred_term": "PT", "alias": "a", "score": score}
        ]

    mock_search.side_effect = fake_search

    s = _make_state_with_chunks(["腹痛 头痛 反酸 嗳气 烧心"])
    syms = extract_symptoms(s)["extracted_symptoms"]
    # 至少有一些 linked=True 与 linked=False 共存
    linked = [s for s in syms if s["linked"]]
    unlinked = [s for s in syms if not s["linked"]]
    assert linked, "Tier 2 阈值通过的关键词应至少 1 项 linked=True"
    assert unlinked, "Tier 2 阈值未达的应回退为 Tier 3 linked=False"

    # 排序:linked=True 全部排在 linked=False 之前
    first_unlinked_idx = next(
        (i for i, r in enumerate(syms) if not r["linked"]), len(syms)
    )
    assert all(syms[i]["linked"] for i in range(first_unlinked_idx))
