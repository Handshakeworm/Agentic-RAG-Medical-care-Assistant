"""Agent ② build_query Step 4 Query 构建 LLM 输出 schema(DEV_SPEC §9.5 第 5 项)。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class QueryConstructionOutput(BaseModel):
    """② build_query Step 4 LLM 输出。

    Dense 用 LLM 改写后的语义连贯句;Sparse 用 OR 词袋(每个症状维度一项)。
    """

    dense_query: str = Field(..., description="用于 Dense 检索的语义查询文本")
    sparse_queries: list[str] = Field(
        ..., min_length=1, description="用于 Sparse 检索的关键词查询列表(每维度一项)"
    )
