"""tests/integration/test_llm_structured_smoke.py — 真调 DeepSeek 验证
`with_structured_output(Schema, method="json_mode")` 能跑通。

为什么要单独验证:
- enrichment.py / table_enrichment_generation.py 都走 method="json_mode",
  22287 child + 1023 figure + 2744 table 全跑过,这是已验证的稳定路径
- F 阶段 13 处节点 LLM 调用全部对齐到 json_mode,这个 smoke 用最小 schema
  锁住"DeepSeek + json_mode + LangChain with_structured_output 能产出合规
  Pydantic 实例",防止后续 LangChain / DeepSeek 变更悄悄打破

跑法:有真 LLM_API_KEY 时自动跑;放在 integration 不在 unit。
"""
from __future__ import annotations

import os

import pytest
from pydantic import BaseModel, Field

from config.settings import settings


_api_key = settings.llm.API_KEY
pytestmark = pytest.mark.skipif(
    not _api_key or _api_key.startswith("sk-xxx") or os.getenv("SKIP_LLM_LIVE_TEST") == "1",
    reason="LLM_API_KEY 未配置真实值或 SKIP_LLM_LIVE_TEST=1",
)


class _SimpleDiagnosis(BaseModel):
    """最小 schema,锁 DeepSeek function_calling 能跑通即可。"""

    disease: str = Field(..., description="疾病名")
    probability: float = Field(..., ge=0.0, le=1.0, description="概率 0-1")
    evidence: list[str] = Field(default_factory=list, description="证据列表")


def test_structured_output_json_mode_works() -> None:
    """DeepSeek + LangChain `with_structured_output(method="json_mode")` 必须跑通。

    若失败:F 阶段 13 处 LLM 调用点的 schema 解析全部会出问题,优先排查
    LangChain ChatOpenAI 版本兼容性 / DeepSeek API 变更。
    """
    from src.models.llm_client import get_llm

    llm = get_llm(timeout_seconds=30)
    chain = llm.with_structured_output(
        _SimpleDiagnosis, method="json_mode"
    ).with_retry(stop_after_attempt=2)

    result = chain.invoke(
        "患者主诉:腹痛 3 天,进食后加重,伴反酸。"
        "请用 JSON 输出最可能的诊断:disease(疾病名) + probability(概率 0-1)"
        " + evidence(证据列表)。"
    )

    assert isinstance(result, _SimpleDiagnosis)
    assert result.disease
    assert 0.0 <= result.probability <= 1.0
    print(f"\n  diagnosis={result.disease} p={result.probability:.2f} evid={result.evidence[:2]}")
