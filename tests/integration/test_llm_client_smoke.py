"""tests/integration/test_llm_client_smoke.py — 真调 DashScope 验证 LLM 链路。

会**真实花钱 + 联网**,所以 skipif 缺 LLM_API_KEY 或显式 placeholder。
不在 e2e 阶段跑也无妨,本测试保证"有 key 时跑一次,确认 client 能真出回答"。

DEV_SPEC §8.3 B8 验收:对接 DashScope OpenAI-compatible API。
"""

from __future__ import annotations

import os

import pytest

from config.settings import settings

# skipif:走 pydantic-settings 读 .env(os.getenv 不会自动加载 .env)
_api_key = settings.llm.API_KEY
pytestmark = pytest.mark.skipif(
    not _api_key or _api_key.startswith("sk-xxx") or os.getenv("SKIP_LLM_LIVE_TEST") == "1",
    reason="LLM_API_KEY 未配置真实值或 SKIP_LLM_LIVE_TEST=1",
)


def test_get_llm_invoke_returns_real_response() -> None:
    """非流式调用:能拿到 AIMessage,content 非空。"""
    from langchain_core.messages import AIMessage

    from src.models.llm_client import get_llm

    llm = get_llm(timeout_seconds=20)
    response = llm.invoke("用一句话回答:发烧的英文是?")

    assert isinstance(response, AIMessage)
    assert response.content
    print(f"\n  响应: {response.content[:150]}")


def test_get_llm_stream_yields_chunks() -> None:
    """流式调用:能迭代出多个 chunk,合并后内容非空(spec B8 验收要求支持流式)。"""
    from src.models.llm_client import get_llm

    llm = get_llm(timeout_seconds=20)
    chunks = list(llm.stream("用一句话回答:咳嗽的英文是?"))

    assert chunks, "stream 没产出任何 chunk"
    full_text = "".join(c.content for c in chunks if hasattr(c, "content"))
    assert full_text.strip()
    print(f"\n  chunks={len(chunks)}, 合并: {full_text[:150]}")
