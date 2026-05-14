"""tests/unit/test_node_exam_loop.py — F9 检查循环单元测试。

⑧a recommend_exam(结构化 RecommendExamOutput,spec §9.3)+ ⑧b wait_exam_report
(interrupt)+ ⑨ process_exam_result(复用 parse_reports)。
"""
from __future__ import annotations

from unittest.mock import patch

from src.agent.schemas.recommend_exam import RecommendExamOutput
from src.agent.state import create_initial_state


# ⑧a recommend_exam


@patch("src.agent.nodes.recommend_exam.get_llm")
def test_recommend_exam_returns_structured_list_and_increments_round(mock_llm_factory):
    from src.agent.nodes.recommend_exam import recommend_exam

    mock_chain = (
        mock_llm_factory.return_value
        .with_structured_output.return_value
        .with_retry.return_value
    )
    mock_chain.invoke.return_value = RecommendExamOutput(
        tests=["腹部超声", "胃镜", "腹部超声"],  # 重复项验证去重
        rationale="腹部超声优先,可区分胆囊炎;胃镜可确认溃疡",
    )

    s = create_initial_state(patient_id="P", patient_input="x")
    s.diagnosis_result = [
        {"disease": "胆囊炎", "probability": 0.5, "differentiation_type": "need_exam"}
    ]
    update = recommend_exam(s)
    assert update["exam_round"] == 1
    # spec §4.1.1 字段定义:list[str] 每项一个检查名,不允许整段塞单元素
    assert update["recommended_tests"] == ["腹部超声", "胃镜"]


@patch("src.agent.nodes.recommend_exam.get_llm")
def test_recommend_exam_empty_tests_yields_empty_list(mock_llm_factory):
    """所有所需检查患者已上传报告 → tests 可为空(spec §9.5 RecommendExamOutput)。"""
    from src.agent.nodes.recommend_exam import recommend_exam

    mock_chain = (
        mock_llm_factory.return_value
        .with_structured_output.return_value
        .with_retry.return_value
    )
    mock_chain.invoke.return_value = RecommendExamOutput(tests=[], rationale="已有报告全覆盖")

    s = create_initial_state(patient_id="P", patient_input="x")
    update = recommend_exam(s)
    assert update["recommended_tests"] == []
    assert update["exam_round"] == 1


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
