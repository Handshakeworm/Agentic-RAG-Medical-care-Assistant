"""tests/unit/test_reranker_fallback.py — DEV_SPEC §3.2.3 rerank_with_fallback 单测。

覆盖 spec §3.2.3 "默认策略"段强约束:
- 正常路径:精排返回降序 idx 列表,与 reranker.rerank 输出一致
- enabled=False:直接 fallback 原序(spec "None(关闭)"模式)
- documents 空:返 []
- top_k 截断
- 模型异常 → fallback 原序(必须不抛异常)
- 超时 → fallback 原序
- timeout_sec=None:不限超时
"""
from __future__ import annotations

import time

import pytest

from src.rag.retrieval.reranker import rerank_with_fallback


class _FakeReranker:
    """单测 mock,无需加载真模型。rerank 返回 (idx, score) 降序列表。"""
    def __init__(self, scores: list[float] | None = None,
                 raise_exc: Exception | None = None,
                 sleep_sec: float = 0.0):
        self._scores = scores
        self._raise = raise_exc
        self._sleep = sleep_sec

    def rerank(self, query: str, documents: list[str], top_k: int | None = None):
        if self._sleep:
            time.sleep(self._sleep)
        if self._raise:
            raise self._raise
        scores = self._scores if self._scores is not None else [float(i) for i in range(len(documents))]
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        if top_k is not None:
            ranked = ranked[:top_k]
        return ranked


def test_normal_rerank_returns_idx_in_descending_score() -> None:
    """正常精排:scores=[0.1, 0.9, 0.5] → idx 顺序 [1, 2, 0]。"""
    rk = _FakeReranker(scores=[0.1, 0.9, 0.5])
    out = rerank_with_fallback(
        query="腹痛", documents=["d0", "d1", "d2"], reranker=rk, timeout_sec=None,
    )
    assert out == [1, 2, 0]


def test_top_k_truncation_after_rerank() -> None:
    """top_k=2:取分数最高的前 2 个 idx。"""
    rk = _FakeReranker(scores=[0.1, 0.9, 0.5, 0.7])
    out = rerank_with_fallback(
        query="腹痛", documents=["d0", "d1", "d2", "d3"],
        reranker=rk, top_k=2, timeout_sec=None,
    )
    assert out == [1, 3]


def test_disabled_returns_original_order() -> None:
    """spec §3.2.3 'None(关闭)'模式:enabled=False → 原序 idx,不调 reranker。"""
    rk = _FakeReranker(raise_exc=RuntimeError("不应被调用"))
    out = rerank_with_fallback(
        query="腹痛", documents=["d0", "d1", "d2"],
        enabled=False, reranker=rk, timeout_sec=None,
    )
    assert out == [0, 1, 2]


def test_disabled_with_top_k() -> None:
    """enabled=False + top_k=2 → 原序前 2 个 idx。"""
    rk = _FakeReranker(raise_exc=RuntimeError("不应被调用"))
    out = rerank_with_fallback(
        query="x", documents=["a", "b", "c", "d"],
        enabled=False, reranker=rk, top_k=2, timeout_sec=None,
    )
    assert out == [0, 1]


def test_empty_documents_returns_empty() -> None:
    """空 documents → []。"""
    rk = _FakeReranker()
    assert rerank_with_fallback(query="x", documents=[], reranker=rk, timeout_sec=None) == []


def test_exception_falls_back_to_original_order() -> None:
    """精排抛异常 → 不冒泡,fallback 原序(spec §3.2.3 强约束)。"""
    rk = _FakeReranker(raise_exc=ValueError("model crashed"))
    out = rerank_with_fallback(
        query="x", documents=["d0", "d1", "d2"], reranker=rk, timeout_sec=None,
    )
    assert out == [0, 1, 2]


def test_exception_with_top_k_falls_back_truncated() -> None:
    """异常 + top_k=2 → 原序前 2 个 idx。"""
    rk = _FakeReranker(raise_exc=RuntimeError("oom"))
    out = rerank_with_fallback(
        query="x", documents=["d0", "d1", "d2", "d3"],
        reranker=rk, top_k=2, timeout_sec=None,
    )
    assert out == [0, 1]


def test_timeout_falls_back_to_original_order() -> None:
    """精排耗时 > timeout_sec → fallback 原序(best-effort 超时)。"""
    rk = _FakeReranker(scores=[0.1, 0.9, 0.5], sleep_sec=2.0)
    out = rerank_with_fallback(
        query="x", documents=["d0", "d1", "d2"],
        reranker=rk, timeout_sec=0.1,
    )
    assert out == [0, 1, 2]


def test_timeout_none_runs_without_limit() -> None:
    """timeout_sec=None → 不走 ThreadPool 超时分支,正常返回精排结果。"""
    rk = _FakeReranker(scores=[0.1, 0.9, 0.5], sleep_sec=0.05)
    out = rerank_with_fallback(
        query="x", documents=["d0", "d1", "d2"],
        reranker=rk, timeout_sec=None,
    )
    assert out == [1, 2, 0]
