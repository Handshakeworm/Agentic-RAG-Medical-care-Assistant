"""src/agent/nodes/diagnose.py — Agent ⑩ diagnose 节点(DEV_SPEC §4.1.2 ⑩)。

执行顺序:
  Step -1  followup_round 触顶兜底短路(非 LLM,优先级最高)
  Step 0   Cross-Encoder 精排截断 + 写 last_reranked_chunks
  Step 0.5 父块扩展(Small-to-Big)— 父块文本仅替换 prompt 中小块,不写回 State
  Step 1   LLM #1:证据归集(EvidenceSheet)
  Step 2   LLM #2:鉴别诊断排序(DiagnosisRanking)
  Step 3   LLM #3:置信度校准(DiagnosisOutput)

整链路兜底:任一步重试耗尽 → 立即停止,产 insufficient + failure_reason 兜底,
prompt + raw_output 写入 State 供审计(spec §4.1.2 ⑩ 结构化输出保障 + §9.6.2)。

LLM 调用按 §9.1 高安全级模板 — 三步独立 try/except,顶层一个 try/except 捕获
中间步异常,_fallbacks + _diagnose_reason 业务指标手动上报。
"""
from __future__ import annotations

import json
import logging
import time

from config.settings import settings
from src.agent.schemas.diagnosis import (
    DiagnosisOutput,
    DiagnosisRanking,
    EvidenceSheet,
    RankedDisease,
)
from src.agent.state import MedicalState
from src.agent.utils.chunks_lookup import lookup_chunk_content
from src.common.metrics import (
    _attempts,
    _diagnose_reason,
    _failures,
    _fallbacks,
    _latency,
    retry_observer,
)
from src.models.llm_client import get_llm
from src.prompts.agent import (
    build_diagnosis_calibration_prompt,
    build_diagnosis_ranking_prompt,
    build_evidence_assembly_prompt,
)
from src.rag.retrieval.reranker import rerank_with_fallback


_logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Step -1 兜底产出
# ────────────────────────────────────────────────────────────────────────────


def _capped_result() -> list[dict]:
    return [
        {
            "disease": "信息不足以支持可靠诊断",
            "probability": 0.0,
            "evidence_chain": ["追问轮次达上限 MAX_FOLLOWUP_ROUNDS"],
            "differentiation_type": "insufficient",
            "unaskable_impact": None,
            "failure_reason": "followup_round_capped",
        }
    ]


