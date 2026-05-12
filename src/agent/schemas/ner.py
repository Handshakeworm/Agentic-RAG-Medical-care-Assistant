"""Agent ② build_query Step 1 NER LLM 输出 schema(DEV_SPEC §9.5 第 3 项)。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NEREntity(BaseModel):
    """单个医学命名实体。"""

    text: str = Field(..., description="实体原文")
    entity_type: Literal["symptom", "disease", "drug", "anatomy"] = Field(
        ..., description="实体类型"
    )
    negation: bool = Field(False, description="是否为否定表述,如'不头痛'")
    temporality: Literal["current", "past", "family"] = Field(
        "current", description="时间属性:当前/既往/家族"
    )
    value: str | None = Field(None, description="量化值(如体温 38.5°C),无则 None")


class NERResult(BaseModel):
    """② build_query Step 1 NER LLM 输出。"""

    entities: list[NEREntity] = Field(
        default_factory=list, description="识别到的医学实体列表"
    )
