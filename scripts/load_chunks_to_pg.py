"""scripts/load_chunks_to_pg.py — derive 输出 → PG chunks 表幂等灌入。

把 12 本教材 POC chunking 结果(`derive_chunks_for_pg.derive_for_book`)按
DEV_SPEC §2.4.2 的 19 列 chunks schema 组装,并通过 `bulk_upsert_chunks`
幂等写入 PostgreSQL。

字段派生(spec §3.1.4):
- chunk_id            子块: SHA256(source_id : heading_path_id : str(idx))
                      父块: SHA256(source_id : heading_path_id : "parent")
- heading_path_id     SHA256(join(":", [hash(normalize(H_i))]))
                      heading_path 按 " > " split 拆回各级标题序列
- relative_chunk_index 子块: "0/1/2..."(同 parent 内递增);父块: "parent"
- chunk_type           parent / child(本批仅文本块,无 table/chart)
- chunk_raw_text       POC 输出的 text 字段(blocks 用 "\n\n" 拼接)
- content_hash         SHA256(chunk_raw_text)
- embedding_status     父块: "skip";子块: "pending"
- linked_chunk_id / image_path / sub_type / title / summary / tags /
  hypothetical_questions:本批一律 NULL(表/图/enrichment 后续任务填充)

幂等性:重跑同本(POC 切分稳定 → chunk_id 稳定)→ ON CONFLICT 覆盖。
NOTE 本脚本**不**做僵尸清理(spec §3.1.4.3 旧集合 - 新集合 删除),首次灌入
chunks 表为空,无僵尸;后续重灌前如需清理,另起脚本。

用法:
    python scripts/load_chunks_to_pg.py                  # 全 12 本
    python scripts/load_chunks_to_pg.py <book_dir>       # 单本
    python scripts/load_chunks_to_pg.py --dry-run <book> # 不写 DB,仅打印 head
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from derive_chunks_for_pg import ALL_BOOKS, derive_for_book  # noqa: E402
from src.db.postgres.models import bulk_upsert_chunks  # noqa: E402
from src.rag.ingestion.idempotency import (  # noqa: E402
    compute_chunk_id,
    compute_content_hash,
    compute_heading_path_id,
    compute_parent_chunk_id,
    compute_source_id,
)

# ─────────────────────────────────────────────────────────────────────
# POC 目录 → sources.file_name 显式映射(以 sources 表现有 13 条为准)
# 用药指南本批跳过(MEMORY: project_drug_reference_deferred)。
# ─────────────────────────────────────────────────────────────────────

BOOK_TO_FILENAME: dict[str, str] = {
    "poc_chunking_诊断学_第10版": "诊断学 第10版.pdf",
    "poc_chunking_内分泌代谢病学_第4版上册": "内分泌代谢病学 第4版上册.pdf",
    "poc_chunking_心血管内科学_第3版": "心血管内科学 第3版.pdf",
    "poc_chunking_协和呼吸病学_第二版": "协和呼吸病学(第二版)  高清版.pdf",
    "poc_chunking_内科学_第9版": "内科学 第9版_葛均波、徐永健、王辰主编2018年（可复制文字）.pdf",
    "poc_chunking_神经内科学": "神经内科学 第2版.pdf",
    "poc_chunking_神经外科学": "神经外科学.pdf",
    "poc_chunking_消化系统与疾病_第2版": "消化系统与疾病 第2版.pdf",
    "poc_chunking_胸心外科": "胸心外科.pdf",
    "poc_chunking_普通外科": "普通外科.pdf",
    "poc_chunking_骨科": "骨科.pdf",
    "poc_chunking_泌尿外科": "泌尿外科.pdf",
}


# ─────────────────────────────────────────────────────────────────────
# 组装一本书的 chunk records
# ─────────────────────────────────────────────────────────────────────


def build_records_for_book(book_dir: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """derive 输出 → 19 列 chunk records 列表。

    返回 (records, stats)。records 含父块 + 子块,可直接喂 `bulk_upsert_chunks`。
    """
    file_name = BOOK_TO_FILENAME[book_dir]
    source_id = compute_source_id(file_name)

    derived = derive_for_book(book_dir)
    parents = derived["parents"]
    children = derived["children"]

    # 子块按 parent_idx 分组,在组内从 0 起递增 → relative_chunk_index
    children_by_parent: dict[int, list[dict]] = defaultdict(list)
    for c in children:
        children_by_parent[c["parent_idx"]].append(c)

    records: list[dict[str, Any]] = []
    parent_chunk_id_by_idx: dict[int, str] = {}

    for p in parents:
        titles = [t.strip() for t in p["heading_path"].split(" > ") if t.strip()]
        hpid = compute_heading_path_id(titles)
        cid = compute_parent_chunk_id(source_id, hpid)
        parent_chunk_id_by_idx[p["parent_idx"]] = cid

        records.append({
            "chunk_id": cid,
            "source_id": source_id,
            "heading_path_id": hpid,
            "heading_path": p["heading_path"],
            "relative_chunk_index": "parent",
            "parent_chunk_id": None,
            "chunk_type": "parent",
            "linked_chunk_id": None,
            "image_path": None,
            "sub_type": None,
            "chunk_raw_text": p["text"],
            "content_hash": compute_content_hash(p["text"]),
            "title": None,
            "summary": None,
            "tags": None,
            "hypothetical_questions": None,
            "embedding_status": "skip",
        })

    for p in parents:
        titles = [t.strip() for t in p["heading_path"].split(" > ") if t.strip()]
        hpid = compute_heading_path_id(titles)
        parent_cid = parent_chunk_id_by_idx[p["parent_idx"]]

        for idx, c in enumerate(children_by_parent[p["parent_idx"]]):
            rel_idx = str(idx)
            cid = compute_chunk_id(source_id, hpid, rel_idx)
            records.append({
                "chunk_id": cid,
                "source_id": source_id,
                "heading_path_id": hpid,
                "heading_path": p["heading_path"],
                "relative_chunk_index": rel_idx,
                "parent_chunk_id": parent_cid,
                "chunk_type": "child",
                "linked_chunk_id": None,
                "image_path": None,
                "sub_type": None,
                "chunk_raw_text": c["text"],
                "content_hash": compute_content_hash(c["text"]),
                "title": None,
                "summary": None,
                "tags": None,
                "hypothetical_questions": None,
                "embedding_status": "pending",
            })

    stats = {
        "n_parents": len(parents),
        "n_children": len(children),
        "n_records": len(records),
        "source_id": source_id,
    }
    return records, stats


# ─────────────────────────────────────────────────────────────────────
# 入库主流程
# ─────────────────────────────────────────────────────────────────────


def load_book(book_dir: str, dry_run: bool = False) -> dict[str, Any]:
    """组装一本书 records 并 upsert 到 PG(dry_run=True 仅打印,不写库)。"""
    records, stats = build_records_for_book(book_dir)
    file_name = BOOK_TO_FILENAME[book_dir]

    if dry_run:
        print(f"[DRY RUN] {book_dir} → file_name={file_name}  source_id={stats['source_id']}")
        print(f"  parents={stats['n_parents']}  children={stats['n_children']}  total={stats['n_records']}")
        print("  --- 第 1 条父块预览 ---")
        p = records[0]
        print(f"    chunk_id={p['chunk_id']}")
        print(f"    heading_path={p['heading_path']}")
        print(f"    heading_path_id={p['heading_path_id']}")
        print(f"    text[:120]={p['chunk_raw_text'][:120]!r}")
        # 找到一个子块预览
        ch = next((r for r in records if r["chunk_type"] == "child"), None)
        if ch:
            print("  --- 第 1 条子块预览 ---")
            print(f"    chunk_id={ch['chunk_id']}")
            print(f"    relative_chunk_index={ch['relative_chunk_index']}  parent={ch['parent_chunk_id']}")
            print(f"    text[:120]={ch['chunk_raw_text'][:120]!r}")
        return stats

    n = bulk_upsert_chunks(records)
    print(f"[OK] {book_dir}: upserted {n} rows  (parents={stats['n_parents']}, children={stats['n_children']})")
    return stats


def load_all() -> None:
    print(f'{"book":<45s} | n_p   | n_c   | total')
    print("-" * 80)
    grand = 0
    for book in ALL_BOOKS:
        try:
            s = load_book(book, dry_run=False)
            print(f'  {book:<43s} | {s["n_parents"]:5d} | {s["n_children"]:5d} | {s["n_records"]:5d}')
            grand += s["n_records"]
        except Exception as e:
            print(f"  {book:<43s} | ERROR: {type(e).__name__}: {e}")
            raise
    print("-" * 80)
    print(f"TOTAL upserted: {grand}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--dry-run" in args:
        args.remove("--dry-run")
        if not args:
            print("Usage: --dry-run <book_dir>", file=sys.stderr)
            sys.exit(2)
        load_book(args[0], dry_run=True)
    elif args:
        load_book(args[0], dry_run=False)
    else:
        load_all()
