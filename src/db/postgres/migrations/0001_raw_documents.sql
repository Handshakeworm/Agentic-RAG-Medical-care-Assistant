-- Migration: 0001_raw_documents
-- 建立 sources 表(来源文档注册,DEV_SPEC 2.4.2)与 raw_documents 表(MinerU 解析产物,DEV_SPEC 2.4.4)

BEGIN;

CREATE TABLE IF NOT EXISTS sources (
    source_id    TEXT PRIMARY KEY,
    file_name    TEXT NOT NULL,
    file_path    TEXT,
    doc_type     VARCHAR(50),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_documents (
    source_id        TEXT PRIMARY KEY REFERENCES sources(source_id) ON DELETE CASCADE,
    file_name        TEXT NOT NULL,
    stored_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    markdown_content TEXT NOT NULL,
    content_list     JSONB NOT NULL,
    middle_data      JSONB,
    model_data       JSONB,

    pdf_path         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_documents_content_list_gin
    ON raw_documents USING GIN (content_list);

COMMIT;
