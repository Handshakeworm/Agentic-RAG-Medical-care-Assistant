-- Migration: 0004_users_patients
-- 建立 users + patients + 8 张患者历史表(DEV_SPEC §2.4.5)
-- 受益对象:G2 (auth) / G5 (patient CRUD) / Agent ⑪ safety_gate(读 allergies)

BEGIN;

-- pgcrypto 提供 gen_random_uuid();PG 13+ 自带 pg_uuid_generate_v4 也行,
-- 这里用 pgcrypto 的 gen_random_uuid 因 ORM server_default 写的就是这个名字
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ── 账号系统(§2.4.5)──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT UNIQUE NOT NULL,
    password    TEXT NOT NULL,
    role        VARCHAR(20) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── 患者基本信息 + 个人史(§2.4.5)─────────────────────────────────────
CREATE TABLE IF NOT EXISTS patients (
    id                      UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    name                    TEXT,
    gender                  VARCHAR(10),
    birth_date              DATE,
    blood_type              VARCHAR(20),
    height_cm               INTEGER,
    weight_kg               NUMERIC(5,1),
    phone                   TEXT,
    emergency_contact       TEXT,
    smoking_status          VARCHAR(20),
    smoking_pack_years      NUMERIC(5,1),
    alcohol_status          VARCHAR(20),
    alcohol_detail          TEXT,
    occupation              TEXT,
    occupational_exposure   TEXT,
    travel_history          TEXT,
    infectious_contact      TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── 既往病史 ⚠️必问(§2.4.5)──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS medical_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    category        VARCHAR(20) NOT NULL,
    condition       TEXT NOT NULL,
    icd10_code      VARCHAR(10),
    diagnosed_at    DATE,
    resolved_at     DATE,
    control_status  VARCHAR(20),
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_medical_history_patient ON medical_history (patient_id);


-- ── 手术与外伤史 ⚠️必问 ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS surgical_trauma_history (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id          UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    type                VARCHAR(10) NOT NULL,
    name                TEXT NOT NULL,
    occurred_at         DATE,
    hospital            TEXT,
    has_complications   BOOLEAN NOT NULL DEFAULT FALSE,
    complications       TEXT,
    sequelae            TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_surgical_trauma_history_patient ON surgical_trauma_history (patient_id);


-- ── 输血史 ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transfusion_history (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id         UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    transfusion_date   DATE,
    blood_product      VARCHAR(30),
    reason             TEXT,
    adverse_reaction   BOOLEAN NOT NULL DEFAULT FALSE,
    reaction_detail    TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_transfusion_history_patient ON transfusion_history (patient_id);


-- ── 过敏史 ⚠️安全底线 ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS allergies (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    allergen        TEXT NOT NULL,
    allergen_type   VARCHAR(20),
    reaction        TEXT,
    reaction_type   VARCHAR(30),
    severity        VARCHAR(20),
    status          VARCHAR(20) NOT NULL DEFAULT 'suspected',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_allergies_patient ON allergies (patient_id);


-- ── 用药史 ⚠️必问 ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS medications (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id           UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    drug_name            TEXT NOT NULL,
    drug_category        VARCHAR(30),
    dosage               TEXT,
    frequency            TEXT,
    route                VARCHAR(20),
    started_at           DATE,
    ended_at             DATE,
    prescribed_by        TEXT,
    is_self_medication   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_medications_patient ON medications (patient_id);
-- 当前用药专用索引(过滤未停用):safety_gate ⑪ 拉"当前用药列表"用
CREATE INDEX IF NOT EXISTS idx_medications_active
    ON medications (patient_id) WHERE ended_at IS NULL;


-- ── 家族史 ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS family_history (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id          UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    relation            VARCHAR(20) NOT NULL,
    condition           TEXT NOT NULL,
    condition_category  VARCHAR(30),
    onset_age           INTEGER,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_family_history_patient ON family_history (patient_id);


-- ── 女性婚育/月经史(1:1)──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS menstrual_reproductive (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id              UUID NOT NULL UNIQUE REFERENCES patients(id) ON DELETE CASCADE,
    menarche_age            INTEGER,
    cycle_days              INTEGER,
    period_days             INTEGER,
    last_menstrual_period   DATE,
    is_pregnant             BOOLEAN,
    gravidity               INTEGER,
    parity                  INTEGER,
    is_lactating            BOOLEAN,
    menopause_age           INTEGER,
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── 检查报告(1:N)──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS exam_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    report_type     VARCHAR(30) NOT NULL,
    report_name     TEXT,
    file_path       TEXT,
    file_mime       VARCHAR(50),
    report_date     DATE,
    llm_summary     TEXT,
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_exam_reports_patient
    ON exam_reports (patient_id, uploaded_at DESC);

COMMIT;
