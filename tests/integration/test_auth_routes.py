"""tests/integration/test_auth_routes.py — auth 端点闭环(TestClient + 真 PG)。

完整覆盖 spec §8.4 G2 验收:注册 / 登录 → token 签发 + 校验 + 角色提取,
过期/无效 token 返 401。

需要 PG 真服务在跑(`docker compose up -d postgres`)+ alembic upgrade head 已跑过。
跳过条件:PG 不可达 → skip。
"""
from __future__ import annotations

import os
import socket
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


def _pg_alive() -> bool:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    try:
        socket.create_connection((host, port), timeout=2).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _pg_alive(), reason="PG 不可达,启动 docker compose 后再跑")


@pytest.fixture
def client() -> TestClient:
    from src.api.app import app
    return TestClient(app)


@pytest.fixture
def fresh_email():
    """每个测试用独立 email + 自动清理。"""
    from src.db.postgres.connection import session_scope

    email = f"g2_{uuid.uuid4().hex[:8]}@example.com"
    yield email
    with session_scope() as s:
        s.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})


# ────────────────────────────────────────────────────────────────────────────
# POST /auth/register
# ────────────────────────────────────────────────────────────────────────────


def test_register_returns_201_with_token(client: TestClient, fresh_email: str) -> None:
    resp = client.post(
        "/auth/register",
        json={"email": fresh_email, "password": "hunter22", "role": "patient"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in_seconds"] > 0


def test_register_writes_hashed_password_to_db(client: TestClient, fresh_email: str) -> None:
    """spec §2.4.5:users.password 是哈希后,不是明文。"""
    from src.db.postgres.connection import session_scope

    resp = client.post(
        "/auth/register",
        json={"email": fresh_email, "password": "hunter22"},
    )
    assert resp.status_code == 201

    with session_scope() as s:
        row = s.execute(
            text("SELECT password FROM users WHERE email = :e"), {"e": fresh_email}
        ).one()
        assert row[0] != "hunter22"
        assert row[0].startswith("$2")  # bcrypt 标识


def test_register_duplicate_email_returns_409(client: TestClient, fresh_email: str) -> None:
    payload = {"email": fresh_email, "password": "hunter22"}
    assert client.post("/auth/register", json=payload).status_code == 201
    resp2 = client.post("/auth/register", json=payload)
    assert resp2.status_code == 409
    assert fresh_email in resp2.json()["detail"]


def test_register_weak_password_returns_422(client: TestClient, fresh_email: str) -> None:
    """schema 强制 password >= 6 字符。"""
    resp = client.post(
        "/auth/register",
        json={"email": fresh_email, "password": "abc"},
    )
    assert resp.status_code == 422


def test_register_invalid_role_returns_422(client: TestClient, fresh_email: str) -> None:
    """schema Literal['patient', 'admin'] 限制,'doctor' 等本期未实现的角色被挡。"""
    resp = client.post(
        "/auth/register",
        json={"email": fresh_email, "password": "hunter22", "role": "doctor"},
    )
    assert resp.status_code == 422


# ────────────────────────────────────────────────────────────────────────────
# POST /auth/login
# ────────────────────────────────────────────────────────────────────────────


def test_login_with_correct_password_returns_token(
    client: TestClient, fresh_email: str
) -> None:
    client.post(
        "/auth/register",
        json={"email": fresh_email, "password": "hunter22"},
    )
    resp = client.post(
        "/auth/login",
        json={"email": fresh_email, "password": "hunter22"},
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_login_with_wrong_password_returns_401(
    client: TestClient, fresh_email: str
) -> None:
    client.post(
        "/auth/register",
        json={"email": fresh_email, "password": "hunter22"},
    )
    resp = client.post(
        "/auth/login",
        json={"email": fresh_email, "password": "wrong-password"},
    )
    assert resp.status_code == 401
    # 不暴露"用户存在",防枚举攻击
    assert "邮箱或密码" in resp.json()["detail"]


def test_login_with_nonexistent_user_returns_401(client: TestClient) -> None:
    """spec G2:用户不存在 → 与密码错共享 401 消息,避免账号枚举。"""
    resp = client.post(
        "/auth/login",
        json={"email": "nobody-here@example.com", "password": "anything"},
    )
    assert resp.status_code == 401


# ────────────────────────────────────────────────────────────────────────────
# GET /auth/me — 带 token 校验
# ────────────────────────────────────────────────────────────────────────────


def test_me_with_valid_token_returns_identity(
    client: TestClient, fresh_email: str
) -> None:
    reg = client.post(
        "/auth/register",
        json={"email": fresh_email, "password": "hunter22", "role": "patient"},
    )
    token = reg.json()["access_token"]
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == fresh_email
    assert body["role"] == "patient"
    assert body["user_id"]


def test_me_without_token_returns_401(client: TestClient) -> None:
    resp = client.get("/auth/me")
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


def test_me_with_garbage_token_returns_401(client: TestClient) -> None:
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_me_with_expired_token_returns_401(client: TestClient) -> None:
    """直接签一个过期 token 打 /auth/me,断言 401(对接 spec G2 验收点)。"""
    from src.api.middleware.auth_middleware import encode_access_token

    expired = encode_access_token(
        user_id=str(uuid.uuid4()),
        role="patient",
        expires_delta=timedelta(seconds=-1),
    )
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
    assert "过期" in resp.json()["detail"]


def test_me_with_wrong_secret_token_returns_401(client: TestClient) -> None:
    """伪造 token(别的密钥签的)→ 401。"""
    import jwt as _jwt

    fake = _jwt.encode(
        {"sub": "fake-user-id", "role": "admin", "exp": 9999999999},
        "different-secret",
        algorithm="HS256",
    )
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {fake}"})
    assert resp.status_code == 401


# ────────────────────────────────────────────────────────────────────────────
# require_role 守卫(端到端验证)
# ────────────────────────────────────────────────────────────────────────────


def test_require_role_guards_admin_endpoint_via_test_router(
    client: TestClient, fresh_email: str
) -> None:
    """临时挂一个 admin-only 测试端点,验证 patient token 被 403 挡。

    生产端点(G6 /admin/*)还没实现,但 require_role 工厂逻辑现在就要锁住。
    """
    from fastapi import APIRouter, Depends

    from src.api.app import app
    from src.api.middleware.auth_middleware import require_role

    test_router = APIRouter()

    @test_router.get("/_test/admin-only")
    def _admin_only(_=Depends(require_role("admin"))) -> dict:
        return {"ok": True}

    app.include_router(test_router)
    try:
        # 用 patient 角色 token
        reg = client.post(
            "/auth/register",
            json={"email": fresh_email, "password": "hunter22", "role": "patient"},
        )
        token = reg.json()["access_token"]
        resp = client.get(
            "/_test/admin-only", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403
        assert "admin" in resp.json()["detail"]
    finally:
        # 清理:把临时 router 的 routes 从 app 上抹掉,避免污染其他测试
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", "") != "/_test/admin-only"
        ]


def test_require_role_allows_admin_token(client: TestClient, fresh_email: str) -> None:
    from fastapi import APIRouter, Depends

    from src.api.app import app
    from src.api.middleware.auth_middleware import require_role

    test_router = APIRouter()

    @test_router.get("/_test/admin-only")
    def _admin_only(_=Depends(require_role("admin"))) -> dict:
        return {"ok": True}

    app.include_router(test_router)
    try:
        reg = client.post(
            "/auth/register",
            json={"email": fresh_email, "password": "hunter22", "role": "admin"},
        )
        token = reg.json()["access_token"]
        resp = client.get(
            "/_test/admin-only", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
    finally:
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", "") != "/_test/admin-only"
        ]
