"""tests/unit/test_metadata_filter.py — DEV_SPEC §3.2.3 post-filter 单测。

覆盖:
- 空 predicates / None → 透传
- 单 predicate True/False 保留 / 剔除
- 多 predicate AND 语义(任一 False 即剔除)
- spec '宽松策略':predicate 返回 None / 抛 KeyError/AttributeError/TypeError → 保留
- 其他异常冒泡(逻辑错误不该静默)
- source_id_in_allowlist factory:在 allowlist / 不在 / 字段缺失三种情况
- 顺序保留
"""
from __future__ import annotations

import pytest

from src.rag.retrieval.metadata_filter import apply_post_filters, source_id_in_allowlist


def _cand(cid: str, **extra) -> dict:
    """构造 §3.2.2 形态 candidate(可加额外字段供 post-filter 测试)。"""
    return {"source_chunk_id": cid, "rrf_score": 1.0, "vector_hits": [], **extra}


# ───────────────────────────────────────────────────────────
# apply_post_filters 框架
# ───────────────────────────────────────────────────────────


def test_no_predicates_passes_through() -> None:
    cands = [_cand("c1"), _cand("c2")]
    assert apply_post_filters(cands, predicates=None) == cands
    assert apply_post_filters(cands, predicates=[]) == cands


def test_single_predicate_true_keeps_candidate() -> None:
    cands = [_cand("c1"), _cand("c2")]
    out = apply_post_filters(cands, [lambda c: True])
    assert out == cands


def test_single_predicate_false_removes_candidate() -> None:
    cands = [_cand("c1"), _cand("c2")]
    out = apply_post_filters(cands, [lambda c: c["source_chunk_id"] == "c1"])
    assert [c["source_chunk_id"] for c in out] == ["c1"]


def test_multiple_predicates_and_semantics() -> None:
    """两个 predicate 同时为 True 才保留。"""
    cands = [_cand("c1", lang="zh"), _cand("c2", lang="en"), _cand("c3", lang="zh")]
    out = apply_post_filters(cands, [
        lambda c: c["lang"] == "zh",
        lambda c: c["source_chunk_id"] != "c3",
    ])
    assert [c["source_chunk_id"] for c in out] == ["c1"]


def test_predicate_returning_none_is_lenient() -> None:
    """spec §3.2.3:predicate 返 None(无法判断)→ 保留。"""
    cands = [_cand("c1")]
    out = apply_post_filters(cands, [lambda c: None])
    assert out == cands


def test_predicate_keyerror_is_lenient() -> None:
    """字段缺失抛 KeyError → spec '宽松策略' 保留。"""
    cands = [_cand("c1")]  # 没有 doc_type 字段
    out = apply_post_filters(cands, [lambda c: c["doc_type"] == "guideline"])
    assert out == cands


def test_predicate_attributeerror_is_lenient() -> None:
    """字段为 None 后被 attr 访问抛 AttributeError → 保留。"""
    cands = [_cand("c1", lang=None)]
    out = apply_post_filters(cands, [lambda c: c["lang"].startswith("zh")])
    assert out == cands


def test_predicate_typeerror_is_lenient() -> None:
    """类型错误(常见于 None 参与比较)→ 保留。"""
    cands = [_cand("c1", year=None)]
    out = apply_post_filters(cands, [lambda c: c["year"] >= 2020])
    assert out == cands


def test_other_exception_propagates() -> None:
    """非字段缺失类异常(如 ValueError)冒泡,不被 swallow(逻辑错误暴露)。"""
    cands = [_cand("c1")]

    def _buggy(c):
        raise ValueError("buggy predicate")

    with pytest.raises(ValueError, match="buggy predicate"):
        apply_post_filters(cands, [_buggy])


def test_preserves_input_order() -> None:
    """过滤后顺序保留(融合后已按 RRF 排好序,post-filter 不改顺序)。"""
    cands = [_cand("c3"), _cand("c1"), _cand("c5"), _cand("c2")]
    out = apply_post_filters(cands, [lambda c: c["source_chunk_id"] != "c5"])
    assert [c["source_chunk_id"] for c in out] == ["c3", "c1", "c2"]


def test_all_filtered_out_returns_empty() -> None:
    cands = [_cand("c1"), _cand("c2")]
    out = apply_post_filters(cands, [lambda c: False])
    assert out == []


# ───────────────────────────────────────────────────────────
# source_id_in_allowlist factory
# ───────────────────────────────────────────────────────────


def test_source_id_in_allowlist_keeps_allowed() -> None:
    cands = [_cand("c1", source_id="src_A"), _cand("c2", source_id="src_B")]
    pred = source_id_in_allowlist({"src_A"})
    out = apply_post_filters(cands, [pred])
    assert [c["source_chunk_id"] for c in out] == ["c1"]


def test_source_id_in_allowlist_missing_field_lenient() -> None:
    """candidate 没 source_id 字段 → predicate 返 None → 宽松保留。"""
    cands = [_cand("c1")]  # 没 source_id
    pred = source_id_in_allowlist({"src_A"})
    out = apply_post_filters(cands, [pred])
    assert out == cands


def test_source_id_in_allowlist_custom_field() -> None:
    """field 参数支持改名(spec 没强制 candidate 形态含 source_id,留扩展)。"""
    cands = [_cand("c1", origin_id="X"), _cand("c2", origin_id="Y")]
    pred = source_id_in_allowlist({"X"}, field="origin_id")
    out = apply_post_filters(cands, [pred])
    assert [c["source_chunk_id"] for c in out] == ["c1"]