def _step_failure_result(step_num: int, exc: BaseException) -> list[dict]:
    return [
        {
            "disease": "信息不足以支持可靠诊断",
            "probability": 0.0,
            "evidence_chain": [f"Step {step_num} 结构化输出失败"],
            "differentiation_type": "insufficient",
            "unaskable_impact": None,
            "failure_reason": (
                f"step_{step_num}_structured_output_failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }
    ]


# ────────────────────────────────────────────────────────────────────────────
# Step 0 / 0.5 工具
# ────────────────────────────────────────────────────────────────────────────


def _candidate_text(chunk: dict) -> str:
    parts = []
    for vh in chunk.get("vector_hits") or []:
        mt = (vh.get("matched_text") or "").strip()
        if mt:
            parts.append(mt)
    return " ".join(parts)


def _rerank_and_truncate(
    candidate_chunks: list[dict], query: str, top_k: int
) -> tuple[list[dict], list[str]]:
    """Step 0:reranker.rerank_with_fallback → 重排截断。返回 (reranked_chunks, indexed_text)。

    fallback 路径(reranker 不可用 / 超时)→ 取原序前 top_k(spec §3.2.3 强约束:
    精排不抛异常,失败必走 fallback)。
    """
    if not candidate_chunks:
        return [], []
    documents = [_candidate_text(c) or c.get("source_chunk_id", "") for c in candidate_chunks]
    indices = rerank_with_fallback(
        query=query,
        documents=documents,
        top_k=top_k,
        timeout_sec=settings.reranker.TIMEOUT_SECONDS,
    )
    reranked = [candidate_chunks[i] for i in indices]
    text = [documents[i] for i in indices]
    return reranked, text


def _expand_parent_chunks(reranked_chunks: list[dict], reranked_text: list[str]) -> list[str]:
    """Step 0.5:对每个 chunk 取父块全文替换;父块缺失则保留小块原文兜底。
    返回 list[str](与 reranked_chunks 同序),不写回 State。"""
    chunk_ids = [c.get("source_chunk_id") for c in reranked_chunks if c.get("source_chunk_id")]
    if not chunk_ids:
        return reranked_text

    try:
        meta = lookup_chunk_content(chunk_ids)
    except Exception as e:
        _logger.warning("chunks_lookup failed during parent expansion: %s", e)
        return reranked_text

    out = []
    for chunk, fallback_text in zip(reranked_chunks, reranked_text):
        cid = chunk.get("source_chunk_id")
        info = meta.get(cid) if cid else None
        if not info:
            out.append(fallback_text)
            continue
        parent_id = info.get("parent_chunk_id")
        if parent_id:
            parent = meta.get(parent_id) or lookup_chunk_content([parent_id]).get(parent_id)
            if parent and parent.get("chunk_raw_text"):
                out.append(parent["chunk_raw_text"])
                continue
        # 父块缺失或自己就是父块 → 用小块原文兜底
        body = info.get("chunk_raw_text") or info.get("medical_statement") or fallback_text
        out.append(body or fallback_text)
    return out


# ────────────────────────────────────────────────────────────────────────────
# 三步 LLM 调用(高安全等级:模板内嵌单步埋点 + 顶层 try/except 兜底)
# ────────────────────────────────────────────────────────────────────────────


def _diagnose_step(step_num: int, chain, prompt: str, schema_name: str):
    """单步 LLM 调用 + 6 指标埋点;不在此处兜底,异常上抛由顶层捕获。"""
    node = f"diagnose_step{step_num}"
    _attempts.labels(node=node, schema=schema_name).inc()
    t0 = time.perf_counter()
    try:
        return chain.invoke(
            prompt,
            config={
                "callbacks": [retry_observer],
                "metadata": {"node": node, "schema": schema_name},
            },
        )
    except Exception as e:
        _failures.labels(
            node=node, schema=schema_name, exception_type=type(e).__name__
        ).inc()
        raise
    finally:
        _latency.labels(node=node, schema=schema_name).observe(
            time.perf_counter() - t0
        )


# ────────────────────────────────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────────────────────────────────


def diagnose(state: MedicalState) -> dict:
    # ─── Step -1: 追问触顶兜底 ───
    if state.followup_round >= settings.agent_limits.MAX_FOLLOWUP_ROUNDS:
        _diagnose_reason.labels(reason_kind="followup_round_capped").inc()
        return {
            "diagnosis_result": _capped_result(),
            # 兜底路径不动 last_reranked_chunks / last_diagnose_prompt / raw_output
            # (前者 init 是空 list,后两者 init 是 None,符合"正常路径保持初始值"
            # 的语义)
        }

    # ─── Step 0: 精排截断 ───
    rerank_top_k = settings.retrieval.RERANK_TOP_K
    rerank_query = " / ".join(
        [state.chief_complaint or ""] + state.confirmed_symptoms
    ).strip()
    reranked_chunks, reranked_text = _rerank_and_truncate(
        state.candidate_chunks, rerank_query, rerank_top_k
    )

    # ─── Step 0.5: 父块扩展(仅 prompt 用,不写回 State)───
    expanded_text = _expand_parent_chunks(reranked_chunks, reranked_text)

    # ─── Step 1-3 链式调用,任一步失败立即停止 ───
    llm = get_llm()
    evidence_chain = llm.with_structured_output(EvidenceSheet).with_retry(
        stop_after_attempt=3
    )
    ranking_chain = llm.with_structured_output(DiagnosisRanking).with_retry(
        stop_after_attempt=3
    )
    calibration_chain = llm.with_structured_output(DiagnosisOutput).with_retry(
        stop_after_attempt=3
    )

    history_summary = json.dumps(state.medical_history, ensure_ascii=False)[:600]
    slots_dict = state.present_illness_slots.model_dump()

    current_step = 0
    last_prompt: str | None = None
    last_raw_output: str | None = None

    try:
        # Step 1
        current_step = 1
        evidence_prompt = build_evidence_assembly_prompt(
            reranked_chunks_text=expanded_text,
            confirmed_symptoms=state.confirmed_symptoms,
            denied_symptoms=state.denied_symptoms,
            slots=slots_dict,
            history_summary=history_summary,
            report_findings=state.report_findings,
        )
        last_prompt = evidence_prompt
        evidence: EvidenceSheet = _diagnose_step(
            1, evidence_chain, evidence_prompt, "EvidenceSheet"
        )
        last_raw_output = evidence.model_dump_json()

        # Step 2
        current_step = 2
        ranking_prompt = build_diagnosis_ranking_prompt(
            evidence_sheet_json=last_raw_output,
            unaskable_symptoms=state.unaskable_symptoms,
        )
        last_prompt = ranking_prompt
        ranking: DiagnosisRanking = _diagnose_step(
            2, ranking_chain, ranking_prompt, "DiagnosisRanking"
        )
        last_raw_output = ranking.model_dump_json()

        # Step 3
        current_step = 3
        calibration_prompt = build_diagnosis_calibration_prompt(
            ranking_json=last_raw_output,
            confirmed_symptoms=state.confirmed_symptoms,
            denied_symptoms=state.denied_symptoms,
            report_findings=state.report_findings,
        )
        last_prompt = calibration_prompt
        result: DiagnosisOutput = _diagnose_step(
            3, calibration_chain, calibration_prompt, "DiagnosisOutput"
        )
        last_raw_output = result.model_dump_json()

        # 正常路径产出
        return {
            "diagnosis_result": [r.model_dump() for r in result.results],
            "last_reranked_chunks": reranked_chunks,
            # 正常路径保持 None(spec §9.6.2)— 不写 last_diagnose_prompt / raw_output
        }

    except Exception as e:
        _logger.error(
            "diagnose pipeline failed at step %d: %s: %s",
            current_step, type(e).__name__, e,
            exc_info=True,
        )
        _fallbacks.labels(node="diagnose", fallback_type="insufficient").inc()
        _diagnose_reason.labels(reason_kind=f"step_{current_step}_failed").inc()

        return {
            "diagnosis_result": _step_failure_result(current_step, e),
            "last_reranked_chunks": reranked_chunks,
            "last_diagnose_prompt": last_prompt,
            "last_diagnose_raw_output": last_raw_output or str(e),
        }
