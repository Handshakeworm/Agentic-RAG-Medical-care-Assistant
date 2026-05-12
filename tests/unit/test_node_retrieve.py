"""tests/unit/test_node_retrieve.py — F4 ③ retrieve 单元测试。

Mock dense_retriever / sparse_retriever / fuse_routes;验证:
- 调用顺序 + 参数透传
- candidate_chunks 覆盖写入
- 空 dense_query → 不调 dense_retriever;空 sparse_queries → 不调 sparse_retriever
"""
from __future__ import annotations

from unittest.mock import patch

from src.agent.state import create_initial_state


@patch("src.agent.nodes.retrieve.fuse_routes", return_value=[{"source_chunk_id": "c1"}])
@patch("src.agent.nodes.retrieve.search_sparse_routes", return_value=[[{"id": "s1"}]])
@patch("src.agent.nodes.retrieve.search_dense_route", return_value=[{"id": "d1"}])
def test_full_retrieve_flow(mock_dense, mock_sparse, mock_fuse):
    from src.agent.nodes.retrieve import retrieve

    s = create_initial_state(patient_id="P", patient_input="x")
    s.dense_query = "持续3天的上腹痛"
    s.sparse_queries = ["腹痛 肚子疼", "发热"]
    update = retrieve(s)

    mock_dense.assert_called_once_with("持续3天的上腹痛")
    mock_sparse.assert_called_once_with(["腹痛 肚子疼", "发热"])
    mock_fuse.assert_called_once()
    assert update["candidate_chunks"] == [{"source_chunk_id": "c1"}]


@patch("src.agent.nodes.retrieve.fuse_routes", return_value=[])
@patch("src.agent.nodes.retrieve.search_sparse_routes", return_value=[])
@patch("src.agent.nodes.retrieve.search_dense_route", return_value=[])
def test_empty_dense_skips_dense_route(mock_dense, mock_sparse, mock_fuse):
    from src.agent.nodes.retrieve import retrieve

    s = create_initial_state(patient_id="P", patient_input="x")
    # dense_query 为空,sparse 仍可用
    s.sparse_queries = ["腹痛"]
    retrieve(s)

    mock_dense.assert_not_called()
    mock_sparse.assert_called_once()


@patch("src.agent.nodes.retrieve.fuse_routes", return_value=[])
@patch("src.agent.nodes.retrieve.search_sparse_routes", return_value=[])
@patch("src.agent.nodes.retrieve.search_dense_route", return_value=[])
def test_empty_sparse_skips_sparse_route(mock_dense, mock_sparse, mock_fuse):
    from src.agent.nodes.retrieve import retrieve

    s = create_initial_state(patient_id="P", patient_input="x")
    s.dense_query = "上腹痛"
    retrieve(s)

    mock_dense.assert_called_once()
    mock_sparse.assert_not_called()
