"""scripts/load_enrichment_to_pg.py — 读 enrichment_output/*.jsonl 灌 PG chunks 表。

`enrichment.py` 的 LLM 产物落 `scripts/enrichment_output/<book_dir>.jsonl`,
本脚本扫这些 jsonl,把 status='ok' 的条按 chunk_id 批量 UPDATE 到
`chunks` 表的 4 个 enrichment 字段(title / summary / tags / hypothetical_questions)。

幂等:UPDATE 按 chunk_id 主键定位,重灌覆盖原值。status='failed' 的跳过(留 NULL)。

用法:
    python scripts/load_enrichment_to_pg.py                              # 扫所有 jsonl
    python scripts/load_enrichment_to_pg.py poc_chunking_诊断学_第10版    # 单本
    python scripts/load_enrichment_to_pg.py --dry-run <book>             # 不写库,统计
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from sqlalchemy import bindparam, text  # noqa: E402

from src.db.postgres.connection import session_scope  # noqa: E402

OUTPUT_DIR = REPO_ROOT / "scripts" / "enrichment_output"
BATCH_SIZE = 500  # 每批 UPDATE 条数


def _read_jsonl(p: Path) -> tuple[list[dict[str, Any]], int, int]:
    """读 jsonl,返回 (ok_records, n_failed, n_bad_lines)。"""
    ok_records: list[dict[str, Any]] = []
    n_failed = 0
    n_bad = 0
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                n_bad += 1
                continue
            if rec.get("status") == "ok":
                ok_records.append(rec)
            else:
                n_failed += 1
    return ok_records, n_failed, n_bad


def _bulk_update(records: list[dict[str, Any]]) -> int:
    """按 chunk_id 批量 UPDATE 4 字段。返回写入条数。"""
    if not records:
        return 0
    sql = text(
        "UPDATE chunks SET "
        "  title=:title, summary=:summary, tags=:tags, "
        "  hypothetical_questions=:hq, updated_at=now() "
        "WHERE chunk_id=:chunk_id"
    ).bindparams(bindparam("tags"), bindparam("hq"))
    payload = [
        {
            "chunk_id": r["chunk_id"],
            "title": r["title"],
            "summary": r["summary"],
            "tags": r["tags"],
            "hq": r["hypothetical_questions"],
        }
        for r in records
    ]
    n = 0
    with session_scope() as s:
        for i in range(0, len(payload), BATCH_SIZE):
            batch = payload[i : i + BATCH_SIZE]
            s.execute(sql, batch)
            n += len(batch)
    return n


def load_book(book_dir: str, dry_run: bool = False) -> dict[str, int]:
    p = OUTPUT_DIR / f"{book_dir}.jsonl"
    if not p.exists():
        print(f"[SKIP] {book_dir}: 无 jsonl 文件")
        return {"book": book_dir, "ok": 0, "failed": 0, "bad": 0, "updated": 0}
    ok, n_failed, n_bad = _read_jsonl(p)
    if dry_run:
        print(
            f"[DRY] {book_dir}: ok={len(ok)}  failed={n_failed}  bad_lines={n_bad}  "
            f"(将更新 {len(ok)} 行)"
        )
        return {"book": book_dir, "ok": len(ok), "failed": n_failed, "bad": n_bad, "updated": 0}
    n_updated = _bulk_update(ok)
    print(
        f"[OK] {book_dir}: 灌入 {n_updated} 行  "
        f"(jsonl: ok={len(ok)}  failed={n_failed}  bad_lines={n_bad})"
    )
    return {"book": book_dir, "ok": len(ok), "failed": n_failed, "bad": n_bad, "updated": n_updated}


def load_all(dry_run: bool = False) -> None:
    files = sorted(OUTPUT_DIR.glob("*.jsonl"))
    if not files:
        print(f"未找到任何 jsonl(目录:{OUTPUT_DIR})")
        return
    grand = {"ok": 0, "failed": 0, "updated": 0}
    for p in files:
        s = load_book(p.stem, dry_run=dry_run)
        grand["ok"] += s["ok"]
        grand["failed"] += s["failed"]
        grand["updated"] += s["updated"]
    print(
        f"--- 汇总 ---  files={len(files)}  ok_rows={grand['ok']}  "
        f"failed_rows={grand['failed']}  updated={grand['updated']}"
    )


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("book_dir", nargs="?", help="POC 目录名;省略则扫所有 jsonl")
    p.add_argument("--dry-run", action="store_true", help="不写库,只打印计数")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.book_dir:
        load_book(args.book_dir, dry_run=args.dry_run)
    else:
        load_all(dry_run=args.dry_run)
