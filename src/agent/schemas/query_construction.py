"""Agent ② build_query Step 4 Query 构建 LLM 输出 schema(DEV_SPEC §9.5 第 5 项)。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class QueryConstructionOutput(BaseModel):
    """② build_query Step 4 LLM 输出 — 仅 dense_query 一字段。

    sparse_queries 由 Step 3 确定性算法(基于已链接症状的 concept_id)产出,
    LLM 不参与;曾经把 sparse_queries 也作为 LLM 输出字段(为 schema 完整),
    但 LLM 看到 prompt 里的"sparse 已定不要改"会合理地省略输出,触发 schema
    校验失败。改为 LLM 只承担 dense_query 改写一职,sparse 路完全交给确定性
    工具,避免 prompt/schema 内在冲突(2026-05-14)。
    """

    dense_query: str = Field(..., description="用于 Dense 检索的语义查询文本")
