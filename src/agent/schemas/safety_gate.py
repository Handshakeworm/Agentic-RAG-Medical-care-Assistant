"""Agent ⑪ safety_gate LLM 兜底层输出 schema(DEV_SPEC §9.5 第 9 项)。

LLM 兜底层处理规则层覆盖不到的情况(交叉过敏、罕见相互作用、肝肾剂量调整等)。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SafetyRisk(BaseModel):
    """单项 LLM 识别的安全风险。"""

    risk_type: Literal["cross_allergy", "interaction", "dosage_adjustment"] = Field(
        ..., description="风险类型"
    )
    description: str = Field(..., description="风险描述")
    severity: Literal["high", "medium", "low"] = Field(..., description="严重程度")
    recommendation: str = Field(..., description="处置建议")


class SafetyGateOutput(BaseModel):
    """⑪ safety_gate LLM 兜底层输出。"""

    additional_risks: list[SafetyRisk] = Field(
        default_factory=list,
        description="LLM 识别的规则层未覆盖的额外风险(交叉过敏、罕见相互作用等)",
    )
