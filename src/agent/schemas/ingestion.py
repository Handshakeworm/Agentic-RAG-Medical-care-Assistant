"""数据摄取层结构化输出 Schema(DEV_SPEC §9.5)。

`ChunkEnrichmentOutput` 是 §3.1.3 enrichment 的 LLM 输出契约,被
`src/rag/ingestion/enrichment.py` 通过 `llm.with_structured_output()` 调用。

字段定义直接来源于 §9.5 第 11 项,**禁止私改字段名 / 类型 / 描述**——
spec authority hierarchy 规定 §9.5 的 schema 列表是权威,业务代码必须对齐。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChunkEnrichmentOutput(BaseModel):
    """3.1.3 enrichment LLM 输出 — 为原始 chunk 生成增强元数据。"""

    title: str = Field(..., description="chunk 标题（LLM 生成）")
    summary: str = Field(..., description="chunk 摘要（LLM 生成）")
    hypothetical_questions: list[str] = Field(
        default_factory=list,
        description="假设性问题（HyDE 反向生成，用于增强检索召回）",
    )


class FigureSummaryEnrichmentOutput(BaseModel):
    """table / figure chunk 单步产出 schema(原"summary chunk"叫法已弃,见 §3.1.2 修订)。

    2026-05-08 决策:图表 chunk 的 medical_statement 是 LLM 生成的(不像 child 的
    chunk_raw_text 是教材原文),所以"vision 看图 → 产 medical statement → enrichment
    看 statement → 产 title/summary/questions" 两步会让 Stage 1 视觉幻觉被 Stage 2
    固化到 4 字段全错。改成单步:vision/text LLM 一次性产出 4 字段,LLM 全程持有
    原始信息源(vision 持有图,text LLM 持有 html),避免错误扩散。

    2026-05-12 单行多列重构:medical_statement 不再作为独立 chunk 的 chunk_raw_text,
    而是作为图表行的独立列(同行 chunk_raw_text 装 caption+html / caption+footnote,
    见 §3.1.2)。本 schema 字段语义不变,只是落 PG 时映射到 chunks.medical_statement 列。
    """

    medical_statement: str = Field(
        ...,
        description="100-300 字医学陈述体描述,作为图表行的 medical_statement 列入库;直接陈述源数据中的医学事实(诊断标准/检查指标/治疗流程等),严禁'图中显示'/'本表列出'等元语言",
    )
    title: str = Field(..., description="≤30 字精准小标题(本图/表的内容标签,不是位置标签);见 ChunkEnrichmentOutput.title")
    summary: str = Field(..., description="≤250 字,与 medical_statement 错位互补的语义抽象,显式化 heading_path 中的病名/主题")
    hypothetical_questions: list[str] = Field(
        default_factory=list,
        description="3 条,临床表述与患者口语混合,用户可能针对本图/表提的问题",
    )
