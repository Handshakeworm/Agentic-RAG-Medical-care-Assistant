"""tests/unit/test_patient_models.py — 锁住 §2.4.5 用户/患者 ORM schema。

不连真 PG,只校验 SQLAlchemy 元数据。CRUD 走 tests/integration/test_patient_crud.py。
"""
from __future__ import annotations

from sqlalchemy import Boolean, Date, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from src.db.postgres.models_patient import (
    Allergy,
    ExamReport,
    FamilyHistory,
    MedicalHistory,
    Medication,
    MenstrualReproductive,
    Patient,
    SurgicalTraumaHistory,
    TransfusionHistory,
    User,
)


# ────────────────────────────────────────────────────────────────────────────
# users — §2.4.5
# ────────────────────────────────────────────────────────────────────────────


def test_users_table_pk_email_unique_role_required() -> None:
    assert User.__tablename__ == "users"
    pk = [c.name for c in User.__table__.primary_key.columns]
    assert pk == ["id"]

    email = User.__table__.c["email"]
    assert isinstance(email.type, Text)
    assert email.unique is True
    assert not email.nullable

    role = User.__table__.c["role"]
    assert isinstance(role.type, String) and role.type.length == 20
    assert not role.nullable


def test_users_id_uuid_with_gen_random_default() -> None:
    col = User.__table__.c["id"]
    assert isinstance(col.type, PG_UUID)
    assert "gen_random_uuid" in str(col.server_default.arg)


# ────────────────────────────────────────────────────────────────────────────
# patients — §2.4.5(1:1 users,FK + ondelete=CASCADE)
# ────────────────────────────────────────────────────────────────────────────


def test_patients_pk_is_fk_to_users_with_cascade() -> None:
    assert Patient.__tablename__ == "patients"
    pk = [c.name for c in Patient.__table__.primary_key.columns]
    assert pk == ["id"]

    fks = list(Patient.__table__.c["id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "users"
    assert fks[0].ondelete == "CASCADE"


def test_patients_personal_history_columns_present() -> None:
    """spec §2.4.5 patients 表 8 个个人史字段(smoking / alcohol / occupation / travel /
    infectious_contact)直接内嵌,不另建子表。"""
    cols = {c.name for c in Patient.__table__.columns}
    expected_personal = {
        "smoking_status",
        "smoking_pack_years",
        "alcohol_status",
        "alcohol_detail",
        "occupation",
        "occupational_exposure",
        "travel_history",
        "infectious_contact",
    }
    assert expected_personal.issubset(cols)


def test_patients_basic_demographics_types() -> None:
    table = Patient.__table__
    assert isinstance(table.c["gender"].type, String)
    assert table.c["gender"].type.length == 10
    assert isinstance(table.c["birth_date"].type, Date)
    assert isinstance(table.c["weight_kg"].type, Numeric)
    assert table.c["weight_kg"].type.precision == 5
    assert table.c["weight_kg"].type.scale == 1


# ────────────────────────────────────────────────────────────────────────────
# 历史子表 — 锁 patient_id FK + cascade(以 medications 为代表)
# ────────────────────────────────────────────────────────────────────────────


def test_history_tables_all_cascade_on_patient_delete() -> None:
    """8 张患者历史表 + exam_reports 必须配 ON DELETE CASCADE,
    确保删 patients 行时清理所有子表(spec §2.4.5 关系图)。"""
    history_tables = [
        MedicalHistory,
        SurgicalTraumaHistory,
        TransfusionHistory,
        Allergy,
        Medication,
        FamilyHistory,
        MenstrualReproductive,
        ExamReport,
    ]
    for cls in history_tables:
        fks = list(cls.__table__.c["patient_id"].foreign_keys)
        assert len(fks) == 1, f"{cls.__name__}.patient_id 应有 1 个 FK"
        assert fks[0].column.table.name == "patients", f"{cls.__name__} FK 目标错"
        assert fks[0].ondelete == "CASCADE", f"{cls.__name__} 缺 ON DELETE CASCADE"


def test_menstrual_reproductive_is_one_to_one() -> None:
    """女性婚育/月经史 1:1(`patient_id` UNIQUE)。"""
    col = MenstrualReproductive.__table__.c["patient_id"]
    assert col.unique is True


def test_medication_required_fields() -> None:
    table = Medication.__table__
    assert not table.c["drug_name"].nullable
    assert isinstance(table.c["drug_name"].type, Text)
    assert table.c["ended_at"].nullable, "ended_at NULL 表示当前正在服用(spec §2.4.5)"
    assert isinstance(table.c["is_self_medication"].type, Boolean)
    assert not table.c["is_self_medication"].nullable


def test_allergy_status_default_suspected() -> None:
    """过敏史 status 默认 'suspected'(spec §2.4.5)。"""
    col = Allergy.__table__.c["status"]
    assert "suspected" in str(col.server_default.arg)
