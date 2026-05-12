"""tests/unit/test_node_advice_response.py — F13 ⑫ + ⑬ 单元测试。

⑫ generate_advice:覆盖 failure_reason 三种取值对 risk_warnings 的影响。
⑬ format_response:成功路径写 final_response;LLM 失败 → 静态兜底文本。
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from src.agent.schemas.advice import AdviceOutput, MedicationAdvice
from src.agent.state import create_initial_state


# ⑫ generate_advice — failure_reason 三类


def _state_with_diag(failure_reason: str | None):
    s = create_initial_state(patient_id="P", patient_input="x")
    s.diagnosis_result = [
        {
            "disease": "胆囊炎",
            "probability": 0.7,
            "differentiation_type": "confirmed",
            "failure_reason": failure_reason,
        }
    ]
    return s


@patch("src.agent.nodes.generate_advice.get_llm")
def test_advice_failure_reason_none_no_extra_warning(mock_llm):
    from src.agent.nodes.generate_advice import generate_advice

    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.return_value = AdviceOutput(
        medications=[
            MedicationAdvice(
                drug_name="奥美拉唑", dosage="20mg", frequency="每日2次",
                duration="14天", notes="餐前服用",
            )
        ],
        exam_suggestions=["腹部超声"],
        risk_warnings=["注意饮食"],
    )

    s = _state_with_diag(None)
    update = generate_advice(s)
    assert update["risk_warnings"] == ["注意饮食"]


@patch("src.agent.nodes.generate_advice.get_llm")
def test_advice_followup_capped_appends_note(mock_llm):
    from src.agent.nodes.generate_advice import generate_advice
    from src.agent.nodes.generate_advice import _FAILURE_NOTE_CAPPED

    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.return_value = AdviceOutput(risk_warnings=[])

    s = _state_with_diag("followup_round_capped")
    update = generate_advice(s)
    assert _FAILURE_NOTE_CAPPED in update["risk_warnings"]


@patch("src.agent.nodes.generate_advice.get_llm")
def test_advice_step_failure_appends_note(mock_llm):
    from src.agent.nodes.generate_advice import generate_advice
    from src.agent.nodes.generate_advice import _FAILURE_NOTE_STEP

    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.return_value = AdviceOutput(risk_warnings=[])

    s = _state_with_diag("step_2_structured_output_failed: ValidationError: missing field")
    update = generate_advice(s)
    assert _FAILURE_NOTE_STEP in update["risk_warnings"]


# ⑬ format_response


@patch("src.agent.nodes.format_response.get_llm")
def test_format_response_writes_final_response(mock_llm):
    from src.agent.nodes.format_response import format_response

    mock_chain = mock_llm.return_value.with_retry.return_value
    msg = MagicMock()
    msg.content = "您可能患有胆囊炎,建议..."
    mock_chain.invoke.return_value = msg

    s = create_initial_state(patient_id="P", patient_input="x")
    update = format_response(s)
    assert update["final_response"].startswith("您可能患有胆囊炎")


@patch("src.agent.nodes.format_response.get_llm")
def test_format_response_falls_back_on_llm_failure(mock_llm):
    from src.agent.nodes.format_response import format_response, _FALLBACK_RESPONSE

    mock_chain = mock_llm.return_value.with_retry.return_value
    mock_chain.invoke.side_effect = RuntimeError("LLM 不可用")

    s = create_initial_state(patient_id="P", patient_input="x")
    update = format_response(s)
    assert update["final_response"] == _FALLBACK_RESPONSE
