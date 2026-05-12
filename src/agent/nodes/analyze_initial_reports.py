"""src/agent/nodes/analyze_initial_reports.py — Agent ①.5 节点(DEV_SPEC §4.1.2 ①.5)。

无条件顺序边:`exam_reports` 为空 → early return 透传(零 LLM 开销);
非空 → 共享 `report_parser.parse_reports()` 逻辑(与 ⑨ process_exam_result 复用)。

报告内容已是标准医学术语,无需 Entity Linking,LLM 直读提取即可。
"""
from __future__ import annotations

from src.agent.state import MedicalState
from src.agent.utils.report_parser import parse_reports


def analyze_initial_reports(state: MedicalState) -> dict:
    """exam_reports 非空时解析报告 → report_findings;为空时透传(返回空 dict)。"""
    if not state.exam_reports:
        return {}

    file_refs = [r["file_ref"] for r in state.exam_reports if r.get("file_ref")]
    findings = parse_reports(file_refs)
    return {"report_findings": findings}
