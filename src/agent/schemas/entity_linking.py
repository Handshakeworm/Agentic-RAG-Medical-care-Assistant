"""Agent ② build_query Step 2 Entity Linking LLM 输出 schema(DEV_SPEC §9.5 第 4 项)。

LLM 从 terms_collection Top-5 候选中选最匹配项,或判定"无匹配"。
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
    confidence: float = Field(..., ge=0.0, le=1.0, description="匹配置信度")


class EntityLinkingResult(BaseModel):
    """② build_query Step 2 Entity Linking LLM 输出。"""

    matches: list[EntityLinkingMatch] = Field(
        default_factory=list, description="各实体的链接结果"
    )
