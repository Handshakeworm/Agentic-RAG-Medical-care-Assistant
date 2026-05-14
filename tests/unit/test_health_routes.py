"""tests/unit/test_health_routes.py — H8 健康检查端点单元测试(DEV_SPEC §5.2.4)。

四条路径(spec §8.4 H8 验收):
1. PG OK + Milvus OK → /readyz 200 ready
2. PG fail            → /readyz 503,failing=["postgres"]
3. Milvus fail        → /readyz 503,failing=["milvus"]
4. 双失败             → /readyz 503,failing=["postgres","milvus"]

附:
5. /healthz 零依赖固定 200(即便 PG 挂了也是)
6. /healthz /readyz 不经过 JWT / 限流(对齐 spec §5.2.4 末)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.health import router as health_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(health_router)
    return TestClient(app)


# ────────────────────────────────────────────────────────────────────────────
# /healthz — Liveness
# ────────────────────────────────────────────────────────────────────────────


def test_healthz_returns_200_with_no_deps(client):
    """spec §5.2.4:零依赖,固定 200"""
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@patch("src.api.routes.health._probe_postgres")
def test_healthz_unaffected_by_pg_failure(mock_pg, client):
    """spec §5.2.4 末:DB 挂了 /healthz 也返 200"""
    mock_pg.return_value = ("postgres", False, "ConnectionError")
    r = client.get("/healthz")
    assert r.status_code == 200


# ────────────────────────────────────────────────────────────────────────────
# /readyz — Readiness 4 条路径
# ────────────────────────────────────────────────────────────────────────────


@patch("src.api.routes.health._probe_milvus")
@patch("src.api.routes.health._probe_postgres")
def test_readyz_all_ok(mock_pg, mock_mv, client):
    mock_pg.return_value = ("postgres", True, None)
    mock_mv.return_value = ("milvus", True, None)
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["deps"] == {"postgres": "ok", "milvus": "ok"}


@patch("src.api.routes.health._probe_milvus")
@patch("src.api.routes.health._probe_postgres")
def test_readyz_pg_fail(mock_pg, mock_mv, client):
    mock_pg.return_value = ("postgres", False, "OperationalError")
    mock_mv.return_value = ("milvus", True, None)
    r = client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["failing"] == ["postgres"]
    assert body["deps"]["postgres"] == "OperationalError"
    assert body["deps"]["milvus"] == "ok"


@patch("src.api.routes.health._probe_milvus")
@patch("src.api.routes.health._probe_postgres")
def test_readyz_milvus_fail(mock_pg, mock_mv, client):
    mock_pg.return_value = ("postgres", True, None)
    mock_mv.return_value = ("milvus", False, "MilvusException")
    r = client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["failing"] == ["milvus"]
    assert body["deps"]["milvus"] == "MilvusException"


@patch("src.api.routes.health._probe_milvus")
@patch("src.api.routes.health._probe_postgres")
def test_readyz_both_fail(mock_pg, mock_mv, client):
    mock_pg.return_value = ("postgres", False, "PgErr")
    mock_mv.return_value = ("milvus", False, "MvErr")
    r = client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert sorted(body["failing"]) == ["milvus", "postgres"]


@patch("src.api.routes.health._probe_milvus")
@patch("src.api.routes.health._probe_postgres")
def test_readyz_redis_unavailable_does_not_block(mock_pg, mock_mv, client):
    """spec §5.2.4:Redis 不可用仍算 ready(降级模式)— 我们根本不探 Redis"""
    mock_pg.return_value = ("postgres", True, None)
    mock_mv.return_value = ("milvus", True, None)
    # 即便 Redis 完全没起,/readyz 也 200(因为 _probe_redis 不存在)
    r = client.get("/readyz")
    assert r.status_code == 200


# ────────────────────────────────────────────────────────────────────────────
# 不经过 JWT / 限流 / 审计
# ────────────────────────────────────────────────────────────────────────────


def test_health_endpoints_in_excluded_paths():
    """spec §5.2.4 末:两个端点不经 JWT / 限流 / 审计

    我们通过检查 RateLimitMiddleware._DEFAULT_EXCLUDED_PATHS 与 G1
    Instrumentator excluded_handlers 是否包含两个路径来验证(更直观):
    """
    from src.api.middleware.rate_limiter import _DEFAULT_EXCLUDED_PATHS
    from src.api.app import _METRICS_EXCLUDED_HANDLERS

    assert "/healthz" in _DEFAULT_EXCLUDED_PATHS
    assert "/readyz" in _DEFAULT_EXCLUDED_PATHS
    assert "/healthz" in _METRICS_EXCLUDED_HANDLERS
    assert "/readyz" in _METRICS_EXCLUDED_HANDLERS
