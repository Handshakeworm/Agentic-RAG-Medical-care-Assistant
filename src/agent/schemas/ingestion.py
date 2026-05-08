"""数据摄取层结构化输出 Schema(DEV_SPEC §9.5)。

`ChunkEnrichmentOutput` 是 §3.1.3 enrichment 的 LLM 输出契约,被
`src/rag/ingestion/enrichment.py` 通过 `llm.with_structured_output()` 调用。

字段定义直接来源于 §9.5 第 11 项,**禁止私改字段名 / 类型 / 描述**——
spec authority hierarchy 规定 §9.5 的 schema 列表是权威,业务代码必须对齐。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChunkEnrichmentOutput(BaseModel):
    """3.1.3 enrichment LLM 输出 — 为原始 chunk 生成增强元数据。

    注:`tags` 字段已废弃(2026-05 决策,见 §3.1.3.2),enrichment 不再生成,
    保留字段是为了兼容历史 schema 和未来若启用 §3.2.3 Pre-filter 时的 backfill。
    """

    title: str = Field(..., description="chunk 标题（LLM 生成）")
    summary: str = Field(..., description="chunk 摘要（LLM 生成）")
    tags: list[str] = Field(
        default_factory=list,
        description="语义标签（已废弃，保留字段兼容；enrichment 不再生成，落库为空列表）",
    )
    hypothetical_questions: list[str] = Field(
        default_factory=list,
        description="假设性问题（HyDE 反向生成，用于增强检索召回）",
    )
