"""src/agent/nodes/extract_symptoms.py — Agent ④ extract_symptoms 节点(DEV_SPEC §4.1.2 ④)。

两阶段零 LLM:

  阶段一(关键词):TF-IDF 从 reranked 之前的 candidate_chunks 提取关键词
                   候选 — 用 TfidfVectorizer 简单 tokenize + score 截断
  阶段二(三层归一化):
    Tier 1  精确别名匹配 — query_term_by_alias_exact 命中即用
    Tier 2  向量检索 + 阈值截断(>= ENTITY_LINKING_TIER2_THRESHOLD)
    Tier 3  保留原文,linked=False(送 ⑤ 软比对)

输出 extracted_symptoms 列表,每项 `{"text", "preferred_term", "linked"}`,
按 (linked desc, text asc) 排序保证幂等。

**已知局限**(spec §4.1.2 ④):TF-IDF 只能拆词级关键词,描述性鉴别线索(如
"右上腹持续性钝痛向右肩背部放射")会被打散,组合语义留给 ⑩ Step 1 LLM 直读 chunk
原文捕获。
"""
from __future__ import annotations

import logging

from sklearn.feature_extraction.text import TfidfVectorizer

from config.settings import settings
from src.agent.state import MedicalState
from src.db.milvus.terms_collection import (
    query_term_by_alias_exact,
    search_aliases,
)
from src.models.embedding_model import get_embedding_model


_logger = logging.getLogger(__name__)


# 中文症状词典常出现的连接字符,加进 TF-IDF 词典里防止整段句子被当一个 token
_TFIDF_TOP_K = 30  # 单 chunk 取前 30 个 TF-IDF 关键词
_MAX_KEYWORD_LEN = 12
_MIN_KEYWORD_LEN = 2


def _extract_keywords(chunks_text: list[str]) -> list[str]:
    """阶段一:TF-IDF 关键词提取(中文用 char-level n-gram 兜底,无需 jieba 依赖)。

    注:正式生产可换 KeyBERT(spec §4.1.2 ④ 列了两选项),这里用 sklearn TfidfVectorizer
    char_wb 模式做 2-4gram,无外部依赖,适合 MVP。
    """
    if not chunks_text:
        return []
    vec = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(2, 4),
        max_features=500,
    )
    try:
        matrix = vec.fit_transform(chunks_text)
    except ValueError:
        # 全是停用词或文本极短
        return []
    feature_names = vec.get_feature_names_out()
    # 取所有 chunk 上 TF-IDF 平均分 Top-K
    avg_scores = matrix.mean(axis=0).A1
    sorted_idx = avg_scores.argsort()[::-1][:_TFIDF_TOP_K]

    out: list[str] = []
    seen: set[str] = set()
    for i in sorted_idx:
        kw = feature_names[i].strip()
        if (
            _MIN_KEYWORD_LEN <= len(kw) <= _MAX_KEYWORD_LEN
            and kw not in seen
        ):
            seen.add(kw)
            out.append(kw)
    return out


def _normalize_keyword(kw: str, embed) -> dict | None:
    """三层归一化。Tier 3 返回带 `linked=False` 的占位;无法处理(空字符串等)→ None。"""
    kw = kw.strip()
    if not kw:
        return None

    # ─── Tier 1: 精确别名匹配 ───
    try:
        hit = query_term_by_alias_exact(kw)
    except Exception as e:
        _logger.debug("Tier1 alias query failed for '%s': %s", kw, e)
        hit = None
    if hit is not None:
        return {
            "text": kw,
            "preferred_term": hit["preferred_term"],
            "linked": True,
        }

    # ─── Tier 2: 向量检索 + 阈值 ───
    try:
        vec = embed.encode_one(kw)
        candidates = search_aliases(query_vector=vec, top_k=1)
    except Exception as e:
        _logger.debug("Tier2 vector search failed for '%s': %s", kw, e)
        candidates = []

    if candidates:
        top = candidates[0]
        threshold = settings.agent_limits.ENTITY_LINKING_TIER2_THRESHOLD
        if top.get("score", 0.0) >= threshold and top.get("preferred_term"):
            return {
                "text": kw,
                "preferred_term": top["preferred_term"],
                "linked": True,
            }

    # ─── Tier 3: 保留原文 ───
    return {"text": kw, "preferred_term": None, "linked": False}


def _candidate_text(chunk: dict) -> str:
    """从 candidate_chunks 抽 chunk 文本:matched_text(各 vector_hits 取最长非空)。"""
    parts = []
    for vh in chunk.get("vector_hits") or []:
        mt = (vh.get("matched_text") or "").strip()
        if mt:
            parts.append(mt)
    return " ".join(parts)


def extract_symptoms(state: MedicalState) -> dict:
    """两阶段零 LLM 提取症状,输出去重列表。"""
    chunks_text = [
        _candidate_text(c) for c in state.candidate_chunks
    ]
    chunks_text = [t for t in chunks_text if t]

    keywords = _extract_keywords(chunks_text)
    if not keywords:
        return {"extracted_symptoms": []}

    embed = get_embedding_model()
    symptoms: list[dict] = []
    seen_keys: set[tuple[str, str | None]] = set()
    for kw in keywords:
        rec = _normalize_keyword(kw, embed)
        if rec is None:
            continue
        key = (rec["text"], rec["preferred_term"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        symptoms.append(rec)

    # 排序:linked=True 先,同组内 text 字母序(确定性,便于回归比对)
    symptoms.sort(key=lambda r: (not r["linked"], r["text"]))
    return {"extracted_symptoms": symptoms}
