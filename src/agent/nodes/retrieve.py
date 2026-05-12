"""src/agent/nodes/retrieve.py — Agent ③ retrieve 节点(DEV_SPEC §4.1.2 ③)。

混合检索:dense_query → search_dense_route(单路) + sparse_queries → search_sparse_routes
(N 路) → fuse_routes 单阶段 RRF + 多向量聚合 → 截断 RETRIEVE_TOP_N → 覆盖写入
candidate_chunks。

PG 回查 callable 注入用 chunks_lookup.lookup_chunk_summary_question(spec §3.2.2:
fusion 用 PG 数据补 summary/question 的 matched_text)。

每轮覆盖写入(spec §4.1.2 ③ 设计理由:build_query 已融合所有累积证据重写 query,
新结果天然反映最新状态,无需保留历史候选)。
"""
from __future__ import annotations

from src.agent.state import MedicalState
from src.agent.utils.chunks_lookup import lookup_chunk_summary_question
from src.rag.retrieval.dense_retriever import search_dense_route
from src.rag.retrieval.fusion import fuse_routes
from src.rag.retrieval.sparse_retriever import search_sparse_routes


def retrieve(state: MedicalState) -> dict:
    """混合检索 + RRF 融合,覆盖写入 candidate_chunks。"""
    dense_query = state.dense_query.strip()
    sparse_queries = [q for q in state.sparse_queries if q.strip()]

    # dense / sparse 任一可空(spec §3.2.2:fuse_routes 跳过空路径)
    dense_route = search_dense_route(dense_query) if dense_query else []
    sparse_routes_results = (
        search_sparse_routes(sparse_queries) if sparse_queries else []
    )

    candidates = fuse_routes(
        dense_route=dense_route,
        sparse_routes=sparse_routes_results,
        pg_chunk_lookup=lookup_chunk_summary_question,
    )

    return {"candidate_chunks": candidates}
