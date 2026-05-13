"""tests/integration/test_patient_routes.py — G5 患者档案端点闭环。

覆盖:
- 角色守卫:admin token 访问 /patients/me 应 403
- GET /patients/me 主档案 + 8 张子表汇总
- PUT /patients/me 基本信息更新(不存在则自动建 patients 行)
- 三张 ⚠️必问表 POST/DELETE 闭环
- 身份隔离:DELETE 别人的子表行 → 404
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


def _register(client: TestClient, role: str = "patient") -> tuple[str, str]:
    email = f"g5_{role}_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "hunter22", "role": role},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"], email


@pytest.fixture
def patient_session():
    """注册 patient + 自动 teardown(级联清 patients/子表 + users)。"""
    from src.api.app import app
    from src.db.postgres.connection import session_scope

    with TestClient(app) as c:
        token, email = _register(c)
    yield token, email
    with session_scope() as s:
        s.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})


# ────────────────────────────────────────────────────────────────────────────
# 角色守卫
# ────────────────────────────────────────────────────────────────────────────


def test_admin_role_cannot_access_patient_endpoints(client: TestClient) -> None:
    """admin 角色访问 /patients/me 应 403(spec G5:仅 patient 角色)。"""
    from src.db.postgres.connection import session_scope

    token, email = _register(client, role="admin")
    try:
        resp = client.get("/patients/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
    finally:
        with session_scope() as s:
            s.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})


# ────────────────────────────────────────────────────────────────────────────
# GET / PUT 主档案
# ────────────────────────────────────────────────────────────────────────────


def test_get_profile_for_new_user_returns_empty_history(
    client: TestClient, patient_session
) -> None:
    """新注册用户没填档案,8 张子表都返空。"""
    token, email = patient_session
    resp = client.get("/patients/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == email
    assert body["name"] is None  # patients 行不存在 → 字段全 None
    assert body["allergy_history"] == []
    assert body["medication_history"] == []
    assert body["family_history"] == []


def test_put_profile_creates_patients_row_on_first_update(
    client: TestClient, patient_session
) -> None:
    """spec §2.4.5:patients 1:1 与 users。注册时只建 users,patients 延后到首次填档。"""
    token, _ = patient_session
    resp = client.put(
        "/patients/me",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "测试用户",
            "gender": "male",
            "birth_date": "1990-01-01",
            "smoking_status": "never",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "测试用户"
    assert body["gender"] == "male"
    assert body["birth_date"] == "1990-01-01"
    assert body["personal_history"]["smoking_status"] == "never"


def test_put_profile_only_updates_provided_fields(
    client: TestClient, patient_session
) -> None:
    """exclude_unset 让 PUT 只动显式给的字段(部分更新语义)。"""
    token, _ = patient_session
    headers = {"Authorization": f"Bearer {token}"}

    client.put("/patients/me", headers=headers, json={"name": "甲", "gender": "male"})
    client.put("/patients/me", headers=headers, json={"name": "乙"})  # 只改 name

    body = client.get("/patients/me", headers=headers).json()
    assert body["name"] == "乙"
    assert body["gender"] == "male"  # 未传 → 不变


# ────────────────────────────────────────────────────────────────────────────
# 三张 ⚠️必问表 POST/DELETE
# ────────────────────────────────────────────────────────────────────────────


def test_create_and_delete_allergy(client: TestClient, patient_session) -> None:
    token, _ = patient_session
    headers = {"Authorization": f"Bearer {token}"}

    # POST
    resp = client.post(
        "/patients/me/allergies",
        headers=headers,
        json={
            "allergen": "青霉素",
            "allergen_type": "drug",
            "severity": "severe",
            "status": "confirmed",
        },
    )
    assert resp.status_code == 201
    record_id = resp.json()["id"]

    # GET 主档案 → 含此条
    body = client.get("/patients/me", headers=headers).json()
    assert any(a["substance"] == "青霉素" for a in body["allergy_history"])

    # DELETE → 204
    resp = client.delete(f"/patients/me/allergies/{record_id}", headers=headers)
    assert resp.status_code == 204

    # 再 GET → 已没有
    body = client.get("/patients/me", headers=headers).json()
    assert body["allergy_history"] == []


def test_create_medication_and_visible_via_safety_gate_query(
    client: TestClient, patient_session
) -> None:
    """safety_gate ⑪ 读 medications WHERE patient_id=... — 验整链能走通。"""
    from src.agent.utils.patient_repo import load_medical_history
    from src.db.postgres.connection import session_scope

    token, email = patient_session
    headers = {"Authorization": f"Bearer {token}"}

    client.post(
        "/patients/me/medications",
        headers=headers,
        json={
            "drug_name": "二甲双胍",
            "drug_category": "hypoglycemic",
            "dosage": "500mg",
            "frequency": "每日两次",
            "is_self_medication": False,
        },
    )

    # 直接调 patient_repo(safety_gate 用的同一接口)
    with session_scope() as s:
        user_id = s.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": email}
        ).scalar_one()
    history = load_medical_history(str(user_id))
    assert any(m["drug_name"] == "二甲双胍" for m in history["medication_history"])


def test_delete_other_users_record_returns_404(client: TestClient) -> None:
    """A 拿 B 的 medication record_id 调 DELETE → 404(防泄漏 + 防越权)。"""
    from src.db.postgres.connection import session_scope

    token_a, email_a = _register(client)
    token_b, email_b = _register(client)

    try:
        # B 创建一条用药
        resp = client.post(
            "/patients/me/medications",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"drug_name": "胰岛素"},
        )
        record_id = resp.json()["id"]

        # A 尝试删 → 404
        resp = client.delete(
            f"/patients/me/medications/{record_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 404

        # B 自己删可以
        resp = client.delete(
            f"/patients/me/medications/{record_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 204
    finally:
        with session_scope() as s:
            s.execute(text("DELETE FROM users WHERE email IN (:a, :b)"), {"a": email_a, "b": email_b})


def test_create_medical_history_and_get_back(client: TestClient, patient_session) -> None:
    token, _ = patient_session
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/patients/me/medical-history",
        headers=headers,
        json={
            "category": "chronic",
            "condition": "2型糖尿病",
            "icd10_code": "E11",
            "control_status": "well_controlled",
        },
    )
    assert resp.status_code == 201

    body = client.get("/patients/me", headers=headers).json()
    items = body["past_history"]["medical_history"]
    assert len(items) == 1
    assert items[0]["condition"] == "2型糖尿病"
    assert items[0]["icd10_code"] == "E11"
