"""Agent ② build_query Step 2 Entity Linking 返回结构(DEV_SPEC §9.5 第 4 项)。

Step 2 是**纯确定性三层归一化**(Tier 1 精确别名 / Tier 2 向量阈值 /
Tier 3 占位),**不调 LLM** —— 与 ④ extract_symptoms 同一套实现,阈值来源
§9.7 `ENTITY_LINKING_TIER2_THRESHOLD`(评测调优微调)。

`EntityLinkingMatch` 因此不是 LLM 结构化输出 schema,而是 build_query Step 2
工具函数 `_link_one_entity` 的返回结构(用 Pydantic 是为了下游字段访问类型安全)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class EntityLinkingMatch(BaseModel):
    """单个实体的术语链接结果。"""

    original_text: str = Field(..., description="NER 原文")
    concept_id: str | None = Field(
        None, description="标准术语库 concept ID(ICD-10 / 自建术语表),未匹配则 None"
    )
    preferred_term: str | None = Field(
        None, description="标准首选术语,未匹配则 None(保留原文参与后续流程)"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="匹配置信度:Tier 1 命中 = 1.0,Tier 2 = cosine 分,Tier 3 = 0.0",
    )
