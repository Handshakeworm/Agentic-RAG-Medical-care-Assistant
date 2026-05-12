"""Agent ⑫ generate_advice LLM 输出 schema(DEV_SPEC §9.5 第 10 项)。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class MedicationAdvice(BaseModel):
    """单项用药建议。"""

    drug_name: str = Field(..., description="药品名称(通用名)")
    dosage: str = Field(..., description="剂量,如'0.1g'")
    frequency: str = Field(..., description="用药频次,如'每日3次'")
    duration: str = Field(..., description="疗程,如'7天'")
    notes: str | None = Field(None, description="特殊注意事项(饭后服用、肝肾功能调整等)")


class AdviceOutput(BaseModel):
    """⑫ generate_advice LLM 输出。"""

    medications: list[MedicationAdvice] = Field(
        default_factory=list, description="用药建议列表(在 safety_constraints 约束内)"
    )
    exam_suggestions: list[str] = Field(
        default_factory=list, description="建议检查项目"
    )
    risk_warnings: list[str] = Field(
        default_factory=list, description="风险提示与注意事项"
    )
    urgent_flag: bool = Field(
        False,
        description="是否高危情况(疑似心梗、脑卒中等),True 时强烈建议立即就医",
    )
