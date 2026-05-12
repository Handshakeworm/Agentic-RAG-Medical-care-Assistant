"""Agent ① info_collect Step 1 LLM 输出 schema(DEV_SPEC §9.5 第 1 项)。

`InfoCollectOutput` 是 ① info_collect 节点 Step 1 的 LLM 结构化输出契约,
被 `src/agent/nodes/info_collect.py` 通过 `llm.with_structured_output()` 调用。

字段定义直接来源于 §9.5 第 1 项,**禁止私改**——spec authority hierarchy 规定
§9.5 是权威,业务代码必须对齐。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class PresentIllnessSlots(BaseModel):
    """现病史结构化要素槽位(13 个维度),未提及的维度为 None/空列表。

    与 src/agent/state.py 的 PresentIllnessSlots 字段一一对应——同一份契约,
    schema 这边给 LLM 看(用于 with_structured_output),state 那边给运行时存。
    """

    onset_time:          str | None = Field(None, description="起病时间,如'3天前'")
    onset_mode:          str | None = Field(None, description="起病方式:急性/缓慢/隐匿")
    trigger:             str | None = Field(None, description="诱因:劳累/受凉/进食/无明显诱因")
    location:            str | None = Field(None, description="部位")
    nature:              str | None = Field(None, description="性质:刺痛/胀痛/绞痛/烧灼感")
    severity:            str | None = Field(None, description="程度:轻/中/重/VAS评分")
    duration_pattern:    str | None = Field(None, description="时间规律:持续性/间歇性/阵发性")
    aggravating:         list[str] = Field(default_factory=list, description="加重因素")
    relieving:           list[str] = Field(default_factory=list, description="缓解因素")
    associated_symptoms: list[str] = Field(default_factory=list, description="伴随症状(患者自述)")
    progression:         str | None = Field(None, description="病程演变:加重/减轻/稳定/波动")
    treatment_tried:     str | None = Field(None, description="诊疗经过:看过没、用过什么药")
    treatment_response:  str | None = Field(None, description="治疗反应:有效/无效/加重")


class InfoCollectOutput(BaseModel):
    """① info_collect Step 1 LLM 输出。"""

    chief_complaint:       str = Field(..., description="主诉(主要症状+持续时间),如'腹痛3天'")
    present_illness:       str = Field(..., description="现病史自由文本(本次发病的详细展开)")
    present_illness_slots: PresentIllnessSlots = Field(
        ..., description="现病史结构化槽位,与 present_illness 同步填充"
    )
