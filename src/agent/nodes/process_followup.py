"""src/agent/nodes/process_followup.py — Agent ⑦ process_followup_answer(DEV_SPEC §4.1.2 ⑦)。

LLM 解析患者回答 → 症状级回答分流(confirmed/denied/uncertain)+ 维度级槽位回填
+ 新症状提取。followup_round += 1 后回到 build_query 复跑流水线。

中安全等级:失败 → 抛异常终止会话(回答未解析将导致信息丢失,不能静默)。
"""
from __future__ import annotations

import logging
import time

from src.agent.schemas.followup import FollowupParseResult
from src.agent.state import MedicalState, PresentIllnessSlots
from src.common.metrics import _attempts, _failures, _latency, retry_observer
from src.models.llm_client import get_llm
from src.prompts.agent import build_followup_parse_prompt


_logger = logging.getLogger(__name__)
_NODE = "process_followup_answer"
_SCHEMA = "FollowupParseResult"

_MULTI_VALUE_SLOTS = {"aggravating", "relieving", "associated_symptoms"}


def _apply_slot_fills(slots: PresentIllnessSlots, fills: dict) -> PresentIllnessSlots:
    """把 LLM 回填值套回 PresentIllnessSlots,类型不符时丢弃该项。"""
    data = slots.model_dump()
    for k, v in fills.items():
        if k not in data:
            _logger.warning("LLM returned unknown slot '%s', ignoring", k)
            continue
        if k in _MULTI_VALUE_SLOTS:
            if isinstance(v, str):
                v = [v]
            if not isinstance(v, list):
                _logger.warning("slot '%s' expects list, got %r", k, v)
                continue
            existing = data[k] or []
            data[k] = list(dict.fromkeys(existing + v))  # 去重保留顺序
        else:
            if isinstance(v, list):
                v = "; ".join(map(str, v))
            data[k] = str(v) if v is not None else None
    return PresentIllnessSlots(**data)


def process_followup_answer(state: MedicalState) -> dict:
    """三类输出 + 槽位回填 + present_illness 追加 + followup_round +=1。"""
    prompt = build_followup_parse_prompt(
        followup_question=state.followup_question,
        followup_answer=state.followup_answer,
        questions=state.followup_questions,
    )

    _attempts.labels(node=_NODE, schema=_SCHEMA).inc()
    t0 = time.perf_counter()
    try:
        chain = get_llm().with_structured_output(
            FollowupParseResult, method="json_mode"
        ).with_retry(stop_after_attempt=3)
        result: FollowupParseResult = chain.invoke(
            prompt,
            config={
                "callbacks": [retry_observer],
                "metadata": {"node": _NODE, "schema": _SCHEMA},
            },
        )
    except Exception as e:
        _failures.labels(
            node=_NODE, schema=_SCHEMA, exception_type=type(e).__name__
        ).inc()
        _logger.error("[%s] structured output failed: %s", _NODE, e, exc_info=True)
        raise  # 中安全:抛回 graph
    finally:
        _latency.labels(node=_NODE, schema=_SCHEMA).observe(
            time.perf_counter() - t0
        )

    confirmed = list(state.confirmed_symptoms)
    denied = list(state.denied_symptoms)
    uncertain = list(state.uncertain_symptoms)
    for r in result.symptom_responses:
        term = r.term
        if r.status == "confirmed" and term not in confirmed:
            confirmed.append(term)
        elif r.status == "denied" and term not in denied:
            denied.append(term)
        elif r.status == "uncertain" and term not in uncertain:
            uncertain.append(term)
        # "unanswered" 不更新任何列表,留给后续轮按需再问

    new_slots = _apply_slot_fills(state.present_illness_slots, result.slot_fills)

    # present_illness 追加新维度信息(spec §4.1.2 ⑦):简单 join
    appended = state.present_illness or ""
    if result.slot_fills:
        addition = "; ".join(f"{k}={v}" for k, v in result.slot_fills.items())
        appended = (appended + "  " + addition).strip()

    return {
        "confirmed_symptoms": confirmed,
        "denied_symptoms": denied,
        "uncertain_symptoms": uncertain,
        "present_illness_slots": new_slots,
        "present_illness": appended,
        "followup_round": state.followup_round + 1,
    }
