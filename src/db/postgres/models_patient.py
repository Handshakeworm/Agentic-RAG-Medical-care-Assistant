"""PostgreSQL ORM 模型 — 用户认证 + 患者档案(DEV_SPEC §2.4.5)。

本文件覆盖 G2 (auth) / G5 (patient CRUD) 所需的 10 张表:

- `User`                     账号系统(§2.4.5 users)
- `Patient`                  基本信息 + 个人史(§2.4.5 patients,1:1 与 users)
- `MedicalHistory`           既往病史 / 传染病(1:N) ⚠️必问
- `SurgicalTraumaHistory`    手术与外伤史(1:N) ⚠️必问
- `TransfusionHistory`       输血史(1:N)
- `Allergy`                  过敏史(1:N) ⚠️安全底线
- `Medication`               用药史(1:N) ⚠️必问
- `FamilyHistory`            家族史(1:N)
- `MenstrualReproductive`    女性婚育/月经史(1:1)
- `ExamReports`              检查报告(1:N,info_collect ① Step 3 加载)

设计取舍:
- `User.role` 用 VARCHAR 不是 Enum — §5.3.2 角色集合可演进(spec §9.2 类型不收窄)
- 所有 `id` UUID PK + DB 端 `gen_random_uuid()` 默认值;ORM 不预生成
- 历史表 `patient_id` 只走 FK + cascade 删,**不**做 ORM relationship
  (§9.1 风格:裸代码不绕弯,FK 完整性由 DB 保证;真要联查用显式 join)
- 不在此处写 upsert 接口:这些表的写入路径在 G5/G6 的 endpoint 视图里,
  按 §9.6.5 "裸代码不封装"原则现写现 commit
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.postgres.models import Base


# ────────────────────────────────────────────────────────────────────────────
# §2.4.5 users — 账号系统
# ────────────────────────────────────────────────────────────────────────────


class User(Base):
    """users 表(§2.4.5)— 认证 + 角色。

    `role`: VARCHAR(20),取值 `patient` / `admin`(§5.3.2;`doctor` 字段保留
    给后续真人医生角色,本期不实现)。AI 后端服务用固定 service token,
    不进 users 表。

    `password`: 存哈希后的字符串(G2 用 bcrypt / argon2 哈希,本表不关心算法)。
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ────────────────────────────────────────────────────────────────────────────
# §2.4.5 patients — 基本信息 + 个人史(1:1 users)
# ────────────────────────────────────────────────────────────────────────────


class Patient(Base):
    """patients 表(§2.4.5)— 1:1 与 users(`id` 同时是 PK 也是 FK)。

    个人史字段(smoking / alcohol / occupation / travel / infectious_contact)
    低基数,直接内嵌而非另建 1:1 子表。
    """

    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    blood_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    emergency_contact: Mapped[str | None] = mapped_column(Text, nullable=True)

    smoking_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    smoking_pack_years: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    alcohol_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    alcohol_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    occupation: Mapped[str | None] = mapped_column(Text, nullable=True)
    occupational_exposure: Mapped[str | None] = mapped_column(Text, nullable=True)
    travel_history: Mapped[str | None] = mapped_column(Text, nullable=True)
    infectious_contact: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ────────────────────────────────────────────────────────────────────────────
# §2.4.5 既往史 / 手术 / 输血 / 过敏 / 用药 / 家族 / 婚育 / 检查报告(1:N or 1:1)
# ────────────────────────────────────────────────────────────────────────────


class MedicalHistory(Base):
    """既往病史(基础疾病 + 传染病)— 1:N,⚠️必问。"""

    __tablename__ = "medical_history"

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    patient_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    condition: Mapped[str] = mapped_column(Text, nullable=False)
    icd10_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    diagnosed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    resolved_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    control_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SurgicalTraumaHistory(Base):
    """手术与外伤史 — 1:N,⚠️必问。"""

    __tablename__ = "surgical_trauma_history"

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    patient_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    hospital: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_complications: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    complications: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequelae: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TransfusionHistory(Base):
    """输血史 — 1:N。"""

    __tablename__ = "transfusion_history"

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    patient_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    transfusion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    blood_product: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    adverse_reaction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    reaction_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Allergy(Base):
    """过敏史 — 1:N,⚠️安全底线。safety_gate ⑪ 直接读这张表抽 banned_drugs。"""

    __tablename__ = "allergies"

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    patient_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    allergen: Mapped[str] = mapped_column(Text, nullable=False)
    allergen_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reaction: Mapped[str | None] = mapped_column(Text, nullable=True)
    reaction_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="suspected"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Medication(Base):
    """用药史(当前 + 历史)— 1:N,⚠️必问。`ended_at IS NULL` 表示仍在服用。"""

    __tablename__ = "medications"

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    patient_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    drug_name: Mapped[str] = mapped_column(Text, nullable=False)
    drug_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    dosage: Mapped[str | None] = mapped_column(Text, nullable=True)
    frequency: Mapped[str | None] = mapped_column(Text, nullable=True)
    route: Mapped[str | None] = mapped_column(String(20), nullable=True)
    started_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    prescribed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_self_medication: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FamilyHistory(Base):
    """家族史 — 1:N。"""

    __tablename__ = "family_history"

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    patient_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation: Mapped[str] = mapped_column(String(20), nullable=False)
    condition: Mapped[str] = mapped_column(Text, nullable=False)
    condition_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    onset_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MenstrualReproductive(Base):
    """女性婚育/月经史 — 1:1(`patient_id` UNIQUE)。"""

    __tablename__ = "menstrual_reproductive"

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    patient_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    menarche_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cycle_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_menstrual_period: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_pregnant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    gravidity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_lactating: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    menopause_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExamReport(Base):
    """检查报告上传 — 1:N。info_collect ① Step 3 / report_parser 多模态消费。"""

    __tablename__ = "exam_reports"

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    patient_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    report_type: Mapped[str] = mapped_column(String(30), nullable=False)
    report_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_mime: Mapped[str | None] = mapped_column(String(50), nullable=True)
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    llm_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
