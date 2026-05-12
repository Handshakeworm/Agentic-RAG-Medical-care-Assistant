"""Agent ⑤ select_discriminative_symptom LLM 输出 schema(DEV_SPEC §9.5 第 6 项)。

两个独立 LLM 调用:
- `DimensionSelection` — 从 present_illness_slots 空槽中选 1~2 个最有鉴别价值的维度
- `AskabilityJudgment` — 在贪心选择循环内对单个症状做可问性评估
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class DimensionSelection(BaseModel):
    """⑤ 维度缺口优先 LLM 输出。"""

    selected_slots: list[str] = Field(
        ...,
        min_length=1,
        max_length=2,
        description="从空槽中选出的 1~2 个槽位名(如 'location' / 'nature')",
    )


class AskabilityJudgment(BaseModel):
    """⑤ 可问性评估 LLM 输出(在贪心循环内调用,逐症状评估)。"""

    askable: bool = Field(..., description="该症状是否适合向患者追问(体征类不可问)")
    reason: str = Field(..., description="判断理由")
