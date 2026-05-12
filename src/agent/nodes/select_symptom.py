"""src/agent/nodes/select_symptom.py — Agent ⑤ select_discriminative_symptom(DEV_SPEC §4.1.2 ⑤)。

维度缺口优先 + 信息增益贪心选择 + 可问性评估,产出 followup_questions(混合类型)。

执行流程:
  1. 维度缺口优先(配额制 ≤ 2):present_illness_slots 空槽 → LLM 选 1~2 个
     最有鉴别价值的维度 → 占用 MAX_FOLLOWUP_QUESTIONS 名额
  2. 已问症状过滤:
     - Tier 1/2 (linked=True):按 preferred_term 集合差(confirmed ∪ denied ∪ uncertain)
     - Tier 3 (linked=False):embedding 软比对,距离 < 0.3 视为已问
  3. 报告证据优先消费:positive_findings → confirmed_symptoms;negative_findings →
     denied_symptoms;命中即跳过追问
  4. 信息增益(二元熵)排序候选 → 贪心循环内可问性评估(LLM):
     - askable=True → followup_questions(symptom)
     - askable=False → unaskable_symptoms(附 info_gain)
     - 名额到 → 停止
  5. 收尾:首轮 (followup_round == 0) 跳过早退检查;否则若症状级 followup_questions
     非空但最高增益 < ASKABLE_GAIN_THRESHOLD → 清空症状级条目;info_gain 设为
     症状级最高(无则 0.0)

LLM 调用两处(维度选择 + 每症状可问性评估),按 §9.1 中安全级模板独立写埋点。
"""
from __future__ import annotations

import logging
import math
import time

from config.settings import settings
from src.agent.schemas.symptom_selection import (
    AskabilityJudgment,
    DimensionSelection,
)
from src.agent.state import MedicalState
from src.common.metrics import _attempts, _failures, _latency, retry_observer
from src.models.embedding_model import get_embedding_model
from src.models.llm_client import get_llm
from src.prompts.agent import (
    build_askability_prompt,
    build_dimension_selection_prompt,
)


_logger = logging.getLogger(__name__)

_TIER3_SOFT_MATCH_DIST = 0.3  # cosine distance < 0.3 视为同义
_DIMENSION_QUOTA_MAX = 2  # spec §4.1.2 ⑤ "1~2 个维度"


# ────────────────────────────────────────────────────────────────────────────
# LLM 调用 1:维度缺口选择(中安全等级,失败 → 跳过维度追问)
# ────────────────────────────────────────────────────────────────────────────


def _call_dimension_selection(
    chief_complaint: str,
    empty_slots: list[str],
    candidate_diseases_preview: list[str],
    quota: int,
) -> list[str]:
    node, schema = "select_symptom_dimension", "DimensionSelection"
    _attempts.labels(node=node, schema=schema).inc()
    t0 = time.perf_counter()
    try:
        chain = get_llm().with_structured_output(DimensionSelection).with_retry(
            stop_after_attempt=3
        )
        result: DimensionSelection = chain.invoke(
            build_dimension_selection_prompt(
                chief_complaint=chief_complaint,
                empty_slots=empty_slots,
                candidate_diseases_preview=candidate_diseases_preview,
                quota=quota,
            ),
            config={
                "callbacks": [retry_observer],
                "metadata": {"node": node, "schema": schema},
            },
        )
        # 过滤 LLM 可能返回的不存在槽名,保留与空槽列表交集
        valid = [s for s in result.selected_slots if s in set(empty_slots)]
        return valid[:quota]
    except Exception as e:
        _failures.labels(
            node=node, schema=schema, exception_type=type(e).__name__
        ).inc()
        _logger.warning(
            "[%s] dimension selection failed, fall back to no dimension: %s",
            node, e,
        )
        # spec §9.3 中安全等级失败处理:跳过维度追问,完全退化为症状级
        return []
    finally:
        _latency.labels(node=node, schema=schema).observe(
            time.perf_counter() - t0
        )


# ────────────────────────────────────────────────────────────────────────────
# LLM 调用 2:单症状可问性评估(中安全等级,失败 → 默认不可问保守策略)
# ────────────────────────────────────────────────────────────────────────────


