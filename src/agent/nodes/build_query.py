"""src/agent/nodes/build_query.py — Agent ② build_query 节点(DEV_SPEC §4.1.2 ②)。

四步流程,每轮循环均完整执行:

  Step 1 NER             — LLM 抽取医学实体(首轮:chief + present_illness;后续轮:
                           仅当 followup_round > last_nlu_round 时对 followup_answer
                           NER,跳过空转)
  Step 2 Entity Linking  — 每个实体查 terms_collection Top-5 → LLM 选 Top-1;
                           按 preferred_term 去重追加到 standardized_entities;
                           首轮把 chief/present 中已链接症状按 negation 分流写入
                           confirmed_symptoms / denied_symptoms
  Step 3 术语扩展        — 由确定性工具 query_processing.build_sparse_queries 完成
                           (每个 concept 取全部别名 → 空格分词袋,过短别名已过滤)
  Step 4 Query 构建      — LLM 整合 confirmed/slots/report_findings → dense_query;
                           sparse_queries 直接照搬 Step 3 产出

LLM 调用三处(Step 1 NER、Step 2 EL、Step 4 Query),按 §9.1 中安全级模板独立写
try/except/finally,各自上报 6 指标。
"""
from __future__ import annotations

import json
import logging
import time

from src.agent.schemas.entity_linking import (
    EntityLinkingMatch,
    EntityLinkingResult,
)
from src.agent.schemas.ner import NEREntity, NERResult
from src.agent.schemas.query_construction import QueryConstructionOutput
from src.agent.state import MedicalState
from src.common.metrics import _attempts, _failures, _latency, retry_observer
from src.db.milvus.terms_collection import search_aliases
from src.models.embedding_model import get_embedding_model
from src.models.llm_client import get_llm
from src.prompts.agent import (
    build_entity_linking_prompt,
    build_ner_prompt,
    build_query_construction_prompt,
)
from src.rag.retrieval.query_processing import build_sparse_queries


_logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Step 1: NER 调用包装(裸 §9.1 模板)
# ────────────────────────────────────────────────────────────────────────────


def _call_ner(text: str) -> NERResult:
    node, schema = "build_query_step1_ner", "NERResult"
    _attempts.labels(node=node, schema=schema).inc()
    t0 = time.perf_counter()
    try:
        chain = get_llm().with_structured_output(
            NERResult, method="json_mode"
        ).with_retry(stop_after_attempt=3)
        return chain.invoke(
            build_ner_prompt(text),
            config={
                "callbacks": [retry_observer],
                "metadata": {"node": node, "schema": schema},
            },
        )
    except Exception as e:
        _failures.labels(
            node=node, schema=schema, exception_type=type(e).__name__
        ).inc()
        _logger.error("[%s] NER failed: %s", node, e, exc_info=True)
        raise
    finally:
        _latency.labels(node=node, schema=schema).observe(
            time.perf_counter() - t0
        )


# ────────────────────────────────────────────────────────────────────────────
# Step 2: 单实体 Entity Linking 调用包装(裸 §9.1 模板)
# ────────────────────────────────────────────────────────────────────────────


def _call_entity_linking(
    original_text: str, candidates: list[dict]
) -> EntityLinkingMatch:
    """单实体 LLM 选 Top-1。失败→退化为"无匹配"占位(spec §9.3:单实体失败不阻塞其他)。"""
    node, schema = "build_query_step2_entity_linking", "EntityLinkingResult"
    _attempts.labels(node=node, schema=schema).inc()
    t0 = time.perf_counter()
    try:
        chain = get_llm().with_structured_output(
            EntityLinkingResult, method="json_mode"
        ).with_retry(stop_after_attempt=3)
        result: EntityLinkingResult = chain.invoke(
            build_entity_linking_prompt(original_text, candidates),
            config={
                "callbacks": [retry_observer],
                "metadata": {"node": node, "schema": schema},
            },
        )
        if result.matches:
            return result.matches[0]
        return EntityLinkingMatch(
            original_text=original_text,
            concept_id=None,
            preferred_term=None,
            confidence=0.0,
        )
    except Exception as e:
        _failures.labels(
            node=node, schema=schema, exception_type=type(e).__name__
        ).inc()
        _logger.warning(
            "[%s] entity linking failed for '%s': %s",
            node, original_text, e,
        )
        # 单实体失败 → 保留原文,继续其他实体(spec §9.3)
        return EntityLinkingMatch(
            original_text=original_text,
            concept_id=None,
            preferred_term=None,
            confidence=0.0,
        )
    finally:
        _latency.labels(node=node, schema=schema).observe(
            time.perf_counter() - t0
        )


