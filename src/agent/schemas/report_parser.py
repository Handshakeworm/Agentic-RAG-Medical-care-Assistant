"""Agent ①.5 / ⑨ report parser LLM 输出 schema(DEV_SPEC §9.5 第 2 项)。

多模态 LLM 直读图片/PDF 报告 → 结构化关键发现。

注:`ReportFinding` **不含** `report_index` 字段——下标由节点代码在写入 State
`report_findings` 时根据 `exam_reports` 下标自动填充,不让 LLM 输出(spec §9.5 注)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ReportFinding(BaseModel):
    """单份报告的结构化关键发现(LLM 直读多模态报告产出)。"""

    report_type: str = Field(
        ...,
        description="报告类型:blood_routine / urine_routine / biochemistry / imaging / ecg / physical_exam / pathology / other",
    )
    report_date: str | None = Field(
        None, description="报告日期(YYYY-MM-DD),无法识别则为 None"
    )
    abnormal_values: list[str] = Field(
        default_factory=list,
        description="异常检验值,保留原始数值,如'WBC 12.3×10⁹/L↑'",
    )
    impressions: list[str] = Field(
        default_factory=list, description="诊断印象,如'右肺上叶磨玻璃结节'"
    )
    positive_findings: list[str] = Field(
        default_factory=list,
        description="阳性发现(含异常值的临床解读,使用医学文献语言)",
    )
    negative_findings: list[str] = Field(
        default_factory=list,
        description="阴性发现 / 已排除项,如'未见肝内胆管扩张'",
    )


class ReportFindings(BaseModel):
    """①.5 / ⑨ 报告解析 LLM 输出。"""

    findings: list[ReportFinding] = Field(
        default_factory=list, description="各份报告的结构化发现列表"
    )
