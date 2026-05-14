"""tests/unit/test_redis_cache.py — H1 Redis 配置缓存单元测试(DEV_SPEC §5.1)。

四个核心场景:
1. cache hit → 不调 loader
2. cache miss → 调 loader + 写回 cache + 设 TTL
3. Redis 不可用 → 直接 loader,异常不抛(降级模式)
4. invalidate_config → 删 key 后下次回源

mock 整个 redis client(不依赖 docker),保证单元测试零依赖。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from src.db.redis import cache as cache_mod


@pytest.fixture(autouse=True)
def _reset_client():
    """每个 case 前重置 lru_cache,避免单例污染。"""
    cache_mod.reset_redis_client_for_test()
    yield
    cache_mod.reset_redis_client_for_test()


# ────────────────────────────────────────────────────────────────────────────
# get_config_cached
# ────────────────────────────────────────────────────────────────────────────


@patch("src.db.redis.cache.redis.Redis")
def test_cache_hit_skips_loader(mock_redis_cls):
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.get.return_value = json.dumps(0.7)
    mock_redis_cls.from_url.return_value = mock_client

    loader = MagicMock(return_value=999)  # 命中应该跳过 loader
    val = cache_mod.get_config_cached("llm_temperature", loader)

    assert val == 0.7
    loader.assert_not_called()
    mock_client.get.assert_called_once_with("config:llm_temperature")
    mock_client.setex.assert_not_called()


@patch("src.db.redis.cache.redis.Redis")
def test_cache_miss_calls_loader_and_writes_back(mock_redis_cls):
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.get.return_value = None  # miss
    mock_redis_cls.from_url.return_value = mock_client

    loader = MagicMock(return_value=0.3)
    val = cache_mod.get_config_cached("llm_temperature", loader, ttl_seconds=60)

    assert val == 0.3
    loader.assert_called_once()
    # 写回带 60s TTL,JSON 序列化(json.dumps 默认无空格)
    mock_client.setex.assert_called_once_with(
        "config:llm_temperature", 60, "0.3"
    )


@patch("src.db.redis.cache.redis.Redis")
def test_cache_miss_loader_returns_none_skips_writeback(mock_redis_cls):
    """loader 返回 None(配置不存在)→ 不污染缓存,等管理员补完再读"""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.get.return_value = None
    mock_redis_cls.from_url.return_value = mock_client

    loader = MagicMock(return_value=None)
    val = cache_mod.get_config_cached("missing_key", loader)

    assert val is None
    loader.assert_called_once()
    mock_client.setex.assert_not_called()


@patch("src.db.redis.cache.redis.Redis")
def test_redis_unavailable_falls_back_to_loader(mock_redis_cls):
    """Redis 不可用 → get_redis_client 返回 None,直接 loader"""
    mock_redis_cls.from_url.side_effect = RedisConnectionError("connection refused")

    loader = MagicMock(return_value={"key": "value"})
    val = cache_mod.get_config_cached("any_key", loader)

    assert val == {"key": "value"}
    loader.assert_called_once()


@patch("src.db.redis.cache.redis.Redis")
def test_redis_get_error_falls_back_silently(mock_redis_cls):
    """ping 通了,但单次 GET 抛 → 静默回源"""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.get.side_effect = RedisConnectionError("network blip")
    mock_redis_cls.from_url.return_value = mock_client

    loader = MagicMock(return_value="fallback_value")
    val = cache_mod.get_config_cached("k", loader)

    assert val == "fallback_value"
    loader.assert_called_once()


@patch("src.db.redis.cache.redis.Redis")
def test_corrupted_cache_value_deletes_and_reloads(mock_redis_cls):
    """Redis 里的值不是合法 JSON(理论不会发生)→ 删 key 走回源"""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.get.return_value = "not-a-json-{"
    mock_redis_cls.from_url.return_value = mock_client

    loader = MagicMock(return_value=42)
    val = cache_mod.get_config_cached("k", loader)

    assert val == 42
    mock_client.delete.assert_called_once_with("config:k")


# ────────────────────────────────────────────────────────────────────────────
# invalidate_config / is_redis_available
# ────────────────────────────────────────────────────────────────────────────


@patch("src.db.redis.cache.redis.Redis")
def test_invalidate_config_deletes_key(mock_redis_cls):
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_redis_cls.from_url.return_value = mock_client

    cache_mod.invalidate_config("llm_temperature")
    mock_client.delete.assert_called_once_with("config:llm_temperature")


@patch("src.db.redis.cache.redis.Redis")
def test_invalidate_config_silent_when_redis_down(mock_redis_cls):
    mock_redis_cls.from_url.side_effect = RedisConnectionError("down")
    # 不应抛,也不应 ping log spam(60s TTL 自然过期兜底)
    cache_mod.invalidate_config("any_key")


@patch("src.db.redis.cache.redis.Redis")
def test_is_redis_available_true_when_ping_succeeds(mock_redis_cls):
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_redis_cls.from_url.return_value = mock_client
    assert cache_mod.is_redis_available() is True


@patch("src.db.redis.cache.redis.Redis")
def test_is_redis_available_false_when_redis_down(mock_redis_cls):
    mock_redis_cls.from_url.side_effect = RedisConnectionError("down")
    assert cache_mod.is_redis_available() is False
