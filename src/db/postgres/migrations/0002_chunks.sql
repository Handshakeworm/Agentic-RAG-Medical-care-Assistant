-- Migration: 0002_chunks
-- 建立 chunks 表(chunk 元数据核心表,DEV_SPEC §2.4.2)。
-- 依赖:0001_raw_documents.sql(sources 表必须先建,FK 引用)。

BEGIN;

CREATE TABLE IF NOT EXISTS chunks (
    -- 幂等性字段(见 §3.1.4)
    chunk_id              TEXT PRIMARY KEY,
    source_id             TEXT NOT NULL REFERENCES sources(source_id),
    heading_path_id       TEXT NOT NULL,
    heading_path          TEXT NOT NULL,
    relative_chunk_index  INT  NOT NULL,
    parent_chunk_id       TEXT REFERENCES chunks(chunk_id),
    chunk_raw_text        TEXT NOT NULL,
    content_hash          TEXT NOT NULL,

    -- LLM 增强字段(见 §3.1.3)
    title                  TEXT,
    summary                TEXT,
    tags                   TEXT[],
    hypothetical_questions TEXT[],

    -- 运维状态字段
    embedding_status      VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chunks_source_id        ON chunks (source_id);
CREATE INDEX IF NOT EXISTS idx_chunks_heading_path_id  ON chunks (heading_path_id);
CREATE INDEX IF NOT EXISTS idx_chunks_content_hash     ON chunks (content_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_status ON chunks (embedding_status)
    WHERE embedding_status != 'done';
CREATE INDEX IF NOT EXISTS idx_chunks_parent_chunk_id  ON chunks (parent_chunk_id)
    WHERE parent_chunk_id IS NOT NULL;

COMMIT;
