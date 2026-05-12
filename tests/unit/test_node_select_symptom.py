"""tests/unit/test_node_select_symptom.py — F6 ⑤ select_discriminative_symptom 单元测试。

Mock LLM(维度选择 + 可问性评估)+ Mock embedding。验证:
- 有空槽 → 混合输出(维度 + 症状),维度占 1~2 席
- 无空槽 → 纯症状输出
- 报告 positive_findings 命中 → 直接入 confirmed,不追问
- 已问症状(confirmed/denied/uncertain)被过滤掉
- 阈值兜底:首轮 (followup_round=0) 不触发清空,后续轮触发
"""
from __future__ import annotations

from unittest.mock import patch

from src.agent.schemas.symptom_selection import (
    AskabilityJudgment,
    DimensionSelection,
)
from src.agent.state import create_initial_state


def _state(extracted, candidate_chunks=None, report_findings=None, **kwargs):
    s = create_initial_state(patient_id="P", patient_input="x")
    s.chief_complaint = "腹痛"
    s.extracted_symptoms = extracted
    s.candidate_chunks = candidate_chunks or [
        {
            "source_chunk_id": f"c{i}",
            "rrf_score": 0.1,
            "vector_hits": [
                {"vector_type": "original", "rank": i, "matched_text": text}
            ],
        }
        for i, text in enumerate(
            [
                "急性胆囊炎 反酸 烧心 腹痛 进食后",
                "胃溃疡 反酸 烧心 上腹痛 进食后加重",
                "胃食管反流 反酸 烧心",
                "胆结石 腹痛 进食后",
            ]
        )
    ]
    s.report_findings = report_findings or []
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


@patch("src.agent.nodes.select_symptom.get_embedding_model")
@patch("src.agent.nodes.select_symptom.get_llm")
def test_dimension_quota_with_symptoms_first_round(mock_llm, mock_embed):
    """有空槽 → 维度占 1~2 席;首轮即使症状级增益低也不清空。"""
    from src.agent.nodes.select_symptom import select_discriminative_symptom

    mock_embed.return_value.encode.return_value = []
    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value

    # 第 1 个 invoke:dimension selection
    # 后续 invoke:每症状可问性评估
    mock_chain.invoke.side_effect = [
        DimensionSelection(selected_slots=["trigger", "aggravating"]),
        AskabilityJudgment(askable=True, reason="患者可感"),
        AskabilityJudgment(askable=True, reason="患者可感"),
        AskabilityJudgment(askable=True, reason="患者可感"),
        AskabilityJudgment(askable=True, reason="患者可感"),
        AskabilityJudgment(askable=True, reason="患者可感"),
    ]

    s = _state(
        extracted=[
            {"text": "反酸", "preferred_term": "反酸", "linked": True},
            {"text": "烧心", "preferred_term": "烧心", "linked": True},
            {"text": "进食后", "preferred_term": "进食后加重", "linked": True},
        ]
    )
    update = select_discriminative_symptom(s)

    fq = update["followup_questions"]
    types = [q["type"] for q in fq]
    assert types.count("dimension") == 2
    assert "symptom" in types
    assert len(fq) <= 5  # MAX_FOLLOWUP_QUESTIONS


@patch("src.agent.nodes.select_symptom.get_embedding_model")
@patch("src.agent.nodes.select_symptom.get_llm")
def test_no_empty_slots_pure_symptom_path(mock_llm, mock_embed):
    """无空槽 → followup_questions 全是症状,无 LLM 维度调用。"""
    from src.agent.nodes.select_symptom import select_discriminative_symptom
    from src.agent.state import PresentIllnessSlots

    mock_embed.return_value.encode.return_value = []
    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.side_effect = [
        AskabilityJudgment(askable=True, reason="ok"),
        AskabilityJudgment(askable=True, reason="ok"),
    ]

    full_slots = PresentIllnessSlots(
        onset_time="3天",
        onset_mode="急性",
        trigger="进食",
        location="上腹",
        nature="胀痛",
        severity="中",
        duration_pattern="持续性",
        aggravating=["进食"],
        relieving=["热敷"],
        associated_symptoms=["反酸"],
        progression="加重",
        treatment_tried="奥美拉唑",
        treatment_response="部分缓解",
    )

    s = _state(
        extracted=[
            {"text": "反酸", "preferred_term": "反酸", "linked": True},
            {"text": "烧心", "preferred_term": "烧心", "linked": True},
        ]
    )
    s.present_illness_slots = full_slots
    update = select_discriminative_symptom(s)

    fq = update["followup_questions"]
    assert all(q["type"] == "symptom" for q in fq)
    # 没有维度选择调用 → invoke 总数 == 症状数(每个跑可问性评估)
    assert mock_chain.invoke.call_count == 2


@patch("src.agent.nodes.select_symptom.get_embedding_model")
@patch("src.agent.nodes.select_symptom.get_llm")
def test_report_positive_findings_consume_symptom(mock_llm, mock_embed):
    """positive_findings 命中 → 症状直接入 confirmed,不出现在 followup_questions。"""
    from src.agent.nodes.select_symptom import select_discriminative_symptom

    mock_embed.return_value.encode.return_value = []
    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.side_effect = [
        DimensionSelection(selected_slots=["trigger"]),
        AskabilityJudgment(askable=True, reason="ok"),
    ]

    s = _state(
        extracted=[
            {"text": "白细胞升高", "preferred_term": "白细胞升高", "linked": True},
            {"text": "反酸", "preferred_term": "反酸", "linked": True},
        ],
        report_findings=[
            {"report_type": "blood_routine", "positive_findings": ["白细胞升高"]}
        ],
    )
    update = select_discriminative_symptom(s)

    assert "白细胞升高" in update["confirmed_symptoms"]
    # 不应再追问"白细胞升高"
    sympts = [q.get("term") for q in update["followup_questions"] if q.get("type") == "symptom"]
    assert "白细胞升高" not in sympts


@patch("src.agent.nodes.select_symptom.get_embedding_model")
@patch("src.agent.nodes.select_symptom.get_llm")
def test_already_asked_filtered(mock_llm, mock_embed):
    """已 confirmed 的症状不再追问。"""
    from src.agent.nodes.select_symptom import select_discriminative_symptom
    from src.agent.state import PresentIllnessSlots

    mock_embed.return_value.encode.return_value = []
    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.side_effect = [
        AskabilityJudgment(askable=True, reason="ok"),
    ]

    full_slots = PresentIllnessSlots(
        onset_time="x", onset_mode="x", trigger="x", location="x", nature="x",
        severity="x", duration_pattern="x", progression="x",
        treatment_tried="x", treatment_response="x",
        aggravating=["x"], relieving=["x"], associated_symptoms=["x"],
    )
    s = _state(
        extracted=[
            {"text": "反酸", "preferred_term": "反酸", "linked": True},
            {"text": "烧心", "preferred_term": "烧心", "linked": True},
        ],
        confirmed_symptoms=["反酸"],
    )
    s.present_illness_slots = full_slots
    update = select_discriminative_symptom(s)

    sympts = [q["term"] for q in update["followup_questions"] if q["type"] == "symptom"]
    assert "反酸" not in sympts  # 已 confirmed,被过滤
    assert "烧心" in sympts
