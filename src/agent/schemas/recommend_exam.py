"""Agent ⑧a recommend_exam LLM 输出 schema(DEV_SPEC §9.5)。

⑧a 把"3-5 项检查推荐"结构化为 list[str](每项一个检查名),
而不是整段 free text 塞进 recommended_tests 单元素 — 否则
state.recommended_tests 字段(spec §4.1.1 定义为 list[str])语义破坏,
下游 ⑫ generate_advice / ⑬ format_response 不能按项遍历。

`rationale` 是 LLM 整体说明(为什么推荐这些 / 已有哪些可复用),作为审计 + prompt
透传给 ⑫,但不进 state.recommended_tests(防止单元素塞文本的老毛病复现)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class RecommendExamOutput(BaseModel):
    """⑧a recommend_exam LLM 结构化输出。"""

    tests: list[str] = Field(
        default_factory=list,
        description=(
            "建议的检查项目清单(每项一个检查名,如'血常规'、'腹部 CT'、'胆囊超声');"
            "spec §4.1.2 ⑧ 期望 3-5 项;若所有所需检查患者都已上传报告则可为空"
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "整体推荐理由(为什么这几项 / 已有哪些可复用 / 哪个对鉴别最关键);"
            "面向患者的简短说明,不是逐项理由,2-3 句即可"
        ),
    )
