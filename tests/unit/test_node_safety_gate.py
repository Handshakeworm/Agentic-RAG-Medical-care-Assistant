"""tests/unit/test_node_safety_gate.py — F12 ⑪ safety_gate 单元测试。

验证:
- 规则层从 allergy_history 抽 banned_drugs
- 规则层 + LLM 兜底输出合并到 safety_constraints
- LLM 失败 → 保守追加通用警告(高安全等级)
"""
from __future__ import annotations

from unittest.mock import patch

from src.agent.schemas.safety_gate import SafetyGateOutput, SafetyRisk
from src.agent.state import create_initial_state


@patch("src.agent.nodes.safety_gate.get_llm")
def test_allergy_in_history_writes_banned_drugs(mock_llm):
    from src.agent.nodes.safety_gate import safety_gate

    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.return_value = SafetyGateOutput(additional_risks=[])

    s = create_initial_state(patient_id="P", patient_input="x")
    s.medical_history = {
        "allergy_history": [
            {"substance": "青霉素"},
            {"name": "磺胺类"},
        ]
    }
    update = safety_gate(s)

    sc = update["safety_constraints"]
    assert "青霉素" in sc["banned_drugs"]
    assert "磺胺类" in sc["banned_drugs"]
    assert sc["additional_risks"] == []


@patch("src.agent.nodes.safety_gate.get_llm")
def test_pregnancy_flag(mock_llm):
    from src.agent.nodes.safety_gate import safety_gate

    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.return_value = SafetyGateOutput(additional_risks=[])

    s = create_initial_state(patient_id="P", patient_input="x")
    s.medical_history = {"obstetric_history": {"pregnancy_status": "pregnant"}}
    update = safety_gate(s)
    assert update["safety_constraints"]["contraindication_flags"]["pregnancy"] is True


@patch("src.agent.nodes.safety_gate.get_llm")
def test_llm_fallback_adds_additional_risks(mock_llm):
    from src.agent.nodes.safety_gate import safety_gate

    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.return_value = SafetyGateOutput(
        additional_risks=[
            SafetyRisk(
                risk_type="cross_allergy",
                description="头孢与青霉素交叉过敏风险",
                severity="high",
                recommendation="禁用头孢类",
            )
        ]
    )

    s = create_initial_state(patient_id="P", patient_input="x")
    s.medical_history = {"allergy_history": [{"substance": "青霉素"}]}
    update = safety_gate(s)
    risks = update["safety_constraints"]["additional_risks"]
    assert len(risks) == 1
    assert risks[0]["severity"] == "high"


@patch("src.agent.nodes.safety_gate.get_llm")
def test_llm_failure_falls_back_to_conservative_warning(mock_llm):
    """高安全等级 — LLM 失败必须保守追加通用警告,不能抛异常阻塞流水线。"""
    from src.agent.nodes.safety_gate import safety_gate

    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.side_effect = RuntimeError("LLM 不可用")

    s = create_initial_state(patient_id="P", patient_input="x")
    update = safety_gate(s)
    risks = update["safety_constraints"]["additional_risks"]
    assert len(risks) == 1
    assert "线下" in risks[0]["recommendation"]
