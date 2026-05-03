"""tests/unit/test_llm_client.py — 锁住 LLM client 工厂行为(DEV_SPEC §8.3 B8)。

不真调外部 API:这层只验证"工厂能正确构造 ChatOpenAI 并把 settings 传进去"。
真实连通性走 tests/integration/test_llm_client_smoke.py。
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI


def test_get_llm_returns_chat_openai_instance() -> None:
    """get_llm() 必须返回 ChatOpenAI 实例 — 业务节点 with_structured_output / with_retry 等都依赖此类型。"""
    from src.models.llm_client import get_llm

    llm = get_llm()
    assert isinstance(llm, ChatOpenAI)


def test_get_llm_picks_up_settings_defaults() -> None:
    """无参调用时,model/base_url/api_key 必须取自 settings.llm。"""
    from config.settings import settings
    from src.models.llm_client import get_llm

    llm = get_llm()
    # ChatOpenAI 把 model 存为 model_name 属性
    assert llm.model_name == settings.llm.MODEL_NAME
    # api_key 在 LangChain 1.x 里是 SecretStr,取值用 .get_secret_value()
    assert llm.openai_api_key.get_secret_value() == settings.llm.API_KEY


def test_get_llm_caches_same_args() -> None:
    """同参数两次 get_llm() 必须返回同一实例(lru_cache)。"""
    from src.models.llm_client import get_llm

    llm1 = get_llm()
    llm2 = get_llm()
    assert llm1 is llm2


def test_get_llm_param_override_creates_new_instance() -> None:
    """显式传不同参数应得到新实例,避免节点之间互相污染。"""
    from src.models.llm_client import get_llm

    base = get_llm()
    different_temp = get_llm(temperature=0.9)
    assert base is not different_temp
    assert different_temp.temperature == 0.9


def test_get_llm_required_methods_for_spec_9_1_template() -> None:
    """DEV_SPEC §9.1 LLM 调用模板要求的 4 个方法必须可用。"""
    from src.models.llm_client import get_llm

    llm = get_llm()
    assert hasattr(llm, "with_structured_output")
    assert hasattr(llm, "with_retry")
    assert hasattr(llm, "invoke")
    assert hasattr(llm, "stream")
