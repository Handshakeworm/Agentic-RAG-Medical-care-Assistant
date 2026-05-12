-- Migration: 0005_sessions_conversations
-- 建立 sessions + conversations 表(DEV_SPEC §2.4.3)
-- 受益对象:G4 问诊接口(每次 graph.invoke 完成后摘要写一行 sessions / conversations)

BEGIN;

CREATE TABLE IF NOT EXISTS sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    title       TEXT,
    status      VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 按用户查历史会话(降序时间)
CREATE INDEX IF NOT EXISTS idx_sessions_user
    ON sessions (user_id, created_at DESC);
-- 查活跃会话(partial index 节省空间)
CREATE INDEX IF NOT EXISTS idx_sessions_status_active
    ON sessions (status) WHERE status = 'active';


CREATE TABLE IF NOT EXISTS conversations (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID NOT NULL REFERENCES sessions(id),
    user_id      UUID NOT NULL REFERENCES users(id),
    user_input   TEXT NOT NULL,
    llm_output   TEXT NOT NULL,
    rag_context  JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 按会话查对话流(升序复现对话顺序)
CREATE INDEX IF NOT EXISTS idx_conversations_session
    ON conversations (session_id, created_at);
-- 按用户查历史(降序最新优先)
CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations (user_id, created_at DESC);

COMMIT;
