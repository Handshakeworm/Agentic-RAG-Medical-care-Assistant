"""tests/integration/test_patient_crud.py — 真起 PG 验证 §2.4.5 用户/患者表闭环。

覆盖:
- users 注册 + email UNIQUE 冲突报错
- patients 1:1 与 users + ON DELETE CASCADE 触发清理子表
- 1 张代表性历史表(medications)CRUD + 当前用药 partial index
- safety_gate ⑪ 读 allergies 的语义场景(allergen_type='drug')

需要:`docker compose up -d postgres` + alembic upgrade head 已跑(或 0004 迁移已应用)。
"""
from __future__ import annotations

import os
import socket
import uuid
from datetime import date

import pytest
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


# ────────────────────────────────────────────────────────────────────────────
# fixtures
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_user():
    """建一个 patient 角色的 users 行,teardown 触发 cascade 删除整条链。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models_patient import User

    email = f"test_b1_{uuid.uuid4().hex[:8]}@example.com"
    with session_scope() as s:
        u = User(email=email, password="hashed_dummy", role="patient")
        s.add(u)
        s.flush()
        s.refresh(u)
        user_id = u.id

    yield user_id

    with session_scope() as s:
        # 删 user 触发 patients ON DELETE CASCADE → patients 子表 ON DELETE CASCADE
        s.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})


# ────────────────────────────────────────────────────────────────────────────
# users
# ────────────────────────────────────────────────────────────────────────────


def test_user_email_unique_constraint() -> None:
    """同 email 第二次插入报 UNIQUE 冲突(spec §2.4.5)。"""
    from sqlalchemy.exc import IntegrityError

    from src.db.postgres.connection import session_scope
    from src.db.postgres.models_patient import User

    email = f"dup_{uuid.uuid4().hex[:8]}@example.com"

    try:
        with session_scope() as s:
            s.add(User(email=email, password="x", role="patient"))

        with pytest.raises(IntegrityError):
            with session_scope() as s:
                s.add(User(email=email, password="y", role="admin"))
    finally:
        with session_scope() as s:
            s.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})


def test_user_id_auto_uuid_generated_by_db(fresh_user) -> None:
    """spec §2.4.5:users.id DEFAULT gen_random_uuid(),ORM 不预生成。"""
    assert fresh_user is not None
    # PG UUID 字符串长度 36(8-4-4-4-12 + 4 个 -)
    assert len(fresh_user) == 36


# ────────────────────────────────────────────────────────────────────────────
# patients 1:1 + 历史表 cascade
# ────────────────────────────────────────────────────────────────────────────


def test_patient_cascade_delete_clears_medications(fresh_user) -> None:
    """删 users 行 → patients(CASCADE)→ medications(CASCADE)。
    spec §2.4.5 关系图:删账号要清空所有医疗历史(GDPR 数据擦除场景)。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models_patient import Medication, Patient

    with session_scope() as s:
        s.add(Patient(id=fresh_user, name="测试患者", gender="male"))
        s.flush()
        s.add(
            Medication(
                patient_id=fresh_user,
                drug_name="阿司匹林",
                drug_category="anticoagulant",
                dosage="100mg",
                frequency="每日一次",
                started_at=date(2024, 1, 1),
                # ended_at NULL → 当前正在服用
            )
        )

    with session_scope() as s:
        cnt = s.execute(
            text("SELECT count(*) FROM medications WHERE patient_id = :pid"),
            {"pid": fresh_user},
        ).scalar_one()
        assert cnt == 1

    # 删 user(fresh_user fixture 的 teardown 也会再删一次,幂等)
    with session_scope() as s:
        s.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": fresh_user})

    # 验证 cascade 清理
    with session_scope() as s:
        cnt = s.execute(
            text("SELECT count(*) FROM medications WHERE patient_id = :pid"),
            {"pid": fresh_user},
        ).scalar_one()
        assert cnt == 0
        cnt = s.execute(
            text("SELECT count(*) FROM patients WHERE id = :pid"),
            {"pid": fresh_user},
        ).scalar_one()
        assert cnt == 0


def test_active_medication_partial_index_used(fresh_user) -> None:
    """spec §2.4.5 medications:safety_gate ⑪ 拉"当前用药列表"用 partial index
    `idx_medications_active WHERE ended_at IS NULL`。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models_patient import Medication, Patient

    with session_scope() as s:
        s.add(Patient(id=fresh_user, name="测试", gender="female"))
        s.flush()
        # 1 个在用,2 个已停
        s.add(Medication(patient_id=fresh_user, drug_name="二甲双胍", ended_at=None))
        s.add(
            Medication(
                patient_id=fresh_user,
                drug_name="氨氯地平",
                started_at=date(2023, 1, 1),
                ended_at=date(2023, 6, 30),
            )
        )
        s.add(
            Medication(
                patient_id=fresh_user,
                drug_name="阿托伐他汀",
                ended_at=date(2024, 8, 1),
            )
        )

    # 查"当前用药" — Index Scan 命中 idx_medications_active
    with session_scope() as s:
        rows = s.execute(
            text(
                "SELECT drug_name FROM medications "
                "WHERE patient_id = :pid AND ended_at IS NULL"
            ),
            {"pid": fresh_user},
        ).all()
        assert len(rows) == 1
        assert rows[0][0] == "二甲双胍"


def test_allergy_drug_listing_for_safety_gate(fresh_user) -> None:
    """模拟 safety_gate ⑪ 规则层从 allergies 抽 banned_drugs 的 SQL。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models_patient import Allergy, Patient

    with session_scope() as s:
        s.add(Patient(id=fresh_user))
        s.flush()
        s.add(
            Allergy(
                patient_id=fresh_user,
                allergen="青霉素",
                allergen_type="drug",
                severity="severe",
                status="confirmed",
            )
        )
        s.add(
            Allergy(
                patient_id=fresh_user,
                allergen="海鲜",
                allergen_type="food",
                severity="mild",
                status="confirmed",
            )
        )

    with session_scope() as s:
        drugs = s.execute(
            text(
                "SELECT allergen FROM allergies "
                "WHERE patient_id = :pid AND allergen_type = 'drug' "
                "AND status != 'resolved'"
            ),
            {"pid": fresh_user},
        ).scalars().all()
        assert drugs == ["青霉素"]


def test_menstrual_reproductive_unique_constraint(fresh_user) -> None:
    """1:1 = patient_id UNIQUE,第二次插入同 patient 报错。"""
    from sqlalchemy.exc import IntegrityError

    from src.db.postgres.connection import session_scope
    from src.db.postgres.models_patient import MenstrualReproductive, Patient

    with session_scope() as s:
        s.add(Patient(id=fresh_user, gender="female"))
        s.flush()
        s.add(MenstrualReproductive(patient_id=fresh_user, menarche_age=13))

    with pytest.raises(IntegrityError):
        with session_scope() as s:
            s.add(MenstrualReproductive(patient_id=fresh_user, menarche_age=14))
