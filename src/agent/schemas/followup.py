"""Agent ⑦ process_followup_answer LLM 输出 schema(DEV_SPEC §9.5 第 7 项)。

⑦ 同时处理症状级回答(对应 ⑤ 选出的 symptom 类追问)和维度级回填
(对应 ⑤ 选出的 dimension 类追问),还要捕获回答中新提到的症状。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SymptomResponse(BaseModel):
    """单个症状的患者回答解析。"""

    term: str = Field(..., description="症状标准术语(preferred_term)")
    status: Literal["confirmed", "denied", "uncertain", "unanswered"] = Field(
        ..., description="患者对该症状的回答状态"
    )


class FollowupParseResult(BaseModel):
    """⑦ process_followup_answer LLM 输出。"""

    symptom_responses: list[SymptomResponse] = Field(
        default_factory=list, description="各症状的回答解析"
    )
    slot_fills: dict[str, str | list[str]] = Field(
        default_factory=dict,
        description=(
            "维度级回填,key=槽位名;value 类型与 PresentIllnessSlots 槽位一致:"
            "单值槽(onset_time/onset_mode/trigger/location/nature/severity/"
            "duration_pattern/progression/treatment_tried/treatment_response)为 str,"
            "多值槽(aggravating/relieving/associated_symptoms)为 list[str]"
        ),
    )
    new_symptoms: list[str] = Field(
        default_factory=list, description="患者回答中新提及的症状(供下轮 build_query 使用)"
    )