def _call_askability(symptom: str) -> bool:
    node, schema = "select_symptom_askability", "AskabilityJudgment"
    _attempts.labels(node=node, schema=schema).inc()
    t0 = time.perf_counter()
    try:
        chain = get_llm().with_structured_output(AskabilityJudgment).with_retry(
            stop_after_attempt=3
        )
        result: AskabilityJudgment = chain.invoke(
            build_askability_prompt(symptom),
            config={
                "callbacks": [retry_observer],
                "metadata": {"node": node, "schema": schema},
            },
        )
        return result.askable
    except Exception as e:
        _failures.labels(
            node=node, schema=schema, exception_type=type(e).__name__
        ).inc()
        _logger.warning(
            "[%s] askability for '%s' failed, defaulting to unaskable: %s",
            node, symptom, e,
        )
        # spec §9.3 中安全等级失败处理:保守策略,宁可少问不误问
        return False
    finally:
        _latency.labels(node=node, schema=schema).observe(
            time.perf_counter() - t0
        )


# ────────────────────────────────────────────────────────────────────────────
# 信息增益(二元熵)
# ────────────────────────────────────────────────────────────────────────────


def _binary_entropy(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -p * math.log2(p) - (1 - p) * math.log2(1 - p)


def _symptom_frequency(symptom_text: str, chunks_text: list[str]) -> float:
    """该症状在 candidate_chunks 中的出现频率(0~1)。"""
    if not chunks_text:
        return 0.0
    hits = sum(1 for t in chunks_text if symptom_text in t)
    return hits / len(chunks_text)


# ────────────────────────────────────────────────────────────────────────────
# 已问症状过滤
# ────────────────────────────────────────────────────────────────────────────


def _filter_already_asked(
    extracted: list[dict],
    asked_terms: set[str],
    asked_texts: list[str],
    embed,
) -> list[dict]:
    """Tier 1/2 按 preferred_term 集合差;Tier 3 用 embedding 软比对。"""
    out: list[dict] = []
    if asked_texts:
        try:
            asked_vecs = embed.encode(asked_texts)
        except Exception:
            asked_vecs = None
    else:
        asked_vecs = None

    for item in extracted:
        if item["linked"] and item["preferred_term"] in asked_terms:
            continue
        if not item["linked"] and asked_vecs:
            try:
                v = embed.encode_one(item["text"])
                # cosine distance = 1 - cosine similarity;cosine for normalized = dot
                # 但 SentenceTransformer 默认未归一,这里粗略用距离阈值
                from numpy import array, dot
                from numpy.linalg import norm
                vn = array(v)
                close = False
                for av in asked_vecs:
                    avn = array(av)
                    sim = dot(vn, avn) / (norm(vn) * norm(avn) + 1e-9)
                    if (1.0 - float(sim)) < _TIER3_SOFT_MATCH_DIST:
                        close = True
                        break
                if close:
                    continue
            except Exception:
                pass
        out.append(item)
    return out


# ────────────────────────────────────────────────────────────────────────────
# 报告证据消费
# ────────────────────────────────────────────────────────────────────────────


def _consume_report_evidence(
    extracted: list[dict],
    report_findings: list[dict],
    confirmed_symptoms: list[str],
    denied_symptoms: list[str],
) -> tuple[list[dict], list[str], list[str]]:
    """positive_findings → confirmed,negative_findings → denied;命中症状直接消费。"""
    pos_set: set[str] = set()
    neg_set: set[str] = set()
    for f in report_findings:
        pos_set.update(f.get("positive_findings") or [])
        neg_set.update(f.get("negative_findings") or [])

    new_confirmed = list(confirmed_symptoms)
    new_denied = list(denied_symptoms)
    remaining: list[dict] = []
    for item in extracted:
        pt = item.get("preferred_term")
        text = item.get("text")
        consumed = False
        if pt and pt in pos_set:
            if pt not in new_confirmed:
                new_confirmed.append(pt)
            consumed = True
        elif text and text in pos_set:
            if text not in new_confirmed:
                new_confirmed.append(text)
            consumed = True
        elif pt and pt in neg_set:
            if pt not in new_denied:
                new_denied.append(pt)
            consumed = True
        elif text and text in neg_set:
            if text not in new_denied:
                new_denied.append(text)
            consumed = True
        if not consumed:
            remaining.append(item)
    return remaining, new_confirmed, new_denied


# ────────────────────────────────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────────────────────────────────


def _candidate_text(chunk: dict) -> str:
    parts = []
    for vh in chunk.get("vector_hits") or []:
        mt = (vh.get("matched_text") or "").strip()
        if mt:
            parts.append(mt)
    return " ".join(parts)


def _empty_slots(slots) -> list[str]:
    """从 PresentIllnessSlots 抽空槽列表(单值=None,多值=[])。"""
    out: list[str] = []
    data = slots.model_dump()
    for k, v in data.items():
        if v is None or v == []:
            out.append(k)
    return out


def select_discriminative_symptom(state: MedicalState) -> dict:
    K = settings.agent_limits.MAX_FOLLOWUP_QUESTIONS
    threshold = settings.agent_limits.ASKABLE_GAIN_THRESHOLD

    # ─── 维度缺口优先 ───
    empty_slots = _empty_slots(state.present_illness_slots)
    dimension_picks: list[str] = []
    candidate_disease_preview = [
        c.get("source_chunk_id", "") for c in state.candidate_chunks[:5]
    ]
    if empty_slots:
        quota = min(_DIMENSION_QUOTA_MAX, K)
        dimension_picks = _call_dimension_selection(
            chief_complaint=state.chief_complaint,
            empty_slots=empty_slots,
            candidate_diseases_preview=candidate_disease_preview,
            quota=quota,
        )

    followup_questions: list[dict] = [
        {"slot": s, "type": "dimension"} for s in dimension_picks
    ]
    remaining_quota = K - len(followup_questions)

    # ─── 报告证据优先消费(可能直接消化掉部分症状) ───
    extracted_remaining, new_confirmed, new_denied = _consume_report_evidence(
        list(state.extracted_symptoms),
        state.report_findings,
        state.confirmed_symptoms,
        state.denied_symptoms,
    )

    # ─── 已问症状过滤 ───
    asked_terms = (
        set(new_confirmed) | set(new_denied) | set(state.uncertain_symptoms)
    )
    asked_texts = list(asked_terms)
    embed = get_embedding_model()
    filtered = _filter_already_asked(
        extracted_remaining, asked_terms, asked_texts, embed
    )

    # ─── 信息增益排序 ───
    chunks_text = [_candidate_text(c) for c in state.candidate_chunks]
    gains: list[tuple[dict, float]] = []
    for item in filtered:
        text = item.get("preferred_term") or item.get("text") or ""
        p = _symptom_frequency(text, chunks_text)
        gains.append((item, _binary_entropy(p)))
    gains.sort(key=lambda x: x[1], reverse=True)

    # ─── 贪心 + 可问性评估(剩余名额内) ───
    symptom_questions: list[dict] = []
    unaskable: list[dict] = []
    symptom_max_gain = 0.0
    for item, gain in gains:
        if remaining_quota <= 0:
            break
        term = item.get("preferred_term") or item.get("text") or ""
        if not term:
            continue
        if _call_askability(term):
            symptom_questions.append({"term": term, "type": "symptom"})
            symptom_max_gain = max(symptom_max_gain, gain)
            remaining_quota -= 1
        else:
            unaskable.append({"preferred_term": term, "info_gain": gain})

    # ─── 阈值兜底 ───
    if state.followup_round > 0 and symptom_questions:
        if symptom_max_gain < threshold:
            symptom_questions = []  # 清空症状级
            symptom_max_gain = 0.0

    followup_questions.extend(symptom_questions)

    return {
        "followup_questions": followup_questions,
        "unaskable_symptoms": unaskable,
        "info_gain": symptom_max_gain if symptom_questions else 0.0,
        "confirmed_symptoms": new_confirmed,
        "denied_symptoms": new_denied,
    }
