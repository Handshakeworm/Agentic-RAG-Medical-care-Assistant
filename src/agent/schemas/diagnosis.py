"""Agent ⑩ diagnose 三步 LLM 输出 schema(DEV_SPEC §9.5 第 8 项)。

三步分阶段推理(spec §4.1.2 ⑩):
- Step 1 → `EvidenceSheet`(证据归集,事实级别,不做概率判断)
- Step 2 → `DiagnosisRanking`(鉴别诊断排序,核心临床推理)
- Step 3 → `DiagnosisOutput`(置信度校准,自检纠偏后的最终结果)

`RankedDisease.failure_reason` 字段由**节点代码**在兜底路径中填充
(spec §4.1.2 ⑩ "结构化输出保障"段),**不由 LLM 输出**——schema 给 None 默认值,
LLM 正常路径下产出 `failure_reason=None`,异常兜底时由 except 块构造。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ────────────────────────────────────────────────────────────────────────────
# Step 1: EvidenceSheet — 证据归集
# ────────────────────────────────────────────────────────────────────────────


class HistoryFactor(BaseModel):
    """单项病史因素及其对候选疾病概率的影响方向。"""

    item: str = Field(..., description="病史项目,如'高血压病史'")
    direction: Literal["increase", "decrease", "neutral"] = Field(
        ..., description="对候选疾病概率的影响:升高/降低/中性"
    )


class SlotRelevance(BaseModel):
    """单个现病史维度槽位与候选疾病的相关性。"""

    slot: str = Field(..., description="槽位名,如'location'")
    value: str = Field(..., description="槽位值,如'右下腹'")
    impact: str = Field(..., description="对候选疾病的诊断意义,如'右下腹痛支持阑尾炎'")


class ReportEvidence(BaseModel):
    """单条报告发现作为诊断证据的角色。"""

    finding: str = Field(..., description="报告中的具体发现,如'WBC 12.3×10⁹/L↑'")
    role: Literal["quantitative_support", "qualitative_support", "exclusion"] = Field(
        ..., description="证据角色:定量支持/定性支持/排除"
    )


class CandidateEvidence(BaseModel):
    """单个候选疾病的证据归集。"""

    disease: str = Field(..., description="候选疾病名")
    supporting: list[str] = Field(default_factory=list, description="支持证据(症状匹配)")
    opposing: list[str] = Field(
        default_factory=list, description="反对证据(否认症状/阴性发现)"
    )
    history_factors: list[HistoryFactor] = Field(
        default_factory=list, description="病史因素列表"
    )
    slot_relevance: list[SlotRelevance] = Field(
        default_factory=list, description="现病史维度槽位相关性列表"
    )
    report_evidence: list[ReportEvidence] = Field(
        default_factory=list, description="报告证据列表"
    )


class EvidenceSheet(BaseModel):
    """⑩ diagnose Step 1 输出 — 结构化证据表。"""

    candidates: list[CandidateEvidence] = Field(
        ..., min_length=1, description="候选疾病证据列表"
    )


# ────────────────────────────────────────────────────────────────────────────
# Step 2 / 3: DiagnosisRanking & DiagnosisOutput
# ────────────────────────────────────────────────────────────────────────────


class RankedDisease(BaseModel):
    """单个候选疾病的排序结果。

    `failure_reason` 由节点代码在兜底路径中填充(spec §4.1.2 ⑩),不由 LLM 输出。
    LLM 在正常路径下产出 `failure_reason=None`,异常兜底时由 except 块构造。
    """

    disease: str = Field(..., description="疾病名;兜底场景固定为 '信息不足以支持可靠诊断'")
    probability: float = Field(..., ge=0.0, le=1.0, description="概率;兜底场景为 0.0")
    evidence_chain: list[str] = Field(default_factory=list, description="关键推理链")
    differentiation_type: Literal["confirmed", "need_exam", "insufficient"] = Field(
        ..., description="鉴别状态"
    )
    unaskable_impact: str | None = Field(None, description="不可问体征的条件推理说明")
    failure_reason: str | None = Field(
        None,
        description=(
            "系统级失败原因(非自然 insufficient)。取值示例:"
            "'followup_round_capped'(追问触顶)、"
            "'step_1_structured_output_failed: ValidationError: ...'、"
            "'step_2_structured_output_failed: ...'、"
            "'step_3_structured_output_failed: ...'。"
            "None 表示 LLM 正常推理。该字段由节点代码在兜底路径中填充,不由 LLM 输出。"
        ),
    )


class DiagnosisRanking(BaseModel):
    """⑩ diagnose Step 2 输出 — 鉴别诊断排序。"""

    ranked: list[RankedDisease] = Field(
        ..., min_length=1, description="按概率降序排列的候选疾病"
    )


class DiagnosisOutput(BaseModel):
    """⑩ diagnose Step 3 最终输出 — 校准后的诊断结果。"""

    results: list[RankedDisease] = Field(
        ..., min_length=1, description="校准后的诊断结果列表"
    )
