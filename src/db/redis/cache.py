"""src/db/redis/cache.py — Redis 缓存客户端(DEV_SPEC §5.1 + §8.4 H1)。

**仅实现配置缓存**:
- Key 命名空间 `config:<key_name>`,TTL 60s(spec §5.1 表)
- Cache-Aside 模式:命中直接返回;未命中调用 loader 回源(PG `system_config` 表)
  → 写回 Redis 并设 TTL → 返回值
- Redis 不可用时降级:**捕获所有 RedisError 并直接 `loader()`**,
  对调用方完全透明,只写 WARNING 日志(不持续报警,与 §5.2.2 告警表
  "Redis 连接失败 连续 3 次健康检查失败 → Warning" 对齐)

**严禁**(对齐 spec §9.1 风格 + §5.1):
- 不实现 RAG 响应级缓存 — 见 §5.1 "为何不做 RAG 响应级缓存"(Agentic State 下
  query_text 做 key 会跨患者/跨追问轮串话,正确性 bug)
- 不写装饰器 / 上下文管理器 / Cache 类。函数直接拿 settings.redis.URL,业务侧
  `from src.db.redis.cache import get_config_cached` 用即可

Redis client 单例:`get_redis_client()` 用 `lru_cache(1)`,失败时返回 None
(降级模式),后续每次调用都会尝试一次 ping(连接池里有连接就 ping 通)。
"""
from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from typing import Any, Callable

import redis
from redis.exceptions import RedisError

from config.settings import settings
from src.common.metrics import redis_command_latency_seconds


_logger = logging.getLogger(__name__)

_CONFIG_NAMESPACE = "config:"


@lru_cache(maxsize=1)
def get_redis_client() -> redis.Redis | None:
    """模块级 Redis client(连接池单例)。Redis 不可用返回 None,降级模式。

    `decode_responses=True` 让 GET 返回 str(后续 json.loads),不需要业务侧 .decode()。
    `socket_timeout=2` / `socket_connect_timeout=2`:Redis 是热路径但不该拖慢请求,
    超时直接走降级回源 PG。
    """
    try:
        client = redis.Redis.from_url(
            settings.redis.URL,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
            max_connections=10,  # spec §5.1 连接池表
        )
        client.ping()
        return client
    except RedisError as e:
        _logger.warning(
            "Redis 不可用,进入降级模式(配置读取直接回源 PG):%s", e
        )
        return None


def is_redis_available() -> bool:
    """供 /readyz 或启动 banner 用 — 探测一次 ping。

    注:spec §5.2.4 明确 Redis 不可用仍算 ready(降级模式),所以
    本函数是观察用,不是 readiness 判定。
    """
    client = get_redis_client()
    if client is None:
        return False
    try:
        client.ping()
        return True
    except RedisError:
        return False


def get_config_cached(
    key_name: str,
    loader: Callable[[], Any],
    *,
    ttl_seconds: int | None = None,
) -> Any:
    """读 Cache-Aside 配置项。

    `key_name`:`system_config.key_name`(如 `llm_temperature`),不带前缀
    `loader`:命中失败的回源 callable,通常封装 PG `SELECT value FROM system_config WHERE key_name=:k`
    `ttl_seconds`:TTL 覆写,默认走 `settings.redis.CONFIG_CACHE_TTL`(60s)

    Redis 不可用 / 单次操作失败 → 直接 loader(),WARNING 日志,不抛
    (对调用方完全透明)。

    返回值:JSON 反序列化后的 Python 对象(int/float/str/bool/dict/list)。
    `loader()` 返回 None 时**不写缓存**(避免缓存 NotFound 让管理员补配置后等 60s)。
    """
    ttl = ttl_seconds if ttl_seconds is not None else settings.redis.CONFIG_CACHE_TTL
    redis_key = _CONFIG_NAMESPACE + key_name
    client = get_redis_client()

    if client is not None:
        t0 = time.perf_counter()
        try:
            cached = client.get(redis_key)
            redis_command_latency_seconds.labels(command="GET").observe(time.perf_counter() - t0)
            if cached is not None:
                return json.loads(cached)
        except RedisError as e:
            redis_command_latency_seconds.labels(command="GET").observe(time.perf_counter() - t0)
            _logger.warning("Redis GET %s 失败,回源 PG:%s", redis_key, e)
        except json.JSONDecodeError as e:
            _logger.warning("Redis %s JSON 损坏,删 key 回源:%s", redis_key, e)
            try:
                client.delete(redis_key)
            except RedisError:
                pass

    value = loader()

    if value is not None and client is not None:
        t1 = time.perf_counter()
        try:
            client.setex(redis_key, ttl, json.dumps(value, ensure_ascii=False))
            redis_command_latency_seconds.labels(command="SETEX").observe(time.perf_counter() - t1)
        except (RedisError, TypeError) as e:
            redis_command_latency_seconds.labels(command="SETEX").observe(time.perf_counter() - t1)
            _logger.warning("Redis SETEX %s 失败:%s", redis_key, e)

    return value


def invalidate_config(key_name: str) -> None:
    """显式删 cache(admin 改完 system_config 后调,触发下次回源)。

    Redis 不可用直接静默 — 60s TTL 兜底,管理员最多等一个 TTL 全部节点新值生效。
    """
    client = get_redis_client()
    if client is None:
        return
    redis_key = _CONFIG_NAMESPACE + key_name
    t0 = time.perf_counter()
    try:
        client.delete(redis_key)
        redis_command_latency_seconds.labels(command="DEL").observe(time.perf_counter() - t0)
    except RedisError as e:
        redis_command_latency_seconds.labels(command="DEL").observe(time.perf_counter() - t0)
        _logger.warning("Redis DEL %s 失败,等 TTL 兜底:%s", redis_key, e)


def reset_redis_client_for_test() -> None:
    """**测试专用**:清掉 lru_cache 让下次 get_redis_client() 重新构造。

    生产代码切勿调用;改 settings.redis.URL 也不会触发(lru_cache key 是空)。
    """
    get_redis_client.cache_clear()
