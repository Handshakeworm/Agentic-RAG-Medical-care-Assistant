"""tests/unit/test_node_followup_loop.py — F8 追问循环单元测试。

⑥a generate_followup(自由文本)+ ⑥b wait_followup_answer(interrupt)+ ⑦
process_followup_answer(structured)。
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from src.agent.schemas.followup import FollowupParseResult, SymptomResponse
from src.agent.state import create_initial_state


# ────────────────────────────────────────────────────────────────────────────
# ⑥a generate_followup
# ────────────────────────────────────────────────────────────────────────────


@patch("src.agent.nodes.generate_followup.get_llm")
def test_generate_followup_writes_question(mock_llm_factory):
    from src.agent.nodes.generate_followup import generate_followup

    mock_chain = mock_llm_factory.return_value.with_retry.return_value
    msg = MagicMock()
    msg.content = "请问您腹痛是什么时候开始的?另外有没有反酸的感觉?"
    mock_chain.invoke.return_value = msg

    s = create_initial_state(patient_id="P", patient_input="x")
    s.chief_complaint = "腹痛"
    s.followup_questions = [
        {"slot": "onset_time", "type": "dimension"},
        {"term": "反酸", "type": "symptom"},
    ]
    update = generate_followup(s)
    assert "followup_question" in update
    assert "反酸" in update["followup_question"]


@patch("src.agent.nodes.generate_followup.get_llm")
def test_generate_followup_empty_questions_returns_empty_string(mock_llm_factory):
    """followup_questions 为空 → 透传,不调 LLM。"""
    from src.agent.nodes.generate_followup import generate_followup

    s = create_initial_state(patient_id="P", patient_input="x")
    update = generate_followup(s)
    assert update == {"followup_question": ""}
    mock_llm_factory.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# ⑥b wait_followup_answer:interrupt 由 LangGraph 抛 GraphInterrupt — 单测仅证明节点
#     调到了 interrupt(state.followup_question)
# ────────────────────────────────────────────────────────────────────────────


@patch("src.agent.nodes.wait_followup_answer.interrupt")
def test_wait_followup_answer_calls_interrupt_with_question(mock_interrupt):
    from src.agent.nodes.wait_followup_answer import wait_followup_answer

    mock_interrupt.return_value = "我有反酸"
    s = create_initial_state(patient_id="P", patient_input="x")
    s.followup_question = "请问您有反酸吗?"
    update = wait_followup_answer(s)
    mock_interrupt.assert_called_once_with("请问您有反酸吗?")
    assert update == {"followup_answer": "我有反酸"}


# ────────────────────────────────────────────────────────────────────────────
# ⑦ process_followup_answer
# ────────────────────────────────────────────────────────────────────────────


@patch("src.agent.nodes.process_followup.get_llm")
def test_process_followup_three_status_classification(mock_llm_factory):
    """confirmed/denied/uncertain 三类分别更新对应列表。"""
    from src.agent.nodes.process_followup import process_followup_answer

    mock_chain = mock_llm_factory.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.return_value = FollowupParseResult(
        symptom_responses=[
            SymptomResponse(term="反酸", status="confirmed"),
            SymptomResponse(term="发热", status="denied"),
            SymptomResponse(term="夜间盗汗", status="uncertain"),
            SymptomResponse(term="呕吐", status="unanswered"),
        ],
        slot_fills={"trigger": "进食后", "aggravating": ["饥饿"]},
        new_symptoms=[],
    )

    s = create_initial_state(patient_id="P", patient_input="x")
    s.followup_question = "..."
    s.followup_answer = "有反酸,没有发烧,不知道是否盗汗"
    s.followup_questions = [
        {"term": "反酸", "type": "symptom"},
        {"term": "发热", "type": "symptom"},
        {"term": "夜间盗汗", "type": "symptom"},
    ]
    update = process_followup_answer(s)

    assert "反酸" in update["confirmed_symptoms"]
    assert "发热" in update["denied_symptoms"]
    assert "夜间盗汗" in update["uncertain_symptoms"]
    # unanswered 不更新
    assert "呕吐" not in update["confirmed_symptoms"] + update["denied_symptoms"] + update["uncertain_symptoms"]
    # 维度回填
    assert update["present_illness_slots"].trigger == "进食后"
    assert "饥饿" in update["present_illness_slots"].aggravating
    assert update["followup_round"] == 1


@patch("src.agent.nodes.process_followup.get_llm")
def test_process_followup_llm_failure_raises(mock_llm_factory):
    """中安全等级:失败必须抛异常,不能静默吃掉患者回答。"""
    from src.agent.nodes.process_followup import process_followup_answer

    mock_chain = mock_llm_factory.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.side_effect = ValueError("schema rejected")

    s = create_initial_state(patient_id="P", patient_input="x")
    s.followup_answer = "有反酸"
    s.followup_questions = [{"term": "反酸", "type": "symptom"}]
    with pytest.raises(ValueError):
        process_followup_answer(s)
