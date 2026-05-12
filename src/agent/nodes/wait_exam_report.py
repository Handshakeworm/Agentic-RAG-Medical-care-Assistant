"""src/agent/nodes/wait_exam_report.py — Agent ⑧b wait_exam_report(DEV_SPEC §4.1.5)。

interrupt 等待患者线下就医后回传检查结果(图片/PDF/文字描述);恢复时只重执行
本节点,⑧a 已生成的 recommended_tests 不会重复 LLM 调用。

interrupt 返回值:list[dict],每项 `{"file_ref": <落盘后的路径>}`(由前端/上传 API 落盘)。
"""
from __future__ import annotations

from langgraph.types import interrupt

from src.agent.state import MedicalState


def wait_exam_report(state: MedicalState) -> dict:
    """暂停执行,interrupt 返回值写入 pending_exam_results。"""
    pending = interrupt(state.recommended_tests)
    return {"pending_exam_results": pending or []}
