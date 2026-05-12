"""tests/unit/test_router_should_continue.py — F7 should_continue 单元测试(DEV_SPEC §4.1.3.1)。

验证:
- 三分支(cap → diagnose / questions 非空 → followup / 其他 → diagnose)
- 纯函数:调用前后 State 字段未被修改
"""
from __future__ import annotations

from config.settings import settings
from src.agent.state import create_initial_state


def test_followup_round_cap_routes_to_diagnose():
    from src.agent.routers.should_continue import should_continue

    s = create_initial_state(patient_id="P", patient_input="x")
    s.followup_round = settings.agent_limits.MAX_FOLLOWUP_ROUNDS
    s.followup_questions = [{"term": "反酸", "type": "symptom"}]  # 即使有也忽略
    assert should_continue(s) == "diagnose"


def test_followup_questions_nonempty_routes_to_followup():
    from src.agent.routers.should_continue import should_continue

    s = create_initial_state(patient_id="P", patient_input="x")
    s.followup_round = 1
    s.followup_questions = [{"term": "反酸", "type": "symptom"}]
    assert should_continue(s) == "followup"


def test_no_questions_routes_to_diagnose():
    from src.agent.routers.should_continue import should_continue

    s = create_initial_state(patient_id="P", patient_input="x")
    s.followup_round = 1
    assert should_continue(s) == "diagnose"


def test_router_does_not_mutate_state():
    """纯函数:State 字段在调用前后必须一致。"""
    from src.agent.routers.should_continue import should_continue

    s = create_initial_state(patient_id="P", patient_input="x")
    s.followup_round = 2
    s.followup_questions = [{"term": "反酸", "type": "symptom"}]
    snap = s.model_dump()
    should_continue(s)
    assert s.model_dump() == snap