def _link_entities(
    entities: list[NEREntity], top_k: int = 5
) -> list[EntityLinkingMatch]:
    """对每个 NER 实体查 terms_collection Top-K → LLM 选 Top-1。"""
    matches: list[EntityLinkingMatch] = []
    if not entities:
        return matches
    embed = get_embedding_model()
    for ent in entities:
        try:
            vec = embed.encode_one(ent.text)
            candidates = search_aliases(query_vector=vec, top_k=top_k)
        except Exception as e:
            _logger.warning(
                "terms_collection search failed for '%s': %s — skipping linking",
                ent.text, e,
            )
            matches.append(
                EntityLinkingMatch(
                    original_text=ent.text,
                    concept_id=None,
                    preferred_term=None,
                    confidence=0.0,
                )
            )
            continue
        matches.append(_call_entity_linking(ent.text, candidates))
    return matches


# ────────────────────────────────────────────────────────────────────────────
# Step 4: Query 构建调用包装(裸 §9.1 模板)
# ────────────────────────────────────────────────────────────────────────────


def _call_query_construction(
    confirmed_symptoms: list[str],
    medical_history_summary: str,
    report_positive: list[str],
    report_impressions: list[str],
    filled_slots: dict,
    sparse_queries_preview: list[str],
) -> QueryConstructionOutput:
    node, schema = "build_query_step4_query", "QueryConstructionOutput"
    _attempts.labels(node=node, schema=schema).inc()
    t0 = time.perf_counter()
    try:
        chain = get_llm().with_structured_output(
            QueryConstructionOutput, method="json_mode"
        ).with_retry(stop_after_attempt=3)
        return chain.invoke(
            build_query_construction_prompt(
                confirmed_symptoms=confirmed_symptoms,
                medical_history_summary=medical_history_summary,
                report_positive=report_positive,
                report_impressions=report_impressions,
                filled_slots=filled_slots,
                sparse_queries_preview=sparse_queries_preview,
            ),
            config={
                "callbacks": [retry_observer],
                "metadata": {"node": node, "schema": schema},
            },
        )
    except Exception as e:
        _failures.labels(
            node=node, schema=schema, exception_type=type(e).__name__
        ).inc()
        _logger.error("[%s] query construction failed: %s", node, e, exc_info=True)
        raise
    finally:
        _latency.labels(node=node, schema=schema).observe(
            time.perf_counter() - t0
        )


# ────────────────────────────────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────────────────────────────────


def _summarize_history(history: dict) -> str:
    """病史 dict → 一行摘要,只取最有诊断意义的项,避免 prompt 膨胀。"""
    parts = []
    past = history.get("past_history") or {}
    if past:
        parts.append(f"既往史:{json.dumps(past, ensure_ascii=False)[:120]}")
    if history.get("medication_history"):
        parts.append(
            f"用药史:{json.dumps(history['medication_history'], ensure_ascii=False)[:80]}"
        )
    if history.get("family_history"):
        parts.append(
            f"家族史:{json.dumps(history['family_history'], ensure_ascii=False)[:80]}"
        )
    return "; ".join(parts)


def _dedup_append(existing: list[dict], new_records: list[dict]) -> list[dict]:
    """按 preferred_term 去重追加;无 preferred_term 的实体仍保留(供下游用 raw_text)。"""
    seen_terms = {
        e.get("preferred_term")
        for e in existing
        if e.get("preferred_term")
    }
    out = list(existing)
    for r in new_records:
        pt = r.get("preferred_term")
        if pt and pt in seen_terms:
            continue
        if pt:
            seen_terms.add(pt)
        out.append(r)
    return out


