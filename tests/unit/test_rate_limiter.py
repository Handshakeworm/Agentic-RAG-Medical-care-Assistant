"""tests/unit/test_rate_limiter.py — G3 限流 unit 测试。

锁住 spec §8.4 G3 验收:基于内存的滑动窗口、超限 429、单元测试。

分两层:
1. `InMemorySlidingWindow` 算法本身 — 时间用 `time.monotonic` mock 精确控制
2. `RateLimitMiddleware` 集成行为 — TestClient 直接打 FastAPI app
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.rate_limiter import (
    InMemorySlidingWindow,
    RateLimitMiddleware,
    _key_for_request,
)


# ────────────────────────────────────────────────────────────────────────────
# InMemorySlidingWindow 算法
# ────────────────────────────────────────────────────────────────────────────


def test_window_allows_up_to_limit() -> None:
    """配额 3 → 头 3 次放行,第 4 次拒。"""
    backend = InMemorySlidingWindow()
    results = [backend.is_allowed("k", limit=3, window_seconds=60) for _ in range(4)]
    assert [r[0] for r in results] == [True, True, True, False]


def test_window_slides_after_time_advances() -> None:
    """打满后,时间往前推超过窗口 → 又能打满。"""
    backend = InMemorySlidingWindow()

    fake_time = [1000.0]

    def fake_monotonic() -> float:
        return fake_time[0]

    with patch(
        "src.api.middleware.rate_limiter.time.monotonic", side_effect=fake_monotonic
    ):
        # t=1000:打满 3 次,第 4 次拒
        for _ in range(3):
            allowed, _ = backend.is_allowed("k", 3, 60)
            assert allowed
        allowed, retry = backend.is_allowed("k", 3, 60)
        assert not allowed
        assert retry >= 1

        # t=1061:窗口已滑过最早那条 → 第 4 次放行
        fake_time[0] = 1061.0
        allowed, _ = backend.is_allowed("k", 3, 60)
        assert allowed


def test_window_keys_are_isolated() -> None:
    """不同 key 各自独立配额,互不串。"""
    backend = InMemorySlidingWindow()
    for _ in range(3):
        assert backend.is_allowed("ip:1.2.3.4", 3, 60)[0]
    # 这个 IP 已用尽
    assert not backend.is_allowed("ip:1.2.3.4", 3, 60)[0]
    # 另一个 IP 完全独立
    assert backend.is_allowed("ip:5.6.7.8", 3, 60)[0]


def test_retry_after_decreases_as_window_slides() -> None:
    """retry_after 应随时间推进而减少(指向最老戳过期那一刻)。"""
    backend = InMemorySlidingWindow()
    fake_time = [1000.0]

    with patch(
        "src.api.middleware.rate_limiter.time.monotonic", side_effect=lambda: fake_time[0]
    ):
        backend.is_allowed("k", 1, 60)  # 装满
        fake_time[0] = 1010.0  # 过了 10 秒
        allowed, retry_at_10s = backend.is_allowed("k", 1, 60)
        assert not allowed

        fake_time[0] = 1050.0  # 过了 50 秒
        allowed, retry_at_50s = backend.is_allowed("k", 1, 60)
        assert not allowed

        assert retry_at_50s < retry_at_10s, "时间推进 → retry_after 应该更小"


def test_retry_after_is_at_least_one_second() -> None:
    """边界:即使瞬间打满,retry_after 也保证 >= 1(HTTP 标准 + 防止 0/负数误用)。"""
    backend = InMemorySlidingWindow()
    backend.is_allowed("k", 1, 60)
    allowed, retry = backend.is_allowed("k", 1, 60)
    assert not allowed
    assert retry >= 1


# ────────────────────────────────────────────────────────────────────────────
# RateLimitMiddleware 集成
# ────────────────────────────────────────────────────────────────────────────


def _make_app(limit: int = 3, excluded: tuple = ("/metrics",)) -> FastAPI:
    """注:H6 已把默认 backend 改成 RedisSlidingWindow,集成测试这里要显式
    传 InMemorySlidingWindow() — 否则会去打真 Redis(开发机有 docker compose 起着),
    串库且行为不可控。Redis 后端有专门的 test_redis_rate_limit_backend.py 覆盖。"""
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        backend=InMemorySlidingWindow(),
        limit=limit,
        window_seconds=60,
        excluded_paths=excluded,
    )

    @app.get("/ping")
    def _ping() -> dict:
        return {"ok": True}

    @app.get("/metrics")
    def _metrics() -> str:
        return "fake-metrics-body"

    return app


def test_middleware_blocks_after_limit() -> None:
    app = _make_app(limit=3)
    client = TestClient(app)
    for _ in range(3):
        assert client.get("/ping").status_code == 200
    resp = client.get("/ping")
    assert resp.status_code == 429
    body = resp.json()
    assert body["detail"]
    assert body["limit"] == 3
    assert body["window_seconds"] == 60
    assert body["retry_after_seconds"] >= 1
    assert resp.headers.get("retry-after") == str(body["retry_after_seconds"])


def test_middleware_excludes_metrics_endpoint() -> None:
    """spec §5.2.1 ② 末:/metrics 不被限流(Prometheus 抓取每 15s 一次)。"""
    app = _make_app(limit=3)
    client = TestClient(app)
    # /metrics 调 10 次都应该 200,不挡
    for _ in range(10):
        assert client.get("/metrics").status_code == 200


def test_middleware_user_token_isolates_from_ip() -> None:
    """同一 IP 但带不同 JWT(不同 user_id)→ 各自独立配额。"""
    from src.api.middleware.auth_middleware import encode_access_token

    app = _make_app(limit=2)
    client = TestClient(app)

    token_a = encode_access_token(user_id="user-a", role="patient")
    token_b = encode_access_token(user_id="user-b", role="patient")

    # user-a 打满
    for _ in range(2):
        assert client.get("/ping", headers={"Authorization": f"Bearer {token_a}"}).status_code == 200
    assert client.get("/ping", headers={"Authorization": f"Bearer {token_a}"}).status_code == 429

    # user-b 同 IP 同进程,但 key 不同,完全独立
    for _ in range(2):
        assert client.get("/ping", headers={"Authorization": f"Bearer {token_b}"}).status_code == 200


def test_middleware_tampered_token_falls_back_to_ip_not_bypass() -> None:
    """伪造 token 不应该让攻击者绕过 IP 限流 — 解 token 失败 → 共享 IP 桶。"""
    app = _make_app(limit=2)
    client = TestClient(app)

    # 先用伪造 token 打 2 次,把 IP 桶填满
    fake_headers = {"Authorization": "Bearer invalid-token-xxx"}
    for _ in range(2):
        assert client.get("/ping", headers=fake_headers).status_code == 200

    # 第 3 次仍然伪造 token → 应该 429(IP 桶满了)
    assert client.get("/ping", headers=fake_headers).status_code == 429

    # 不带 token 也 429(同 IP 桶)
    assert client.get("/ping").status_code == 429


# ────────────────────────────────────────────────────────────────────────────
# _key_for_request 单独覆盖
# ────────────────────────────────────────────────────────────────────────────


def test_key_for_request_valid_token_returns_user_key() -> None:
    from src.api.middleware.auth_middleware import encode_access_token
    from starlette.requests import Request

    token = encode_access_token(user_id="abc-123", role="patient")
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "client": ("1.1.1.1", 50000),
    }
    req = Request(scope)
    assert _key_for_request(req) == "user:abc-123"


def test_key_for_request_no_token_returns_ip_key() -> None:
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [],
        "client": ("9.9.9.9", 12345),
    }
    req = Request(scope)
    assert _key_for_request(req) == "ip:9.9.9.9"


def test_key_for_request_invalid_token_falls_back_to_ip() -> None:
    """伪造 token 不直接 401,fallback 到 IP key — 防止攻击者用伪造 token 绕 IP 桶。"""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [(b"authorization", b"Bearer tampered.xxx.yyy")],
        "client": ("8.8.8.8", 100),
    }
    req = Request(scope)
    assert _key_for_request(req) == "ip:8.8.8.8"
