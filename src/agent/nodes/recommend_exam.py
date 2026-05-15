"""src/agent/nodes/recommend_exam.py — Agent ⑧a recommend_exam(DEV_SPEC §4.1.2 ⑧)。

结构化输出:LLM 给出 `tests: list[str]`(每项一个检查名)+ `rationale`(整体说明)。
不再像旧实现那样把整段 free text 塞进 `recommended_tests[0]` 破坏字段语义。

基于诊断候选 + unaskable 体征 + 已有报告,推荐 3-5 项检查;**不静默删除已有
报告对应项**,由 LLM 在 rationale 里说明哪些可复用。exam_round +=1。

中安全等级失败处理:抛异常终止会话(检查推荐失败说明 LLM 完全不可用)。
"""
from __future__ import annotations

import logging
import time

from src.agent.schemas.recommend_exam import RecommendExamOutput
from src.agent.state import MedicalState
from src.common.metrics import _attempts, _failures, _latency, retry_observer
from src.models.llm_client import get_llm
from src.prompts.agent import build_recommend_exam_prompt


_logger = logging.getLogger(__name__)
_NODE = "recommend_exam"
_SCHEMA = "RecommendExamOutput"


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
        chain = get_llm().with_structured_output(RecommendExamOutput, method="json_mode").with_retry(stop_after_attempt=3)
        result: RecommendExamOutput = chain.invoke(
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
        _logger.error("[%s] structured output failed: %s", _NODE, e)
        raise
    finally:
        _latency.labels(node=_NODE, schema=_SCHEMA).observe(
            time.perf_counter() - t0
        )

    # 去重保留顺序(LLM 偶尔会重复推荐;state 字段定义无重复语义)
    tests_unique: list[str] = []
    seen: set[str] = set()
    for t in result.tests:
        t_clean = (t or "").strip()
        if t_clean and t_clean not in seen:
            tests_unique.append(t_clean)
            seen.add(t_clean)

    return {
        "recommended_tests": tests_unique,
        "exam_round": state.exam_round + 1,
    }
