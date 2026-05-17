"""src/agent/nodes/build_query.py — Agent ② build_query 节点(DEV_SPEC §4.1.2 ②)。

四步流程,每轮循环均完整执行:

  Step 1 NER             — LLM 抽取医学实体(首轮:chief + present_illness;后续轮:
                           仅当 followup_round > last_nlu_round 时对 followup_answer
                           NER,跳过空转)
  Step 2 Entity Linking  — 三层归一化(**无 LLM**,与 ④ extract_symptoms 同一套):
                           Tier 1 query_term_by_alias_exact 精确别名命中即用;
                           Tier 2 search_aliases Top-1,cosine ≥
                           settings.agent_limits.ENTITY_LINKING_TIER2_THRESHOLD
                           直接采纳;Tier 3 保留原文(preferred_term=None);
                           按 preferred_term 去重追加到 standardized_entities;
                           首轮把 chief/present 中已链接症状按 negation 分流写入
                           confirmed_symptoms / denied_symptoms
  Step 3 Sparse 多字段直采 — 不再调用 EL alias 反查(RETRIEVAL_EVAL §2 评测:中文症状词
                           EL 50% Tier 3 占位,alias 反查收益低)。改为 state 多字段
                           直采:chief_complaint + slots 单值字段(trigger / location /
                           nature / severity / duration_pattern / onset_mode)+ slots
                           list 字段(associated_symptoms / aggravating / relieving)
                           + report_findings 的 positive_findings(全加)+ impressions
                           (阴性过滤:含 "(-)" / "正常" / "阴性" / "未见" / "无异常"
                           的整条跳过,避免 BM25 不懂否定造成反向召回)。EL Step 2
                           产物 confirmed_symptoms 等仍由 ⑤ select_symptom 消费。
  Step 4 Dense Query 构建 — LLM 整合 confirmed/slots/report_findings → dense_query;
                           sparse_queries 直接照搬 Step 3 产出

LLM 调用两处(Step 1 NER、Step 4 Query),按 §9.1 中安全级模板独立写
try/except/finally,各自上报 6 指标。
"""
from __future__ import annotations

import json
import logging
import re
import time

from config.settings import settings
from src.agent.schemas.entity_linking import EntityLinkingMatch
from src.agent.schemas.ner import NEREntity, NERResult
from src.agent.schemas.query_construction import QueryConstructionOutput
from src.agent.state import MedicalState
from src.common.metrics import _attempts, _failures, _latency, retry_observer
from src.db.milvus.terms_collection import (
    query_term_by_alias_exact,
    search_aliases,
)
from src.models.embedding_model import get_embedding_model
from src.models.llm_client import get_llm
from src.prompts.agent import (
    build_ner_prompt,
    build_query_construction_prompt,
)


# Step 3 阴性 impressions 过滤:含此类字样的 impressions 整条视为阴性,跳过(BM25 不懂否定)
_NEGATIVE_IMPRESSION_RE = re.compile(r"\(-\)|正常|阴性|未见|无异常")

# Step 3 slots 单值字段(每条 strip 后长度 ≥ 2 入 sparse)
_SLOT_SCALAR_FIELDS = (
    "trigger", "location", "nature", "severity", "duration_pattern", "onset_mode",
)
# Step 3 slots list 字段(每条独立成袋)
_SLOT_LIST_FIELDS = ("associated_symptoms", "aggravating", "relieving")


_logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Step 1: NER 调用包装(裸 §9.1 模板)
# ────────────────────────────────────────────────────────────────────────────


def _call_ner(text: str) -> NERResult:
    node, schema = "build_query_step1_ner", "NERResult"
    _attempts.labels(node=node, schema=schema).inc()
    t0 = time.perf_counter()
    try:
        chain = get_llm().with_structured_output(NERResult, method="json_mode").with_retry(stop_after_attempt=3)
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
# Step 2: Entity Linking — 三层归一化(无 LLM,与 ④ extract_symptoms 同设计)
# ────────────────────────────────────────────────────────────────────────────


