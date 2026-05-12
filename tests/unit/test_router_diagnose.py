"""tests/unit/test_router_diagnose.py — F11 diagnose_router 单元测试。"""
from __future__ import annotations

from config.settings import settings
from src.agent.state import create_initial_state


def _state(top: dict, exam_round: int = 0):
    s = create_initial_state(patient_id="P", patient_input="x")
    s.diagnosis_result = [top]
    s.exam_round = exam_round
    return s


def test_need_exam_within_round_routes_to_recommend_exam():
    from src.agent.routers.diagnose_router import diagnose_router

    s = _state({"differentiation_type": "need_exam"}, exam_round=1)
    assert diagnose_router(s) == "recommend_exam"


def test_need_exam_at_cap_routes_to_safety_gate():
    from src.agent.routers.diagnose_router import diagnose_router

    s = _state(
        {"differentiation_type": "need_exam"},
        exam_round=settings.agent_limits.MAX_EXAM_ROUNDS,
    )
    assert diagnose_router(s) == "safety_gate"


def test_confirmed_routes_to_safety_gate():
    from src.agent.routers.diagnose_router import diagnose_router

    s = _state({"differentiation_type": "confirmed"})
    assert diagnose_router(s) == "safety_gate"


def test_insufficient_routes_to_safety_gate():
    from src.agent.routers.diagnose_router import diagnose_router

    s = _state({"differentiation_type": "insufficient"})
    assert diagnose_router(s) == "safety_gate"


def test_empty_diagnosis_defaults_to_safety_gate():
    from src.agent.routers.diagnose_router import diagnose_router

    s = create_initial_state(patient_id="P", patient_input="x")
    assert diagnose_router(s) == "safety_gate"
