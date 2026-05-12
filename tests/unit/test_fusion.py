"""tests/unit/test_fusion.py — DEV_SPEC §3.2.2 单阶段多路 RRF + 多向量聚合 单测。

覆盖:
- 单 record 单路 RRF 公式  1/(k+rank)
- 跨路求和(同 record 在 dense + sparse 都命中 → 分数累加)
- 多向量聚合(同 source_chunk_id 多 vector_type record 求和)
- Top-N 截断(按 chunk-level 分数降序,同分按 chunk_id 字母序确定性)
- vector_hits 形态(每命中一种 vector_type 加一条,rank 取首路命中)
- matched_text 三类取值规则(spec 行 1822-1825):
    original → hit['original_content']
    summary  → pg.summary
    question → pg.hypothetical_questions[N](N 解析自 `_q{n}` id 后缀)
- pg_chunk_lookup=None 时 summary/question 的 matched_text 留空字符串
- pg_chunk_lookup 只对截断存活、且需要 summary/question 的 chunk 调用
"""
from __future__ import annotations

import pytest

from src.rag.retrieval.fusion import RRF_K, _parse_question_index, fuse_routes


def _hit(record_id: str, source_chunk_id: str, vector_type: str,
         original_content: str = "", source_id: str = "src", score: float = 1.0) -> dict:
    """构造单条 search_dense / search_sparse_bm25 形态 hit。"""
    return {
        "id": record_id,
        "source_chunk_id": source_chunk_id,
        "vector_type": vector_type,
        "original_content": original_content,
        "source_id": source_id,
        "score": score,
    }


# ───────────────────────────────────────────────────────────
# _parse_question_index
# ───────────────────────────────────────────────────────────


def test_parse_question_index_basic() -> None:
    assert _parse_question_index("chunk_abc_q0") == 0
    assert _parse_question_index("chunk_abc_q2") == 2
    assert _parse_question_index("chunk_abc_q12") == 12


def test_parse_question_index_returns_none_for_non_question_id() -> None:
    """`_summary` / 没后缀 / 后缀非数字 → None。"""
    assert _parse_question_index("chunk_abc_summary") is None
    assert _parse_question_index("chunk_abc") is None
    assert _parse_question_index("chunk_abc_qfoo") is None


def test_parse_question_index_with_q_in_chunk_id() -> None:
    """chunk_id 自身含 `_q` 子串(罕见) — 用 rfind 取最后一个 `_q`,确定性。"""
    assert _parse_question_index("some_q_chunk_q3") == 3


# ───────────────────────────────────────────────────────────
# 单路 RRF 公式
# ───────────────────────────────────────────────────────────


def test_single_dense_route_rrf_formula() -> None:
    """单路 dense,3 个 record → RRF 分数 = 1/(60 + rank);chunk 一对一 record。"""
    dense = [
        _hit("r1", "c1", "original", original_content="A"),
        _hit("r2", "c2", "original", original_content="B"),
        _hit("r3", "c3", "original", original_content="C"),
    ]
    out = fuse_routes(dense_route=dense, sparse_routes=[], top_n=10)
    # 顺序保留: c1 > c2 > c3
    assert [c["source_chunk_id"] for c in out] == ["c1", "c2", "c3"]
    # 分数等于 1/(60+1), 1/(60+2), 1/(60+3)
    assert out[0]["rrf_score"] == pytest.approx(1 / (RRF_K + 1))
    assert out[1]["rrf_score"] == pytest.approx(1 / (RRF_K + 2))
    assert out[2]["rrf_score"] == pytest.approx(1 / (RRF_K + 3))


def test_cross_route_sum_aggregate() -> None:
    """同 record_id 在 dense 与一条 sparse 路都命中 → 两路分数求和。"""
    rid = "rec_X"
    dense = [_hit(rid, "c1", "original", original_content="A")]  # rank 1 in dense
    sparse_routes = [[_hit(rid, "c1", "original", original_content="A")]]  # rank 1 in sparse
    out = fuse_routes(dense_route=dense, sparse_routes=sparse_routes, top_n=10)
    assert len(out) == 1
    expected = 1 / (RRF_K + 1) * 2  # 跨两路求和
    assert out[0]["rrf_score"] == pytest.approx(expected)


