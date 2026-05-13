"""限流中间件(DEV_SPEC §8.4 G3)— 内存滑动窗口,H6 切 Redis 时只换 backend。

设计:
- `RateLimitBackend` Protocol 抽象配额存储,默认 `InMemorySlidingWindow`
  实现进程内滑动窗口;H6 任务追加一个 `RedisSlidingWindow` 走 Redis ZSET +
  时间戳,接口完全一致,middleware 不动
- 滑动窗口算法:每个 key 维护一个 deque[timestamp],请求来时 popleft 过期戳 +
  append now,len(deque) > limit 就拒(并算 retry_after)。比固定窗口公平,
  整点不会突刺出 2 倍配额
- key 选择:有效 JWT → `user:<sub>`,无 token / 无效 token / 不带 → `ip:<addr>`
  (无效 token 不直接拒,只 fallback 到 IP,避免攻击者用伪造 token 绕过 IP 限流)
- 超限响应:429 + `Retry-After` header(HTTP 7231 标准)+ JSON body 含
  retry_after_seconds / limit / window_seconds 三个字段供前端做 UI 提示

**不在本任务范围**:
- Redis 后端 → H6
- 多实例配额共享 → H6(单进程进程内有效就行,§G3 验收明确)
- 按角色 / 按端点的细粒度配额 → 后续,目前全局统一 settings.api.RATE_LIMIT_PER_MINUTE
- token bucket / leaky bucket 算法 → spec 钦定滑动窗口
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Iterable, Protocol

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.api.middleware.auth_middleware import decode_access_token


# ────────────────────────────────────────────────────────────────────────────
# Backend Protocol — H6 接 Redis 时实现这个接口即可
# ────────────────────────────────────────────────────────────────────────────


class RateLimitBackend(Protocol):
    """配额存储抽象。

    `is_allowed` 返回 `(allowed, retry_after_seconds)`:
    - `allowed=True`  → 放行(retry_after=0)
    - `allowed=False` → 拒绝(retry_after 表示再过多少秒会有名额)
    """

    def is_allowed(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]: ...


# ────────────────────────────────────────────────────────────────────────────
# 内存滑动窗口实现
# ────────────────────────────────────────────────────────────────────────────


class InMemorySlidingWindow:
    """进程内滑动窗口(线程安全)。

    存储:`{key: deque[float]}`,deque 元素是请求的 `time.monotonic()` 时间戳。
    查询时先清理窗口外的戳,再看剩余长度是否 < limit。

    内存占用:每 key 最多存 `limit` 个 float(8 byte)。默认 30/分钟 → 240 byte/key,
    1 万并发用户 ≈ 2.4 MB,完全可承受。冷 key 会随窗口滑过自动空 deque,但 dict
    entry 不自动清理 — 后续若 key 基数爆炸再加 LRU。
    """

    def __init__(self) -> None:
        self._records: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def is_allowed(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            q = self._records[key]
            # 清理窗口外的(滑动窗口的核心:每次请求都 evict 过期戳)
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                # retry_after = 最老的戳 + 窗口 - 现在,向上取整保证 >= 1s
                retry_after = int(q[0] + window_seconds - now) + 1
                return False, max(retry_after, 1)
            q.append(now)
            return True, 0


# ────────────────────────────────────────────────────────────────────────────
# Middleware
# ────────────────────────────────────────────────────────────────────────────


_DEFAULT_EXCLUDED_PATHS: tuple[str, ...] = (
    "/metrics",   # Prometheus 抓取每 15s 一次,限流会让监控断线
    "/healthz",   # H8 后存在;先列上,接入时无需回头改本文件
    "/readyz",
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """全局限流。每请求一次 backend 查询,超限 429。

    构造参数:
    - `backend`:配额存储,默认 `InMemorySlidingWindow()`
    - `limit`:窗口内最大请求数(默认 settings.api.RATE_LIMIT_PER_MINUTE)
    - `window_seconds`:窗口大小(默认 60)
    - `excluded_paths`:不限流的路径集合(默认 `/metrics` + 两个 H8 端点)
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        backend: RateLimitBackend | None = None,
        limit: int,
        window_seconds: int = 60,
        excluded_paths: Iterable[str] = _DEFAULT_EXCLUDED_PATHS,
    ) -> None:
        super().__init__(app)
        self.backend: RateLimitBackend = backend or InMemorySlidingWindow()
        self.limit = limit
        self.window_seconds = window_seconds
        self.excluded_paths = set(excluded_paths)

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.excluded_paths:
            return await call_next(request)

        key = _key_for_request(request)
        allowed, retry_after = self.backend.is_allowed(
            key, self.limit, self.window_seconds
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "请求过于频繁,请稍后再试",
                    "retry_after_seconds": retry_after,
                    "limit": self.limit,
                    "window_seconds": self.window_seconds,
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)


# ────────────────────────────────────────────────────────────────────────────
# key 提取
# ────────────────────────────────────────────────────────────────────────────


def _key_for_request(request: Request) -> str:
    """有效 JWT → user:<sub>;否则 → ip:<addr>。

    无效 token **不直接拒**,只 fallback 到 IP — 否则攻击者拿一堆伪造 token
    可以绕过 IP 桶。让伪造 token 跟未登录用户共享同一个 IP 配额。
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        try:
            payload = decode_access_token(token)
            return f"user:{payload['sub']}"
        except Exception:
            pass  # 解码失败 / 过期 / 篡改 → fallback 到 IP

    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"
