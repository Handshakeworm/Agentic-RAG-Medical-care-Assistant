"""tests/integration/conftest.py — integration 测试共享 fixture。

**核心问题**(2026-05-13 G4-G7 回归暴露):
所有 integration 测试用模块级 `from src.api.app import app`,共享 G3
`RateLimitMiddleware` 的内存桶。`TestClient` 默认 `client.host="testclient"`,
所有测试都打同一个 IP key,跨测试累积超过 `RATE_LIMIT_PER_MINUTE=30` → 后续
测试拿到 429 而不是预期状态码,假阴性。

**修复**:autouse fixture 关掉 limiter — 测试只验业务逻辑,限流由
`tests/unit/test_rate_limiter.py` 单独覆盖,不重复。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_rate_limit_for_integration(monkeypatch):
    """所有 integration 测试自动关 limiter,避免跨测试 429 假阴性。

    单元测试 `tests/unit/test_rate_limiter.py` 用 `_make_app(limit=N)` 自建小 limit
    app,不被本 fixture 影响。
    """
    from src.api.middleware.rate_limiter import InMemorySlidingWindow

    monkeypatch.setattr(
        InMemorySlidingWindow,
        "is_allowed",
        lambda self, key, limit, window_seconds: (True, 0),
    )
