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


class FigureSummaryEnrichmentOutput(BaseModel):
    """*_summary chunk(table_summary / chart_summary / figure_summary)单步产出 schema。

    2026-05-08 决策:figure summary chunk 的 chunk_raw_text 本身是 LLM 生成的
    (不像 child chunk 的 chunk_raw_text 是教材原文),所以"vision 看图 → 产 medical
    statement → enrichment 看 statement → 产 title/summary/questions" 这两步会让
    Stage 1 的视觉幻觉被 Stage 2 固化到 4 字段全错。改成单步:vision/text LLM
    一次性产出 chunk_raw_text(medical_statement)+ enrichment 三字段,LLM 全程持有
    原始信息源(vision 持有图,text LLM 持有 html),避免错误扩散。

    与 ChunkEnrichmentOutput 的差异:多了 medical_statement 字段。其他 3 字段语义对齐
    (用同一套 enrichment 文本规则,见 §3.1.3.2)。
    """

    medical_statement: str = Field(
        ...,
        description="100-300 字医学陈述体描述,作为本图/表 *_summary chunk 的 chunk_raw_text 入库;直接陈述源数据中的医学事实(诊断标准/检查指标/治疗流程等),严禁'图中显示'/'本表列出'等元语言",
    )
    title: str = Field(..., description="≤30 字精准小标题(本图/表的内容标签,不是位置标签);见 ChunkEnrichmentOutput.title")
    summary: str = Field(..., description="≤250 字,与 medical_statement 错位互补的语义抽象,显式化 heading_path 中的病名/主题")
    hypothetical_questions: list[str] = Field(
        default_factory=list,
        description="2-3 条,临床表述与患者口语混合,用户可能针对本图/表提的问题",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="已废弃(同 ChunkEnrichmentOutput.tags),enrichment 不再生成,落库为空列表",
    )
