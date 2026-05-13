"""tests/integration/test_admin_routes.py — G6 admin 接口闭环。

覆盖:
- 角色守卫:patient token 进 admin 接口 → 403
- 用户管理:GET /admin/users 分页、DELETE 不能删自己
- system_config GET/PUT/DELETE 闭环
- PUT 同事务写 config_change_log(spec §5.3.1)
- 知识库上传 stub 返 202 + unimplemented
"""
from __future__ import annotations

import os
import socket
import uuid

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


pytestmark = pytest.mark.skipif(not _pg_alive(), reason="PG 不可达")


@pytest.fixture
def client() -> TestClient:
    from src.api.app import app
    return TestClient(app)


def _register(client: TestClient, role: str = "admin") -> tuple[str, str]:
    email = f"g6_{role}_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "hunter22", "role": role},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"], email


@pytest.fixture
def admin_session():
    from src.api.app import app
    from src.db.postgres.connection import session_scope

    with TestClient(app) as c:
        token, email = _register(c, role="admin")
    yield token, email
    with session_scope() as s:
        s.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})


# ────────────────────────────────────────────────────────────────────────────
# 角色守卫
# ────────────────────────────────────────────────────────────────────────────


def test_patient_role_cannot_access_admin_endpoints(client: TestClient) -> None:
    from src.db.postgres.connection import session_scope

    token, email = _register(client, role="patient")
    try:
        for url in ("/admin/users", "/admin/config", "/admin/kb/upload"):
            method = client.post if "upload" in url else client.get
            resp = method(url, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 403, f"{url} 应被 patient 角色挡掉"
    finally:
        with session_scope() as s:
            s.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})


def test_admin_endpoints_require_token(client: TestClient) -> None:
    for url in ("/admin/users", "/admin/config"):
        assert client.get(url).status_code == 401


# ────────────────────────────────────────────────────────────────────────────
# 用户管理
# ────────────────────────────────────────────────────────────────────────────


def test_list_users_returns_admin_self(client: TestClient, admin_session) -> None:
    token, email = admin_session
    resp = client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()}
    assert email in emails


def test_list_users_pagination(client: TestClient, admin_session) -> None:
    token, _ = admin_session
    resp = client.get(
        "/admin/users?limit=1&offset=0",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) <= 1


def test_admin_cannot_delete_self(client: TestClient, admin_session) -> None:
    """防 admin 误手把自己 ban 掉锁死系统。"""
    from src.db.postgres.connection import session_scope

    token, email = admin_session
    with session_scope() as s:
        my_id = s.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": email}
        ).scalar_one()

    resp = client.delete(
        f"/admin/users/{my_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_admin_can_delete_other_user(client: TestClient, admin_session) -> None:
    from src.db.postgres.connection import session_scope

    admin_token, _ = admin_session
    _, victim_email = _register(client, role="patient")

    with session_scope() as s:
        victim_id = s.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": victim_email}
        ).scalar_one()

    resp = client.delete(
        f"/admin/users/{victim_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 204

    with session_scope() as s:
        cnt = s.execute(
            text("SELECT count(*) FROM users WHERE id = :uid"), {"uid": victim_id}
        ).scalar_one()
        assert cnt == 0


# ────────────────────────────────────────────────────────────────────────────
# system_config + config_change_log 同事务
# ────────────────────────────────────────────────────────────────────────────


def test_config_upsert_writes_change_log(client: TestClient, admin_session) -> None:
    """spec §5.3.1 末:admin 改值 → 同事务写 system_config + config_change_log。"""
    from src.db.postgres.connection import session_scope

    token, _ = admin_session
    headers = {"Authorization": f"Bearer {token}"}
    key = f"test_g6_{uuid.uuid4().hex[:8]}"

    try:
        # 首次写入
        resp = client.put(
            f"/admin/config/{key}",
            headers=headers,
            json={
                "value": 0.7,
                "value_type": "FLOAT",
                "description": "LLM 温度测试",
                "change_reason": "初次设置",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert float(body["value"]) == 0.7

        # 改值 → 应该再写一行 change_log
        resp = client.put(
            f"/admin/config/{key}",
            headers=headers,
            json={"value": 0.3, "change_reason": "降温减少幻觉"},
        )
        assert resp.status_code == 200
        assert float(resp.json()["value"]) == 0.3

        # 验证 config_change_log 有 2 行
        with session_scope() as s:
            logs = s.execute(
                text(
                    "SELECT old_value, new_value, change_reason FROM config_change_log "
                    "WHERE config_key = :k ORDER BY created_at"
                ),
                {"k": key},
            ).all()
            assert len(logs) == 2
            assert logs[0][0] is None and float(logs[0][1]) == 0.7
            assert float(logs[1][0]) == 0.7 and float(logs[1][1]) == 0.3
            assert logs[1][2] == "降温减少幻觉"
    finally:
        with session_scope() as s:
            s.execute(text("DELETE FROM config_change_log WHERE config_key = :k"), {"k": key})
            s.execute(text("DELETE FROM system_config WHERE key_name = :k"), {"k": key})


def test_config_get_404_for_missing_key(client: TestClient, admin_session) -> None:
    token, _ = admin_session
    resp = client.get(
        f"/admin/config/nonexistent_{uuid.uuid4().hex[:8]}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_config_delete_writes_change_log(client: TestClient, admin_session) -> None:
    from src.db.postgres.connection import session_scope

    token, _ = admin_session
    headers = {"Authorization": f"Bearer {token}"}
    key = f"test_del_{uuid.uuid4().hex[:8]}"

    try:
        client.put(
            f"/admin/config/{key}",
            headers=headers,
            json={"value": "x", "change_reason": "create"},
        )
        resp = client.delete(f"/admin/config/{key}", headers=headers)
        assert resp.status_code == 204

        # change_log 应该有 2 条:create + delete(new_value=null)
        with session_scope() as s:
            logs = s.execute(
                text(
                    "SELECT new_value, change_reason FROM config_change_log "
                    "WHERE config_key = :k ORDER BY created_at"
                ),
                {"k": key},
            ).all()
            assert len(logs) == 2
            assert logs[1][0] is None  # delete 的 new_value=null
            assert "DELETE" in logs[1][1]
    finally:
        with session_scope() as s:
            s.execute(text("DELETE FROM config_change_log WHERE config_key = :k"), {"k": key})
            s.execute(text("DELETE FROM system_config WHERE key_name = :k"), {"k": key})


# ────────────────────────────────────────────────────────────────────────────
# 知识库上传 stub
# ────────────────────────────────────────────────────────────────────────────


def test_kb_upload_returns_unimplemented_stub(client: TestClient, admin_session) -> None:
    """C7 pipeline 未做完,本端点先返 202 + unimplemented 提示。"""
    token, _ = admin_session
    resp = client.post(
        "/admin/kb/upload",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "unimplemented"
    assert "C7" in body["instruction"]
