"""scripts/load_chunk_embeddings_to_milvus.py — C5 灌库:PG pending chunks → Milvus 多向量。

DEV_SPEC §3.1.5。每条 PG chunk(child / table / figure)产 1 original + 0-1 summary +
0-3 question 条 Milvus 记录;跑完把 chunks.embedding_status 翻成 'done'(parent 永远 'skip')。

# 输入

PG `chunks` 表中 `embedding_status='pending'` 的行(目前 child 22287 + table 2744 + figure 1023)。

# 流程

1. 按 source_id 顺序遍历(stable);每个 source 内按 chunk_id 升序分批(默认 100 chunks/批)
2. 一批 chunks → C5 `build_milvus_records()` 展开为 ~500 条向量记录(GPU encode 一次)
3. Milvus upsert(同 id 自动覆盖,幂等)
4. PG UPDATE: pending → done(同 batch chunk_id 一次性翻)
5. 异常处理:任一步抛错 → 整批标 failed,后续可手动重置回 pending 重试

# 幂等 & 断点续传

- Milvus upsert:基于确定性 id `{chunk_id}` / `{chunk_id}_summary` / `{chunk_id}_q{n}`,重跑覆盖
- PG status:done 后下次 SELECT 不再扫到;中途崩溃最坏多 embed 一批,不会双写

# 用法

    python scripts/load_chunk_embeddings_to_milvus.py                    # 全 12 本
    python scripts/load_chunk_embeddings_to_milvus.py --source-id <id>   # 单本
    python scripts/load_chunk_embeddings_to_milvus.py --limit 100        # 小批冒烟
    python scripts/load_chunk_embeddings_to_milvus.py --dry-run          # 只跑 encode,不写 Milvus / PG
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from src.db.milvus.docs_collection import ensure_docs_collection, upsert_chunks  # noqa: E402
from src.db.postgres.connection import session_scope  # noqa: E402
from src.rag.ingestion.embedding import build_milvus_records  # noqa: E402


# 一个 PG batch 内的 chunk 行数;~5 倍展开后约 500 条向量记录,8B INT8 batch=16 单次 GPU encode 可吃下
DEFAULT_BATCH_CHUNKS = 100


# ─────────────────────────────────────────────────────────────────────
# 读取 / 写入 PG
# ─────────────────────────────────────────────────────────────────────


def _fetch_pending_batch(session, source_id: str | None, limit: int) -> list[dict[str, Any]]:
    """拉一批 pending chunks(parent 不会出现,因为它们是 skip 状态)。"""
    sql = """
        SELECT chunk_id, source_id, chunk_type, chunk_raw_text, medical_statement,
               title, summary, hypothetical_questions
        FROM chunks
        WHERE embedding_status = 'pending'
        {source_filter}
        ORDER BY source_id, chunk_id
        LIMIT :limit
    """.format(source_filter="AND source_id = :source_id" if source_id else "")
    params: dict[str, Any] = {"limit": limit}
    if source_id:
        params["source_id"] = source_id
    rows = session.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def _mark_status(session, chunk_ids: list[str], status: str) -> None:
    session.execute(
        text(
            "UPDATE chunks SET embedding_status = :status, updated_at = now() "
            "WHERE chunk_id = ANY(:ids)"
        ),
        {"status": status, "ids": chunk_ids},
    )


def _count_pending(session, source_id: str | None) -> int:
    sql = "SELECT count(*) FROM chunks WHERE embedding_status='pending'"
    params: dict[str, Any] = {}
    if source_id:
        sql += " AND source_id = :source_id"
        params["source_id"] = source_id
    return session.execute(text(sql), params).scalar_one()


# ─────────────────────────────────────────────────────────────────────
# 主循环
# ─────────────────────────────────────────────────────────────────────


def run(
    source_id: str | None,
    batch_chunks: int,
    limit: int | None,
    dry_run: bool,
) -> dict[str, int]:
    if not dry_run:
        ensure_docs_collection()

    with session_scope() as session:
        total_pending = _count_pending(session, source_id)
    print(f"[start] pending chunks: {total_pending}"
          f"{f'  (--source-id={source_id})' if source_id else ''}"
          f"{f'  (--limit={limit})' if limit else ''}"
          f"{'  [DRY-RUN]' if dry_run else ''}")

    stats = {"chunks_done": 0, "chunks_failed": 0, "vectors": 0, "batches": 0}
    t0 = time.time()
    remaining_budget = limit  # None 表示无上限

    while True:
        # 决定本批拉多少:取 batch_chunks 与剩余 budget 的较小值
        this_batch = batch_chunks
        if remaining_budget is not None:
            if remaining_budget <= 0:
                break
            this_batch = min(this_batch, remaining_budget)

        with session_scope() as session:
            chunks = _fetch_pending_batch(session, source_id, this_batch)

        if not chunks:
            break

        chunk_ids = [c["chunk_id"] for c in chunks]
        batch_vectors = 0
        try:
            records = build_milvus_records(chunks)
            batch_vectors = len(records)
            if not dry_run:
                upsert_chunks(records)
            with session_scope() as session:
                _mark_status(session, chunk_ids, "done" if not dry_run else "pending")
            stats["chunks_done"] += len(chunks)
            stats["vectors"] += batch_vectors
        except Exception as e:  # noqa: BLE001
            print(f"  [FAIL] batch {stats['batches'] + 1} ({len(chunks)} chunks): {e!r}")
            with session_scope() as session:
                _mark_status(session, chunk_ids, "failed")
            stats["chunks_failed"] += len(chunks)
            # 失败这批不算 vectors;继续下一批,避免单点阻塞
        stats["batches"] += 1

        elapsed = time.time() - t0
        rate = stats["chunks_done"] / elapsed if elapsed > 0 else 0
        eta_s = (total_pending - stats["chunks_done"]) / rate if rate > 0 else 0
        print(
            f"  [batch {stats['batches']:>4}] chunks={len(chunks):>3}  "
            f"vectors+={batch_vectors:>4}  "
            f"done={stats['chunks_done']:>5}/{total_pending}  "
            f"failed={stats['chunks_failed']:>3}  "
            f"rate={rate:.1f} chunks/s  eta={eta_s / 60:.1f} min"
        )

        if remaining_budget is not None:
            remaining_budget -= len(chunks)

    print(
        f"\n[done] chunks_done={stats['chunks_done']}  "
        f"chunks_failed={stats['chunks_failed']}  vectors={stats['vectors']}  "
        f"elapsed={(time.time() - t0) / 60:.1f} min"
    )
    return stats


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--source-id", help="只灌指定 source_id(默认全表 pending)")
    p.add_argument(
        "--batch-chunks", type=int, default=DEFAULT_BATCH_CHUNKS,
        help=f"一批 PG chunks 数(默认 {DEFAULT_BATCH_CHUNKS},约展开为 5x 条向量)",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="最多处理多少 chunks(冒烟用,默认无上限)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="跑 GPU encode 但不写 Milvus / 不翻 PG status",
    )
    return p.parse_args()


def main():
    args = _parse_args()
    run(
        source_id=args.source_id,
        batch_chunks=args.batch_chunks,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
