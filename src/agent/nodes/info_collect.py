"""src/agent/nodes/info_collect.py — Agent ① info_collect 节点(DEV_SPEC §4.1.2 ①)。

三步顺序执行,单轮无 interrupt:
1. LLM 从 patient_input 提取 chief_complaint + present_illness + 13 维 slots
   (`InfoCollectOutput`,中安全等级,失败抛异常终止会话)
2. 以 patient_id 从 PG 加载 medical_history(零 LLM)
3. 以 patient_id 从 PG 加载 exam_reports 文件引用(零 LLM,不读取文件内容)

LLM 调用按 §9.1 模板裸写 try/except/finally,6 指标手动上报。
"""
from __future__ import annotations

import logging
import time

from src.agent.schemas.info_collect import InfoCollectOutput
from src.agent.state import MedicalState, PresentIllnessSlots
from src.agent.utils.patient_repo import (
    load_initial_exam_reports,
    load_medical_history,
)
from src.common.metrics import _attempts, _failures, _latency, retry_observer
from src.models.llm_client import get_llm
from src.prompts.agent import build_info_collect_prompt


_logger = logging.getLogger(__name__)

_NODE = "info_collect_step1"
_SCHEMA = "InfoCollectOutput"


def info_collect(state: MedicalState) -> dict:
    """Step 1 LLM 提取 + Step 2/3 DB 加载,返回 State 更新 dict。"""
    # ─── Step 1: LLM 提取主诉 + 现病史 + 13 维 slots(spec §9.1 中安全等级模板)───
    prompt = build_info_collect_prompt(state.patient_input)

    _attempts.labels(node=_NODE, schema=_SCHEMA).inc()
    t0 = time.perf_counter()
    try:
        chain = get_llm().with_structured_output(
            InfoCollectOutput, method="json_mode"
        ).with_retry(stop_after_attempt=3)
        result: InfoCollectOutput = chain.invoke(
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
        raise  # 中安全:抛回 graph,终止会话(无主诉无法继续)
    finally:
        _latency.labels(node=_NODE, schema=_SCHEMA).observe(
            time.perf_counter() - t0
        )

    # ─── Step 2 + 3: DB 加载病史档案 + 报告文件引用(占位实现见 patient_repo)───
    medical_history = load_medical_history(state.patient_id)
    exam_reports = load_initial_exam_reports(state.patient_id)

    return {
        "chief_complaint": result.chief_complaint,
        "present_illness": result.present_illness,
        # InfoCollectOutput.PresentIllnessSlots 与 state.PresentIllnessSlots 字段一一对应,
        # 走 model_dump → state model 重构,确保类型校验同时通过两端
        "present_illness_slots": PresentIllnessSlots(
            **result.present_illness_slots.model_dump()
        ),
        "medical_history": medical_history,
        "exam_reports": exam_reports,
    }