def build_query(state: MedicalState) -> dict:
    """四步执行;若检查路径(followup_round == last_nlu_round)直接跳到 Step 4。"""
    is_first_round = state.followup_round == 0
    is_check_path = (
        not is_first_round and state.followup_round == state.last_nlu_round
    )

    new_entities_records: list[dict] = []
    standardized_entities = list(state.standardized_entities)
    confirmed_symptoms = list(state.confirmed_symptoms)
    denied_symptoms = list(state.denied_symptoms)

    # ─── Step 1: NER(check path 跳过,首轮对 chief+present,后续轮对 answer)───
    if not is_check_path:
        if is_first_round:
            ner_text = (
                f"{state.chief_complaint}\n{state.present_illness}".strip()
            )
        else:
            ner_text = state.followup_answer or ""

        ner_text = ner_text.strip()
        if ner_text:
            ner_result = _call_ner(ner_text)
            entities = ner_result.entities
        else:
            entities = []

        # ─── Step 2: Entity Linking(每实体一次 LLM)───
        matches = _link_entities(entities)

        for ent, match in zip(entities, matches):
            record = {
                "raw_text": ent.text,
                "entity_type": ent.entity_type,
                "negation": ent.negation,
                "temporality": ent.temporality,
                "numeric_value": ent.value,
                "concept_id": match.concept_id,
                "preferred_term": match.preferred_term,
                "confidence": match.confidence,
            }
            new_entities_records.append(record)

        standardized_entities = _dedup_append(
            standardized_entities, new_entities_records
        )

        # 首轮主诉症状初始化(spec §4.1.2 ② Step 2)
        if is_first_round:
            for r in new_entities_records:
                if (
                    r["entity_type"] == "symptom"
                    and r["temporality"] == "current"
                    and r["preferred_term"] is not None
                ):
                    if r["negation"]:
                        if r["preferred_term"] not in denied_symptoms:
                            denied_symptoms.append(r["preferred_term"])
                    else:
                        if r["preferred_term"] not in confirmed_symptoms:
                            confirmed_symptoms.append(r["preferred_term"])

    # ─── Step 3: 术语扩展(确定性,无 LLM)───
    # 用 standardized_entities 中所有有 concept_id 的 symptom 实体,逐个产出独立 BM25 词袋
    grouped: list[list[str]] = []
    for r in standardized_entities:
        if r.get("entity_type") == "symptom" and r.get("concept_id"):
            grouped.append([r["concept_id"]])
    sparse_queries = build_sparse_queries(grouped)

    # ─── Step 4: Query 构建(LLM)───
    report_pos = []
    report_imp = []
    for f in state.report_findings:
        report_pos.extend(f.get("positive_findings") or [])
        report_imp.extend(f.get("impressions") or [])

    filled_slots = {
        k: v for k, v in state.present_illness_slots.model_dump().items() if v
    }
    history_summary = _summarize_history(state.medical_history)

    qc = _call_query_construction(
        confirmed_symptoms=confirmed_symptoms,
        medical_history_summary=history_summary,
        report_positive=report_pos,
        report_impressions=report_imp,
        filled_slots=filled_slots,
        sparse_queries_preview=sparse_queries,
    )

    update = {
        "standardized_entities": standardized_entities,
        "confirmed_symptoms": confirmed_symptoms,
        "denied_symptoms": denied_symptoms,
        "dense_query": qc.dense_query,
        # 以确定性产出的 sparse_queries 为准(LLM 输出仅做 schema 占位,防止它瞎改)
        "sparse_queries": sparse_queries or qc.sparse_queries,
    }

    # NER 已执行 → 推进游标(spec §4.1.2 ② Step 1)
    if not is_check_path:
        update["last_nlu_round"] = state.followup_round

    return update
