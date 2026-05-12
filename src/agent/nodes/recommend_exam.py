"""src/agent/nodes/recommend_exam.py — Agent ⑧a recommend_exam(DEV_SPEC §4.1.2 ⑧)。

LLM 自由文本输出(无 schema)— 基于诊断候选 + unaskable 体征 + 已有报告,
推荐 3-5 项检查,**不静默删除已有报告对应项**(由 LLM 加复用评估说明)。

输出文本由调用方/前端解析展示,这里整段写入 recommended_tests[0]。
exam_round +=1。

中安全等级失败处理:抛异常终止会话(检查推荐失败说明 LLM 完全不可用)。
"""
from __future__ import annotations

import logging
import time

from src.agent.state import MedicalState
from src.common.metrics import _attempts, _failures, _latency, retry_observer
from src.models.llm_client import get_llm
from src.prompts.agent import build_recommend_exam_prompt


_logger = logging.getLogger(__name__)
_NODE = "recommend_exam"
_SCHEMA = "free_text"


def _candidate_chunks_preview(state: MedicalState) -> list[str]:
    """取前 3 条 candidate chunk 的 matched_text 作为参考片段。"""
    out: list[str] = []
    for c in state.candidate_chunks[:3]:
        for vh in c.get("vector_hits") or []:
            mt = (vh.get("matched_text") or "").strip()
            if mt:
                out.append(mt)
                break
    return out


def recommend_exam(state: MedicalState) -> dict:
    prompt = build_recommend_exam_prompt(
        diagnosis_results=state.diagnosis_result,
        unaskable_symptoms=state.unaskable_symptoms,
        candidate_chunks_preview=_candidate_chunks_preview(state),
        existing_report_findings=state.report_findings,
    )

    _attempts.labels(node=_NODE, schema=_SCHEMA).inc()
    t0 = time.perf_counter()
    try:
        chain = get_llm().with_retry(stop_after_attempt=3)
        msg = chain.invoke(
            prompt,
            config={
                "callbacks": [retry_observer],
                "metadata": {"node": _NODE, "schema": _SCHEMA},
            },
        )
        text = (msg.content if hasattr(msg, "content") else str(msg)).strip()
    except Exception as e:
        _failures.labels(
            node=_NODE, schema=_SCHEMA, exception_type=type(e).__name__
        ).inc()
        _logger.error("[%s] free-text LLM call failed: %s", _NODE, e)
        raise
    finally:
        _latency.labels(node=_NODE, schema=_SCHEMA).observe(
            time.perf_counter() - t0
        )

    return {
        "recommended_tests": [text] if text else [],
        "exam_round": state.exam_round + 1,
    }
