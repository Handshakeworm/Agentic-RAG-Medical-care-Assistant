"""src/agent/routers/diagnose_router.py — Agent 条件路由 diagnose_router(DEV_SPEC §4.1.3.2)。

纯函数路由(spec §9.7 hard rule:常量来自 settings.agent_limits,禁止 hardcode)。

逻辑(spec §4.1.3.2):
- top1.differentiation_type == "need_exam" 且 exam_round < MAX_EXAM_ROUNDS
  → "recommend_exam"
- 其他(confirmed / insufficient / exam_round 触顶) → "safety_gate"
"""
from __future__ import annotations

from config.settings import settings
from src.agent.state import MedicalState


def diagnose_router(state: MedicalState) -> str:
    if not state.diagnosis_result:
        # 异常防御:无诊断结果(理论上不应发生,但 graph 出 bug 时不至于 KeyError)
        return "safety_gate"
    top = state.diagnosis_result[0]
    if (
        top.get("differentiation_type") == "need_exam"
        and state.exam_round < settings.agent_limits.MAX_EXAM_ROUNDS
    ):
        return "recommend_exam"
    return "safety_gate"
