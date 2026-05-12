"""tests/unit/test_dense_retriever.py — DEV_SPEC §3.2.2 Dense Route 单测。

monkeypatch 替换 embedding singleton 与底层 ANN,验证:
- query 文本经 encode_one 转向量后透传给 search_dense
- top_k 默认取 settings.agent_limits.RETRIEVE_TOP_N
- top_k 显式传值时透传
- source_id_filter 透传
- 不传 vector_type_filter(spec 要求三类向量记录均参与召回)
"""
from __future__ import annotations

import pytest

import src.rag.retrieval.dense_retriever as dr
from config.settings import settings


@pytest.fixture
def fake_pipeline(monkeypatch):
    """替换 get_embedding_model + search_dense,记录调用参数。"""
    state: dict = {"encoded": None, "search_calls": []}

    class _FakeModel:
        def encode_one(self, text: str) -> list[float]:
            state["encoded"] = text
            return [0.5] * 4096

    monkeypatch.setattr(dr, "get_embedding_model", lambda: _FakeModel())

    def _fake_search_dense(
        query_vector: list[float],
        top_k: int = 20,
        source_id_filter: str | None = None,
        vector_type_filter: str | None = None,
    ):
        state["search_calls"].append({
            "query_vector": query_vector,
            "top_k": top_k,
            "source_id_filter": source_id_filter,
            "vector_type_filter": vector_type_filter,
        })
        return [{
            "id": "fake_hit_1",
            "source_chunk_id": "chunk_1",
            "vector_type": "original",
            "original_content": "fake content",
            "source_id": "src_X",
            "score": 0.95,
        }]

    monkeypatch.setattr(dr, "search_dense", _fake_search_dense)
    return state


def test_query_text_encoded_and_passed_to_ann(fake_pipeline) -> None:
    """dense_query → encode_one(text) → 拿到的向量原样喂给 search_dense。"""
    dr.search_dense_route("进食后加重的上腹胀痛伴反酸")
    assert fake_pipeline["encoded"] == "进食后加重的上腹胀痛伴反酸"
    assert len(fake_pipeline["search_calls"]) == 1
    call = fake_pipeline["search_calls"][0]
    assert call["query_vector"] == [0.5] * 4096


def test_default_top_k_is_retrieve_top_n(fake_pipeline) -> None:
    """top_k 不传时取 §9.7 RETRIEVE_TOP_N。"""
    dr.search_dense_route("腹痛")
    assert fake_pipeline["search_calls"][0]["top_k"] == settings.agent_limits.RETRIEVE_TOP_N


def test_explicit_top_k_overrides_default(fake_pipeline) -> None:
    dr.search_dense_route("腹痛", top_k=50)
    assert fake_pipeline["search_calls"][0]["top_k"] == 50


def test_source_id_filter_propagates(fake_pipeline) -> None:
    dr.search_dense_route("腹痛", source_id_filter="src_abc")
    assert fake_pipeline["search_calls"][0]["source_id_filter"] == "src_abc"


def test_no_vector_type_filter_full_coverage(fake_pipeline) -> None:
    """spec §3.2.2 要求 original/summary/question 三类向量均参与召回 →
    不传 vector_type_filter,保持底层默认 None。"""
    dr.search_dense_route("腹痛")
    assert fake_pipeline["search_calls"][0]["vector_type_filter"] is None


def test_returns_list_of_dict_shape(fake_pipeline) -> None:
    out = dr.search_dense_route("腹痛")
    assert isinstance(out, list)
    assert all(isinstance(h, dict) for h in out)
    assert "score" in out[0]
