"""src/agent/nodes/process_exam_result.py — Agent ⑨ process_exam_result(DEV_SPEC §4.1.2 ⑨)。

消费 ⑧b 写入的 pending_exam_results:
1. 已落盘的文件引用追加到 exam_reports
2. 调 report_parser.parse_reports 复用 ①.5 解析逻辑 → 追加到 report_findings
3. 流程回到 build_query,带新证据重新召回

注:落盘步骤由前端/API 层在交互层完成,本节点假设 pending_exam_results 已是
`[{"file_ref": "/落盘路径"}, ...]` 形态。
"""
from __future__ import annotations

import logging

from src.agent.state import MedicalState
from src.agent.utils.report_parser import parse_reports


_logger = logging.getLogger(__name__)


def process_exam_result(state: MedicalState) -> dict:
    pending = state.pending_exam_results or []
    if not pending:
        return {}

    new_refs = []
    for p in pending:
        if isinstance(p, dict) and "file_ref" in p:
            new_refs.append({"file_ref": p["file_ref"]})

    file_paths = [p["file_ref"] for p in new_refs]
    new_findings = parse_reports(file_paths) if file_paths else []

    # 追加到 exam_reports / report_findings,report_index 在 parse_reports 中已补
    # 但 parse_reports 给的 index 是基于本批 file_paths(0..N-1),需要重映射到全局
    base = len(state.exam_reports)
    for f in new_findings:
        f["report_index"] = base + f.get("report_index", 0)

    return {
        "exam_reports": list(state.exam_reports) + new_refs,
        "report_findings": list(state.report_findings) + new_findings,
        # 清空 pending 防重复消费
        "pending_exam_results": [],
    }
