-- Migration: 0006_audit_config
-- 建立 4 张审计表 + system_config 动态配置表
-- (DEV_SPEC §5.2.3.1~§5.2.3.4 + §5.3)
-- 受益对象:G4 写 rag_trace / G6 写 kb_change_log + config_change_log /
--          I 阶段离线评估读 diagnosis_feedback / 全链路读 system_config

BEGIN;


-- ── §5.2.3.1 rag_trace — per-session 链路追踪(15 字段 + 3 索引)─────
CREATE TABLE IF NOT EXISTS rag_trace (
    trace_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        UUID NOT NULL REFERENCES sessions(id),
    user_id           UUID NOT NULL REFERENCES users(id),
    raw_query         TEXT NOT NULL,
    intent_result     JSONB,
    retrieved_chunks  JSONB,
    reranked_chunks   JSONB,
    final_prompt      TEXT,
    llm_raw_output    TEXT,
    final_response    TEXT NOT NULL,
    model_name        VARCHAR(64) NOT NULL,
    token_usage       JSONB,
    latency_ms        JSONB,
    error_info        JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rag_trace_session
    ON rag_trace (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rag_trace_user
    ON rag_trace (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rag_trace_created
    ON rag_trace (created_at);


-- ── §5.2.3.2 kb_change_log — 知识库变更 ────────────────────────────
CREATE TABLE IF NOT EXISTS kb_change_log (
    change_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id      UUID NOT NULL REFERENCES users(id),
    operation        VARCHAR(32) NOT NULL,
    source_id        VARCHAR(255) NOT NULL,
    source_name      VARCHAR(255),
    prev_version     VARCHAR(64),
    new_version      VARCHAR(64),
    chunk_strategy   JSONB,
    affected_chunks  INTEGER,
    change_summary   TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_change_source
    ON kb_change_log (source_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kb_change_operator
    ON kb_change_log (operator_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kb_change_created
    ON kb_change_log (created_at);


-- ── §5.2.3.3 config_change_log — 配置变更 ──────────────────────────
CREATE TABLE IF NOT EXISTS config_change_log (
    change_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id    UUID NOT NULL REFERENCES users(id),
    config_key     VARCHAR(255) NOT NULL,
    old_value      JSONB,
    new_value      JSONB,
    change_reason  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_config_change_key
    ON config_change_log (config_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_config_change_created
    ON config_change_log (created_at);


-- ── §5.2.3.4 diagnosis_feedback — 反馈与标注 ────────────────────────
CREATE TABLE IF NOT EXISTS diagnosis_feedback (
    feedback_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id           UUID NOT NULL REFERENCES rag_trace(trace_id),
    reviewer_id        UUID NOT NULL REFERENCES users(id),
    rating             VARCHAR(32) NOT NULL,
    rating_details     JSONB,
    comment            TEXT,
    expected_response  TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_feedback_trace
    ON diagnosis_feedback (trace_id);
CREATE INDEX IF NOT EXISTS idx_feedback_rating
    ON diagnosis_feedback (rating, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_created
    ON diagnosis_feedback (created_at);


-- ── §5.3 system_config — 动态配置(运营软参)────────────────────────
CREATE TABLE IF NOT EXISTS system_config (
    key_name     VARCHAR(255) PRIMARY KEY,
    value        JSONB,
    value_type   VARCHAR(32),
    description  TEXT,
    updated_by   UUID REFERENCES users(id),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
