-- Migration: 0003_chunks_media_columns
-- 单行多列设计落地(DEV_SPEC 2026-05-12 §3.1.2 修订)。
--
-- 改动:
-- 1. ADD COLUMN medical_statement TEXT — table / figure 行装 LLM 100-300 字医学陈述,
--    作为 dense `original` 向量来源(child / parent 此列 NULL)。
-- 2. DROP COLUMN linked_chunk_id — 原"源 chunk + summary chunk"双行架构的横向 FK,
--    单行设计后无需。索引一并删除。
-- 3. DROP COLUMN tags — 2026-05 决策废弃,enrichment 不再生成,已从 ChunkEnrichmentOutput
--    与 Milvus payload 全面移除(详见 §3.1.3.2 末尾决策块)。
-- 4. REPLACE INDEX idx_chunks_embedding_status WHERE 子句去掉 'bm25_only' —
--    单行设计下 table / figure 走标准 pending→done 生命周期,bm25_only 状态值废弃。
--
-- 依赖:0002_chunks.sql。
-- 数据安全:本迁移前 chunks 表只灌过 text parent/child(linked_chunk_id 全 NULL,
--    tags 全 NULL),DROP COLUMN 无信息丢失。

BEGIN;

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS medical_statement TEXT;

DROP INDEX IF EXISTS idx_chunks_linked_chunk_id;
ALTER TABLE chunks DROP COLUMN IF EXISTS linked_chunk_id;
ALTER TABLE chunks DROP COLUMN IF EXISTS tags;

DROP INDEX IF EXISTS idx_chunks_embedding_status;
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_status ON chunks (embedding_status)
    WHERE embedding_status NOT IN ('done', 'skip');

COMMIT;