def test_multi_vector_aggregate_same_chunk() -> None:
    """同 source_chunk_id 下的 original + summary + question 多 record 分数累加,
    Top 排序按 chunk-level 累计分数。"""
    dense = [
        _hit("c1_original", "c1", "original", original_content="origA"),
        _hit("c1_summary", "c1", "summary", original_content="origA"),
        _hit("c1_q0", "c1", "question", original_content="origA"),
        _hit("c2_original", "c2", "original", original_content="origB"),  # 单 vector
    ]
    out = fuse_routes(dense_route=dense, sparse_routes=[], top_n=10)
    assert [c["source_chunk_id"] for c in out] == ["c1", "c2"]
    c1_score = sum(1 / (RRF_K + r) for r in (1, 2, 3))
    c2_score = 1 / (RRF_K + 4)
    assert out[0]["rrf_score"] == pytest.approx(c1_score)
    assert out[1]["rrf_score"] == pytest.approx(c2_score)
    # vector_hits 形态:c1 有 3 条(original/summary/question),c2 有 1 条
    assert len(out[0]["vector_hits"]) == 3
    assert {h["vector_type"] for h in out[0]["vector_hits"]} == {"original", "summary", "question"}
    assert len(out[1]["vector_hits"]) == 1


def test_top_n_truncation_by_chunk_score() -> None:
    """top_n=2 截断 → 只剩前 2 个 chunk(按 chunk-level 分数降序)。"""
    dense = [
        _hit(f"r{i}", f"c{i}", "original", original_content=f"x{i}")
        for i in range(1, 6)
    ]
    out = fuse_routes(dense_route=dense, sparse_routes=[], top_n=2)
    assert len(out) == 2
    assert [c["source_chunk_id"] for c in out] == ["c1", "c2"]


def test_tie_break_by_chunk_id_alpha_order() -> None:
    """两 chunk 累计分数同分时,按 chunk_id 字母序排(确定性,可重现)。"""
    dense = [_hit("rZ", "cZ", "original", "Z")]
    sparse = [[_hit("rA", "cA", "original", "A")]]
    out = fuse_routes(dense_route=dense, sparse_routes=sparse, top_n=10)
    # 两 chunk 均为 1/(60+1) 同分,字母序 cA < cZ
    assert [c["source_chunk_id"] for c in out] == ["cA", "cZ"]


# ───────────────────────────────────────────────────────────
# vector_hits 形态 + rank 取值
# ───────────────────────────────────────────────────────────


def test_vector_hits_rank_uses_first_seen_route() -> None:
    """同 record 在多路命中时 rank 取首路(dense 在 sparse 之前加入)。"""
    rid = "rX"
    dense = [
        _hit("dummy_first", "cD", "original", "D"),  # rank 1 (占位)
        _hit(rid, "c1", "original", "X"),             # rank 2 in dense
    ]
    sparse = [[_hit(rid, "c1", "original", "X")]]    # rank 1 in sparse,但应保留 dense 首见的 rank=2
    out = fuse_routes(dense_route=dense, sparse_routes=sparse, top_n=10)
    c1 = next(c for c in out if c["source_chunk_id"] == "c1")
    assert len(c1["vector_hits"]) == 1
    assert c1["vector_hits"][0]["rank"] == 2  # 首路是 dense, rank=2


def test_vector_hits_sorted_by_first_rank() -> None:
    """同 chunk 多 vector_type record 时,vector_hits 按 first-seen rank 升序。"""
    dense = [
        _hit("c1_q0", "c1", "question", "X"),       # rank 1
        _hit("c1_original", "c1", "original", "X"), # rank 2
        _hit("c1_summary", "c1", "summary", "X"),   # rank 3
    ]
    out = fuse_routes(dense_route=dense, sparse_routes=[], top_n=10)
    ranks = [h["rank"] for h in out[0]["vector_hits"]]
    assert ranks == sorted(ranks)
    # 顺序与命中顺序一致
    assert [h["vector_type"] for h in out[0]["vector_hits"]] == ["question", "original", "summary"]


# ───────────────────────────────────────────────────────────
# matched_text 三类取值规则(spec §3.2.2 行 1822-1825)
# ───────────────────────────────────────────────────────────


def test_matched_text_original_uses_hit_original_content() -> None:
    """vector_type='original' 的 matched_text = hit['original_content']。"""
    dense = [_hit("c1", "c1", "original", original_content="子块正文段落")]
    out = fuse_routes(dense_route=dense, sparse_routes=[], top_n=10)
    assert out[0]["vector_hits"][0]["matched_text"] == "子块正文段落"


def test_matched_text_summary_from_pg_lookup() -> None:
    """vector_type='summary' 的 matched_text = pg_chunk_lookup 返回的 summary 字段。"""
    dense = [_hit("c1_summary", "c1", "summary", original_content="冗余原文")]

    def _lookup(chunk_ids):
        return {"c1": {"summary": "LLM 改写后的章节摘要"}}

    out = fuse_routes(dense, [], top_n=10, pg_chunk_lookup=_lookup)
    assert out[0]["vector_hits"][0]["matched_text"] == "LLM 改写后的章节摘要"


