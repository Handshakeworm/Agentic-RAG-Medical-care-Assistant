"""tests/unit/test_node_exam_loop.py — F9 检查循环单元测试。

⑧a recommend_exam(自由文本)+ ⑧b wait_exam_report(interrupt)+ ⑨
process_exam_result(复用 parse_reports)。
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from src.agent.state import create_initial_state


# ⑧a recommend_exam


@patch("src.agent.nodes.recommend_exam.get_llm")
def test_recommend_exam_writes_text_and_increments_round(mock_llm_factory):
    from src.agent.nodes.recommend_exam import recommend_exam

    mock_chain = mock_llm_factory.return_value.with_retry.return_value
    msg = MagicMock()
    msg.content = "1. 腹部超声(优先,可区分胆囊炎)\n2. 胃镜(可确认溃疡)"
    mock_chain.invoke.return_value = msg

    s = create_initial_state(patient_id="P", patient_input="x")
    s.diagnosis_result = [
        {"disease": "胆囊炎", "probability": 0.5, "differentiation_type": "need_exam"}
    ]
    update = recommend_exam(s)
    assert update["exam_round"] == 1
    assert "腹部超声" in update["recommended_tests"][0]


# ⑧b wait_exam_report


@patch("src.agent.nodes.wait_exam_report.interrupt")
def test_wait_exam_report_calls_interrupt(mock_interrupt):
    from src.agent.nodes.wait_exam_report import wait_exam_report

    mock_interrupt.return_value = [{"file_ref": "/tmp/lab.pdf"}]
    s = create_initial_state(patient_id="P", patient_input="x")
    s.recommended_tests = ["腹部超声"]
    update = wait_exam_report(s)
    mock_interrupt.assert_called_once_with(["腹部超声"])
    assert update == {"pending_exam_results": [{"file_ref": "/tmp/lab.pdf"}]}


# ⑨ process_exam_result


@patch("src.agent.nodes.process_exam_result.parse_reports")
def test_process_exam_result_appends_reports_and_findings(mock_parse):
    from src.agent.nodes.process_exam_result import process_exam_result

    mock_parse.return_value = [
        {
            "report_type": "imaging",
            "report_date": "2026-05-12",
            "report_index": 0,
            "abnormal_values": [],
            "impressions": ["胆囊炎征象"],
            "positive_findings": ["胆囊壁增厚"],
            "negative_findings": [],
        }
    ]

    s = create_initial_state(patient_id="P", patient_input="x")
    s.exam_reports = [{"file_ref": "/already/old.jpg"}]  # base = 1
    s.pending_exam_results = [{"file_ref": "/new/lab.pdf"}]
    update = process_exam_result(s)

    assert len(update["exam_reports"]) == 2
    assert update["report_findings"][0]["report_index"] == 1  # base + 0
    assert update["pending_exam_results"] == []


def test_process_exam_result_empty_pending_returns_empty():
    from src.agent.nodes.process_exam_result import process_exam_result

    s = create_initial_state(patient_id="P", patient_input="x")
    update = process_exam_result(s)
    assert update == {}
