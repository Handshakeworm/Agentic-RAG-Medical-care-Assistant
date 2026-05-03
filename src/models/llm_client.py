"""src/models/llm_client.py — LLM 推理客户端工厂(DEV_SPEC §8.3 B8)。

只提供配置好的 ChatOpenAI 实例,**不封装** retry / metrics / structured output。
那些都按 DEV_SPEC §9.1 在每个调用站点自己写裸代码 try/except/finally(详见 CLAUDE.md
"LLM call contract" 段)。

业务节点用法(典型):

    from src.models.llm_client import get_llm
    from src.agent.schemas.ner import NERResult
    from src.common.metrics import _attempts, _failures, _latency, retry_observer

    llm = get_llm()
    chain = llm.with_structured_output(NERResult).with_retry(stop_after_attempt=3)
    # ... 按 §9.1 模板写 try/except/finally + 6 个指标埋点
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from config.settings import settings


@lru_cache(maxsize=8)
def get_llm(
    *,
    model: str | None = None,
    temperature: float = 0.1,
    timeout_seconds: int = 30,
) -> ChatOpenAI:
    """返回 ChatOpenAI 实例,同参数自动复用(lru_cache)。

    默认从 settings.llm 读 BASE_URL / API_KEY / MODEL_NAME。
    各节点若需不同模型/温度,显式传参覆盖(如 enrichment 用更小模型 + 高温)。
    """
    return ChatOpenAI(
        model=model or settings.llm.MODEL_NAME,
        base_url=settings.llm.BASE_URL,
        api_key=settings.llm.API_KEY,
        temperature=temperature,
        timeout=timeout_seconds,
    )