def test_matched_text_question_parses_index_from_id() -> None:
    """vector_type='question' 的 matched_text:解析 `_q{n}` 后取
    pg.hypothetical_questions[n]。"""
    dense = [
        _hit("c1_q0", "c1", "question", original_content="冗余原文"),
        _hit("c1_q2", "c1", "question", original_content="冗余原文"),
    ]

    def _lookup(chunk_ids):
        return {"c1": {"hypothetical_questions": ["问题0文本", "问题1文本", "问题2文本"]}}

    out = fuse_routes(dense, [], top_n=10, pg_chunk_lookup=_lookup)
    texts = sorted(h["matched_text"] for h in out[0]["vector_hits"])
    assert texts == ["问题0文本", "问题2文本"]


def test_matched_text_summary_question_blank_when_no_pg_lookup() -> None:
    """pg_chunk_lookup=None → summary/question 的 matched_text 留空字符串(单测用)。"""
    dense = [
        _hit("c1_summary", "c1", "summary", original_content="orig"),
        _hit("c1_q0", "c1", "question", original_content="orig"),
    ]
    out = fuse_routes(dense, [], top_n=10)
    for hit in out[0]["vector_hits"]:
        assert hit["matched_text"] == ""


def test_matched_text_question_index_out_of_range_returns_blank() -> None:
    """question 索引越界(N ≥ len) → 空字符串,不抛错。"""
    dense = [_hit("c1_q5", "c1", "question", original_content="orig")]

    def _lookup(chunk_ids):
        return {"c1": {"hypothetical_questions": ["q0", "q1", "q2"]}}

    out = fuse_routes(dense, [], top_n=10, pg_chunk_lookup=_lookup)
    assert out[0]["vector_hits"][0]["matched_text"] == ""


def test_matched_text_pg_data_missing_summary_returns_blank() -> None:
    """PG 行缺 summary 字段(None)→ 空字符串。"""
    dense = [_hit("c1_summary", "c1", "summary", original_content="orig")]

    def _lookup(chunk_ids):
        return {"c1": {"summary": None}}

    out = fuse_routes(dense, [], top_n=10, pg_chunk_lookup=_lookup)
    assert out[0]["vector_hits"][0]["matched_text"] == ""


# ───────────────────────────────────────────────────────────
# pg_chunk_lookup 调用优化
# ───────────────────────────────────────────────────────────


def test_pg_lookup_skipped_when_only_original_hits() -> None:
    """所有命中都是 original → 不调 pg_chunk_lookup(节省 IO)。"""
    dense = [
        _hit("c1", "c1", "original", "A"),
        _hit("c2", "c2", "original", "B"),
    ]
    calls: list = []

    def _lookup(chunk_ids):
        calls.append(set(chunk_ids))
        return {}

    fuse_routes(dense, [], top_n=10, pg_chunk_lookup=_lookup)
    assert calls == []


def test_pg_lookup_only_for_truncated_survivors() -> None:
    """top_n 截断后,只对存活的 chunk 调 PG 回查(避免回查后被截掉的浪费)。"""
    # 5 chunk,前 2 含 summary 命中,后 3 全 original
    dense = [
        _hit("c1_summary", "c1", "summary", "A"),     # rank 1
        _hit("c2_summary", "c2", "summary", "B"),     # rank 2
        _hit("c3_summary", "c3", "summary", "C"),     # rank 3 — 会被 top_n=2 截掉
        _hit("c4", "c4", "original", "D"),
    ]
    calls: list = []

    def _lookup(chunk_ids):
        calls.append(set(chunk_ids))
        return {cid: {"summary": f"sum_{cid}"} for cid in chunk_ids}

    fuse_routes(dense, [], top_n=2, pg_chunk_lookup=_lookup)
    assert len(calls) == 1
    # 只对 top_n 存活的 c1 / c2 回查;c3 被截不查
    assert calls[0] == {"c1", "c2"}


# ───────────────────────────────────────────────────────────
# 边界
# ───────────────────────────────────────────────────────────


def test_empty_routes_returns_empty_list() -> None:
    """全空入入 → 空列表,不抛错。"""
    assert fuse_routes(dense_route=[], sparse_routes=[]) == []


def test_only_sparse_routes_no_dense() -> None:
    """dense 空,sparse 单路 → 仍能融合。"""
    out = fuse_routes(
        dense_route=[],
        sparse_routes=[[_hit("r1", "c1", "original", "A")]],
        top_n=10,
    )
    assert len(out) == 1
    assert out[0]["source_chunk_id"] == "c1"
    assert out[0]["rrf_score"] == pytest.approx(1 / (RRF_K + 1))


def test_skips_empty_sparse_route() -> None:
    """sparse_routes 内含空 list 不应被作为一路计入(否则会减小其他路 rank 的相对权重)。"""
    out = fuse_routes(
        dense_route=[_hit("r1", "c1", "original", "A")],
        sparse_routes=[[], [_hit("r2", "c2", "original", "B")], []],
        top_n=10,
    )
    assert {c["source_chunk_id"] for c in out} == {"c1", "c2"}