def _link_one_entity(text: str, embed) -> EntityLinkingMatch:
    """三层归一化单实体。Tier 1 精确 → Tier 2 向量阈值 → Tier 3 占位。

    阈值来源 §9.7 `ENTITY_LINKING_TIER2_THRESHOLD`(默认 0.92,可 .env 覆盖,
    评测调优后微调)。
    """
    text = text.strip()
    if not text:
        return EntityLinkingMatch(
            original_text=text, concept_id=None, preferred_term=None, confidence=0.0
        )

    # ─── Tier 1: 精确别名匹配 ───
    try:
        hit = query_term_by_alias_exact(text)
    except Exception as e:
        _logger.debug("Tier1 alias query failed for '%s': %s", text, e)
        hit = None
    if hit is not None:
        return EntityLinkingMatch(
            original_text=text,
            concept_id=hit["concept_id"],
            preferred_term=hit["preferred_term"],
            confidence=1.0,
        )

    # ─── Tier 2: 向量检索 + 阈值 ───
    try:
        vec = embed.encode_one(text)
        candidates = search_aliases(query_vector=vec, top_k=1)
    except Exception as e:
        _logger.debug("Tier2 vector search failed for '%s': %s", text, e)
        candidates = []

    if candidates:
        top = candidates[0]
        threshold = settings.agent_limits.ENTITY_LINKING_TIER2_THRESHOLD
        if top.get("score", 0.0) >= threshold and top.get("preferred_term"):
            return EntityLinkingMatch(
                original_text=text,
                concept_id=top["concept_id"],
                preferred_term=top["preferred_term"],
                confidence=float(top["score"]),
            )

    # ─── Tier 3: 保留原文 ───
    return EntityLinkingMatch(
        original_text=text, concept_id=None, preferred_term=None, confidence=0.0
    )


def _link_entities(entities: list[NEREntity]) -> list[EntityLinkingMatch]:
    """三层归一化批量 EL,无 LLM。Tier 2 向量检索复用 embedding model 单例。"""
    if not entities:
        return []
    embed = get_embedding_model()
    return [_link_one_entity(ent.text, embed) for ent in entities]


# ────────────────────────────────────────────────────────────────────────────
# Step 4: Query 构建调用包装(裸 §9.1 模板)
# ────────────────────────────────────────────────────────────────────────────


def _call_query_construction(
    confirmed_symptoms: list[str],
    medical_history_summary: str,
    report_positive: list[str],
    report_impressions: list[str],
    filled_slots: dict,
) -> QueryConstructionOutput:
    node, schema = "build_query_step4_query", "QueryConstructionOutput"
    _attempts.labels(node=node, schema=schema).inc()
    t0 = time.perf_counter()
    try:
        chain = get_llm().with_structured_output(QueryConstructionOutput, method="json_mode").with_retry(stop_after_attempt=3)
        return chain.invoke(
            build_query_construction_prompt(
                confirmed_symptoms=confirmed_symptoms,
                medical_history_summary=medical_history_summary,
                report_positive=report_positive,
                report_impressions=report_impressions,
                filled_slots=filled_slots,
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

    # ─── Step 3: Sparse 多字段直采(确定性,无 LLM,RETRIEVAL_EVAL §2)───
    # 来源 A:state 多字段(chief_complaint + slots 单值 + slots list)
    # 来源 B:report_findings 的 positive_findings(全加)+ impressions(阴性过滤)
    sparse_queries: list[str] = []

    def _add(item: str | None) -> None:
        if item is None:
            return
        s = item.strip()
        if len(s) >= 2:
            sparse_queries.append(s)

    slots_dict = state.present_illness_slots.model_dump()

    # 来源 A.1 — 主诉
    _add(state.chief_complaint)
    # 来源 A.2 — slots 单值字段
    for field in _SLOT_SCALAR_FIELDS:
        _add(slots_dict.get(field))
    # 来源 A.3 — slots list 字段(每条独立成袋)
    for field in _SLOT_LIST_FIELDS:
        for item in slots_dict.get(field) or []:
            _add(item)

    # 来源 B — report_findings;report_pos / report_imp 同时供 Step 4 LLM dense_query 改写
    report_pos: list[str] = []
    report_imp: list[str] = []
    for f in state.report_findings:
        report_pos.extend(f.get("positive_findings") or [])
        report_imp.extend(f.get("impressions") or [])

    for item in report_pos:
        _add(item)
    for item in report_imp:
        if item and _NEGATIVE_IMPRESSION_RE.search(item):
            continue  # 跳过阴性印象(BM25 不懂否定,反向贡献)
        _add(item)

    # 保序去重
    sparse_queries = list(dict.fromkeys(sparse_queries))

    # ─── Step 4: Query 构建(LLM)───

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
    )

    update = {
        "standardized_entities": standardized_entities,
        "confirmed_symptoms": confirmed_symptoms,
        "denied_symptoms": denied_symptoms,
        "dense_query": qc.dense_query,
        # sparse_queries 由 Step 3 确定性产出,LLM 不参与(详见 QueryConstructionOutput docstring)
        "sparse_queries": sparse_queries,
    }

    # NER 已执行 → 推进游标(spec §4.1.2 ② Step 1)
    if not is_check_path:
        update["last_nlu_round"] = state.followup_round

    return update
