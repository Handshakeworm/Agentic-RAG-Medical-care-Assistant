"""src/agent/nodes/diagnose.py — Agent ⑩ diagnose 节点(DEV_SPEC §4.1.2 ⑩)。

执行顺序:
  Step -1  followup_round 触顶兜底短路(非 LLM,优先级最高)
  Step 0   Cross-Encoder 精排截断 + 写 last_reranked_chunks
  Step 0.5 Context 扩展(spec §3.2.3 四规则)— 仅 prompt 用,不写回 State
             规则 1:child → parent_chunk_id 父块全文
             规则 2:table/figure → parent 父块全文 + 自身 image_path 截图
             规则 3:父块 → heading_path_id 同节图表(封顶 RETRIEVE_PARENT_FIGURE_CAP)
             规则 4:vector_hits.matched_text 作为 prompt 召回线索附加
  Step 1   LLM #1(**vision LLM,DashScope qwen3.5-plus**):证据归集(EvidenceSheet)
             figure 的 image_path 转 base64 作为多模态消息送入(详见 §3.2.3 LLM 路由段)
  Step 2   LLM #2(主链 LLM,DeepSeek):鉴别诊断排序(DiagnosisRanking)
  Step 3   LLM #3(主链 LLM):置信度校准(DiagnosisOutput)

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
from src.agent.utils.chunks_lookup import (
    lookup_chunk_content,
    lookup_figures_by_heading_path,
)
from src.agent.utils.report_loader import load_report
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
        enabled=settings.reranker.ENABLED,
    )
    reranked = [candidate_chunks[i] for i in indices]
    text = [documents[i] for i in indices]
    return reranked, text


def _load_figure_data_uri(image_path: str | None) -> str | None:
    """把 chunks.image_path 加载成 base64 data URI(失败返 None 不抛)。"""
    if not image_path:
        return None
    try:
        loaded = load_report(image_path)
    except Exception as e:
        _logger.warning("figure image load failed (%s): %s", image_path, e)
        return None
    if loaded.get("kind") != "image":
        return None  # PDF 等非图(理论上不该是 figure image_path,防御性兜底)
    return loaded.get("data_uri")


def _build_diagnose_context(
    reranked_chunks: list[dict], reranked_text: list[str]
) -> dict:
    """Step 0.5:spec §3.2.3 Context 扩展四规则,产出供 Step 1 prompt 消费的结构。

    规则 1:child → parent_chunk_id 父块全文(替换 reranked_text 中的小块)
    规则 2:table/figure 自身 → 父块全文(同规则 1)+ figure 自身截图加载
    规则 3:父块 → heading_path_id 同节图表(封顶 RETRIEVE_PARENT_FIGURE_CAP)
    规则 4:vector_hits.matched_text 作为召回线索 hint(去重)

    去重(spec §3.2.3 末段):四条规则展开后按 chunk_id 去重,常见 case 是图表
    chunk 直接命中(规则 2)+ 父块展开后规则 3 又拉同一图表,只留一份。

    medical_statement **不进 prompt**(spec §3.2.3 规则 2/3 + §3.1.5.1 关键认知段:
    enrichment 字段仅承担召回辅助,不作 LLM context payload)。

    Returns:
        {
            "parent_texts": list[str],   # 与 reranked_chunks 同序;空入入返 reranked_text
            "figures": list[dict],       # 跨规则去重后的同节 + 直接命中图表;每条:
                                         #   chunk_id, chunk_type ("table"|"figure"),
                                         #   chunk_raw_text, title,
                                         #   image_data_uri: str | None  (base64 加载失败 None)
            "vector_hints": list[str],   # 去重后的命中向量文本(matched_text);
                                         #   matched_text 已被 parent_texts 中任一片覆盖时跳过
        }
    """
    if not reranked_chunks:
        return {"parent_texts": list(reranked_text), "figures": [], "vector_hints": []}

    chunk_ids = [c.get("source_chunk_id") for c in reranked_chunks if c.get("source_chunk_id")]
    try:
        meta = lookup_chunk_content(chunk_ids)
    except Exception as e:
        _logger.warning("chunks_lookup failed during context build: %s", e)
        # 全规则降级:用 reranked_text 兜底,无图无 hint
        return {
            "parent_texts": list(reranked_text),
            "figures": [],
            "vector_hints": list(_dedup_vector_hints(reranked_chunks, [])),
        }

    # ── 规则 1 + 2:展开父块文本(与 reranked_chunks 同序)──
    # 同时收集:① 父块 heading_path_id(给规则 3 查同节图表用)
    #          ② 直接命中的 table/figure chunk(给规则 2 加截图 + 进 figures 列表)
    parent_text_by_idx: list[str] = []
    parent_heading_paths: set[str] = set()
    direct_hit_figures: list[dict] = []
    parent_ids_to_fetch: list[str] = []

    for chunk, fallback_text in zip(reranked_chunks, reranked_text):
        cid = chunk.get("source_chunk_id")
        info = meta.get(cid) if cid else None
        if not info:
            parent_text_by_idx.append(fallback_text)
            continue

        # 规则 2 一部分:直接命中 table/figure → 记到 direct_hit_figures
        if info.get("chunk_type") in ("table", "figure"):
            direct_hit_figures.append(
                {
                    "chunk_id": cid,
                    "chunk_type": info["chunk_type"],
                    "chunk_raw_text": info.get("chunk_raw_text") or "",
                    "title": info.get("title"),
                    "image_data_uri": _load_figure_data_uri(info.get("image_path")),
                }
            )

        # 规则 1 + 规则 2 父块替换:有 parent_chunk_id 则取父块全文
        parent_id = info.get("parent_chunk_id")
        if parent_id:
            parent_ids_to_fetch.append(parent_id)
            parent_text_by_idx.append(None)  # 占位,下一轮回填
        else:
            # 父块缺失或自己就是父块 → 用 chunk_raw_text 兜底
            body = info.get("chunk_raw_text") or fallback_text
            parent_text_by_idx.append(body)
            if info.get("heading_path_id"):
                parent_heading_paths.add(info["heading_path_id"])

    # 一次性批量查父块
    parent_meta: dict[str, dict] = {}
    if parent_ids_to_fetch:
        unique_parent_ids = list({pid for pid in parent_ids_to_fetch if pid not in meta})
        try:
            parent_meta = lookup_chunk_content(unique_parent_ids) if unique_parent_ids else {}
        except Exception as e:
            _logger.warning("parent chunks lookup failed: %s", e)
            parent_meta = {}
        # 合并已有 meta 里的父块
        parent_meta = {**parent_meta, **{k: v for k, v in meta.items() if k in parent_ids_to_fetch}}

    # 回填父块文本占位
    for i, chunk in enumerate(reranked_chunks):
        if parent_text_by_idx[i] is not None:
            continue
        cid = chunk.get("source_chunk_id")
        info = meta.get(cid) if cid else None
        parent_id = info.get("parent_chunk_id") if info else None
        parent_info = parent_meta.get(parent_id) if parent_id else None
        if parent_info and parent_info.get("chunk_raw_text"):
            parent_text_by_idx[i] = parent_info["chunk_raw_text"]
            if parent_info.get("heading_path_id"):
                parent_heading_paths.add(parent_info["heading_path_id"])
        else:
            # 父块查询失败 / 内容为空 → 用小块原文兜底
            body = (info.get("chunk_raw_text") if info else None) or reranked_text[i]
            parent_text_by_idx[i] = body or ""

    # ── 规则 3:按 heading_path_id 批量查同节图表(封顶 RETRIEVE_PARENT_FIGURE_CAP)──
    cap = settings.agent_limits.RETRIEVE_PARENT_FIGURE_CAP
    same_section_figures: list[dict] = []
    if parent_heading_paths:
        try:
            grouped = lookup_figures_by_heading_path(parent_heading_paths, cap=cap)
        except Exception as e:
            _logger.warning("same-section figures lookup failed: %s", e)
            grouped = {}
        for figs in grouped.values():
            for f in figs:
                same_section_figures.append(
                    {
                        "chunk_id": f["chunk_id"],
                        "chunk_type": f["chunk_type"],
                        "chunk_raw_text": f.get("chunk_raw_text") or "",
                        "title": f.get("title"),
                        "image_data_uri": _load_figure_data_uri(f.get("image_path")),
                    }
                )

    # ── 去重(spec §3.2.3 末段):跨规则 2/3 按 chunk_id 去重 ──
    seen: set[str] = set()
    merged_figures: list[dict] = []
    for f in (*direct_hit_figures, *same_section_figures):
        cid = f.get("chunk_id")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        merged_figures.append(f)

    # ── 规则 4:vector_hits matched_text 提示(去重,且与父块原文不重叠才附加)──
    vector_hints = list(_dedup_vector_hints(reranked_chunks, parent_text_by_idx))

    return {
        "parent_texts": parent_text_by_idx,
        "figures": merged_figures,
        "vector_hints": vector_hints,
    }


def _dedup_vector_hints(
    reranked_chunks: list[dict], parent_texts: list[str]
) -> list[str]:
    """规则 4:收集 vector_hits.matched_text;已被 parent_texts 任一片覆盖的整体跳过。"""
    parent_joined = "\n".join(t for t in parent_texts if t)
    out: list[str] = []
    seen: set[str] = set()
    for chunk in reranked_chunks:
        for vh in chunk.get("vector_hits") or []:
            mt = (vh.get("matched_text") or "").strip()
            if not mt or mt in seen:
                continue
            if mt in parent_joined:
                seen.add(mt)
                continue  # 已被父块原文覆盖
            seen.add(mt)
            out.append(mt)
    return out


# ────────────────────────────────────────────────────────────────────────────
# 三步 LLM 调用(高安全等级:模板内嵌单步埋点 + 顶层 try/except 兜底)
# ────────────────────────────────────────────────────────────────────────────


def _diagnose_step(step_num: int, chain, prompt_or_messages, schema_name: str):
    """单步 LLM 调用 + 6 指标埋点;不在此处兜底,异常上抛由顶层捕获。

    Args:
        prompt_or_messages: Step 1 走多模态时为 `list[BaseMessage]`(含图),
            Step 2/3 为纯文本 `str`;LangChain `chain.invoke` 对两种入参都支持。
    """
    node = f"diagnose_step{step_num}"
    _attempts.labels(node=node, schema=schema_name).inc()
    t0 = time.perf_counter()
    try:
        return chain.invoke(
            prompt_or_messages,
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

    # ─── Step 0.5: Context 扩展(spec §3.2.3 四规则,仅 prompt 用,不写回 State)───
    ctx = _build_diagnose_context(reranked_chunks, reranked_text)

    # ─── Step 1-3 链式调用,任一步失败立即停止 ───
    # Step 1 走 vision LLM(spec §3.2.3 LLM 路由 + §9.3 diagnose Step 1 行);
    # Step 2/3 走主链 DeepSeek
    vision_llm = get_llm(
        model=settings.llm.VISION_MODEL_NAME,
        base_url=settings.llm.VISION_BASE_URL,
        api_key=settings.llm.VISION_API_KEY,
    )
    main_llm = get_llm()
    evidence_chain = vision_llm.with_structured_output(EvidenceSheet, method="json_mode").with_retry(stop_after_attempt=3)
    ranking_chain = main_llm.with_structured_output(DiagnosisRanking, method="json_mode").with_retry(stop_after_attempt=3)
    calibration_chain = main_llm.with_structured_output(DiagnosisOutput, method="json_mode").with_retry(stop_after_attempt=3)

    history_summary = json.dumps(state.medical_history, ensure_ascii=False)[:600]
    slots_dict = state.present_illness_slots.model_dump()

    current_step = 0
    last_prompt: str | None = None
    last_raw_output: str | None = None

    try:
        # Step 1(vision LLM,多模态 messages)
        current_step = 1
        evidence_messages, evidence_prompt_text = build_evidence_assembly_prompt(
            parent_texts=ctx["parent_texts"],
            figures=ctx["figures"],
            vector_hints=ctx["vector_hints"],
            confirmed_symptoms=state.confirmed_symptoms,
            denied_symptoms=state.denied_symptoms,
            slots=slots_dict,
            history_summary=history_summary,
            report_findings=state.report_findings,
        )
        # last_prompt 存文本镜像(§9.6 final_prompt 是 str 字段),
        # invoke 喂 multimodal messages
        last_prompt = evidence_prompt_text
        evidence: EvidenceSheet = _diagnose_step(
            1, evidence_chain, evidence_messages, "EvidenceSheet"
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
