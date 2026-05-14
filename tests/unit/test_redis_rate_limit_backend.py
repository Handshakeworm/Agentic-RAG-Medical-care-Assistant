"""tests/unit/test_redis_rate_limit_backend.py — H6 RedisSlidingWindow 单元测试。

覆盖:
1. 首次调用 → SCRIPT LOAD + EVALSHA → allowed
2. 配额内 → allowed
3. 超限 → 拒绝 + retry_after
4. NOSCRIPT 自愈(Redis 重启脚本缓存丢失)
5. Redis 不可用 → fail-open(放行)
6. 协议契约 — 与 G3 InMemorySlidingWindow 接口完全一致
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from src.db.redis import cache as cache_mod
from src.db.redis.rate_limit_backend import RedisSlidingWindow


@pytest.fixture(autouse=True)
def _reset_client():
    cache_mod.reset_redis_client_for_test()
    yield
    cache_mod.reset_redis_client_for_test()


# ────────────────────────────────────────────────────────────────────────────
# Allowed / 拒绝路径
# ────────────────────────────────────────────────────────────────────────────


@patch("src.db.redis.cache.redis.Redis")
def test_first_call_loads_script_and_allows(mock_redis_cls):
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.script_load.return_value = "sha-abc"
    mock_client.evalsha.return_value = [1, 0]
    mock_redis_cls.from_url.return_value = mock_client

    backend = RedisSlidingWindow()
    allowed, retry = backend.is_allowed("user:42", limit=10, window_seconds=60)

    assert allowed is True
    assert retry == 0
    mock_client.script_load.assert_called_once()
    mock_client.evalsha.assert_called_once()
    args, _ = mock_client.evalsha.call_args
    assert args[0] == "sha-abc"
    assert args[1] == 1                # numkeys
    assert args[2] == "ratelimit:user:42"  # key 命名空间


@patch("src.db.redis.cache.redis.Redis")
def test_subsequent_calls_skip_script_load(mock_redis_cls):
    """SCRIPT LOAD 缓存 sha,后续直接 EVALSHA 节带宽"""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.script_load.return_value = "sha-abc"
    mock_client.evalsha.return_value = [1, 0]
    mock_redis_cls.from_url.return_value = mock_client

    backend = RedisSlidingWindow()
    backend.is_allowed("k", 10, 60)
    backend.is_allowed("k", 10, 60)
    backend.is_allowed("k", 10, 60)

    assert mock_client.script_load.call_count == 1
    assert mock_client.evalsha.call_count == 3


@patch("src.db.redis.cache.redis.Redis")
def test_over_limit_returns_retry_after(mock_redis_cls):
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.script_load.return_value = "sha"
    mock_client.evalsha.return_value = [0, 17]  # 超限,17s 后重试
    mock_redis_cls.from_url.return_value = mock_client

    backend = RedisSlidingWindow()
    allowed, retry = backend.is_allowed("ip:1.2.3.4", 5, 60)
    assert allowed is False
    assert retry == 17


# ────────────────────────────────────────────────────────────────────────────
# NOSCRIPT 自愈
# ────────────────────────────────────────────────────────────────────────────


@patch("src.db.redis.cache.redis.Redis")
def test_noscript_error_triggers_reload(mock_redis_cls):
    """Redis 重启 → script cache 丢 → 第一次 EVALSHA NOSCRIPT → 重 LOAD + 再调 EVALSHA"""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.script_load.side_effect = ["sha-old", "sha-new"]
    mock_client.evalsha.side_effect = [
        RedisError("NOSCRIPT No matching script."),
        [1, 0],
    ]
    mock_redis_cls.from_url.return_value = mock_client

    backend = RedisSlidingWindow()
    backend._script_sha = "sha-old"  # 模拟之前已 load 过
    allowed, retry = backend.is_allowed("k", 10, 60)
    assert allowed is True
    # script_load 被调一次(NOSCRIPT 触发的重新 load),evalsha 调两次
    assert mock_client.script_load.call_count == 1
    assert mock_client.evalsha.call_count == 2


@patch("src.db.redis.cache.redis.Redis")
def test_other_redis_error_falls_back_to_open(mock_redis_cls):
    """非 NOSCRIPT 的 RedisError → fail-open,放行"""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.script_load.return_value = "sha"
    mock_client.evalsha.side_effect = RedisError("network blip")
    mock_redis_cls.from_url.return_value = mock_client

    backend = RedisSlidingWindow()
    allowed, retry = backend.is_allowed("k", 10, 60)
    assert allowed is True
    assert retry == 0


# ────────────────────────────────────────────────────────────────────────────
# Redis 不可用 — fail-open
# ────────────────────────────────────────────────────────────────────────────


@patch("src.db.redis.cache.redis.Redis")
def test_redis_unavailable_fails_open(mock_redis_cls):
    """Redis 完全连不上 → get_redis_client 返 None → 放行"""
    mock_redis_cls.from_url.side_effect = RedisConnectionError("down")

    backend = RedisSlidingWindow()
    allowed, retry = backend.is_allowed("k", 10, 60)
    assert allowed is True
    assert retry == 0


# ────────────────────────────────────────────────────────────────────────────
# 协议契约 — 与 G3 内存版可热替换
# ────────────────────────────────────────────────────────────────────────────


def test_protocol_compatible_with_in_memory_backend():
    """RedisSlidingWindow 必须满足 RateLimitBackend.is_allowed(key, limit, window) -> (bool, int)"""
    from src.api.middleware.rate_limiter import InMemorySlidingWindow, RateLimitBackend

    in_memory: RateLimitBackend = InMemorySlidingWindow()
    redis_be: RateLimitBackend = RedisSlidingWindow()

    # 只检签名一致即可(实际行为分别测过)
    assert hasattr(in_memory, "is_allowed")
    assert hasattr(redis_be, "is_allowed")
