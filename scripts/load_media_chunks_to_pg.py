"""scripts/load_media_chunks_to_pg.py — 图表 chunk(table / figure)单行多列灌 PG。

DEV_SPEC §3.1.2(2026-05-12 修订)单行多列设计:每张 mineru 图/表 block 对应 chunks 表
**一行**,同时承载:
- `chunk_raw_text`:caption + html + footnote(table)/ caption + footnote(figure;
  mineru mermaid/markdown 质量差,**不写入**)→ BM25 sparse 来源
- `medical_statement`:LLM 100-300 字医学陈述 → dense `original` 向量来源
- `summary` / `hypothetical_questions`:enrichment 同生产
- `image_path` / `sub_type`:截图相对路径 / mineru 子类
- `content_hash`:SHA256(chunk_raw_text + "\n" + medical_statement),两路任一变即重 embed

# 输入

| 路径 | 内容 |
|---|---|
| `scripts/figure_extract_output/<book>.jsonl` | manifest:位置 + chunk_kind + 原文/截图/caption/heading |
| `scripts/figure_enrichment_output/<book>.jsonl` | vision LLM 跑出的 figure + chart 4 字段(status=ok / failed / duplicate_of_anchor) |
| `scripts/table_enrichment_output/<book>.jsonl` | text LLM 跑出的 table 4 字段(同上) |

按 `(book_dir, page_idx, block_idx)` 三键 join。

# 过滤(三条都满足才灌)

1. `manifest.heading_path_id is not None`(剔除 preface/参考文献区孤儿)
2. `manifest.merge_role != "merged"`(sibling 被 anchor 吸收,不独立入库)
3. enrichment 行 `status == "ok"`(跳过 duplicate_of_anchor / failed)

# 幂等

ON CONFLICT (chunk_id) DO UPDATE 覆盖所有内容字段;重跑同输入命中同一行。

# 用法

    python scripts/load_media_chunks_to_pg.py                  # 全 12 本
    python scripts/load_media_chunks_to_pg.py <book_dir>       # 单本
    python scripts/load_media_chunks_to_pg.py --dry-run <book> # 不写库,只打印汇总 + 头几条
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from load_chunks_to_pg import BOOK_TO_FILENAME  # noqa: E402
from src.db.postgres.models import bulk_upsert_chunks  # noqa: E402
from src.rag.ingestion.idempotency import (  # noqa: E402
    compute_chunk_id,
    compute_heading_path_id,
    compute_media_content_hash,
    compute_parent_chunk_id,
    compute_source_id,
)

MANIFEST_DIR = REPO_ROOT / "scripts" / "figure_extract_output"
FIGURE_ENRICHMENT_DIR = REPO_ROOT / "scripts" / "figure_enrichment_output"
TABLE_ENRICHMENT_DIR = REPO_ROOT / "scripts" / "table_enrichment_output"


# ─────────────────────────────────────────────────────────────────────
# 工具:读 jsonl 并按 (page_idx, block_idx) 建索引
# ─────────────────────────────────────────────────────────────────────


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _build_enrichment_index(book_dir: str) -> dict[tuple[int, int], dict]:
    """合并 figure + table 两个 enrichment 目录,按 (page_idx, block_idx) 建索引。

    每条 manifest 块只会出现在两个目录之一(由 chunk_kind 决定走 vision 还是 text LLM),
    所以 key 不会冲突。
    """
    out: dict[tuple[int, int], dict] = {}
    for d in (FIGURE_ENRICHMENT_DIR, TABLE_ENRICHMENT_DIR):
        for rec in _read_jsonl(d / f"{book_dir}.jsonl"):
            key = (rec["page_idx"], rec["block_idx"])
            out[key] = rec
    return out


# ─────────────────────────────────────────────────────────────────────
# 单条 manifest + enrichment → PG record
# ─────────────────────────────────────────────────────────────────────


def _build_chunk_raw_text(m: dict) -> str:
    """拼 chunk_raw_text。

    - table:caption + html + footnote(html 高质量保留)
    - figure(原 chart 或原 flowchart):caption + footnote(mineru 转录 mermaid/markdown
      质量差,**不写入**,见 §3.1.2)

    caption / footnote 是 list[str](mineru 原始字段);html 在 manifest['content']。
    """
    caption = m.get("caption") or []
    footnote = m.get("footnote") or []
    parts: list[str] = [*caption]
    if m.get("chunk_kind") == "table":
        # table 把 html 放在 caption 与 footnote 之间
        content = m.get("content") or ""
        if content:
            parts.append(content)
    parts.extend(footnote)
    return "\n".join(p for p in parts if p)


def _build_record(
    m: dict,
    e: dict,
    source_id: str,
) -> dict[str, Any]:
    """单条 manifest + enrichment → PG chunks 表一条 record。"""
    page_idx = m["page_idx"]
    block_idx = m["block_idx"]
    heading_path = m["heading_path"]
    # 注意:manifest 中的 heading_path_id 是 derive_figure_heading_paths.py 用简化公式
    # SHA256(heading_path) 算的,与 spec §3.1.4.2 不一致;此处忽略,改用 spec 公式
    # 从 heading_path 字符串重算,保证与 text load 阶段写入的 parent 行 chunk_id 对齐。
    titles = [t.strip() for t in heading_path.split(" > ") if t.strip()]
    heading_path_id = compute_heading_path_id(titles)

    # chunk_type:manifest chunk_kind 是 'table' / 'figure' / 'chart',
    # 'chart' 归并到 'figure'(spec §3.1.2)
    chunk_kind = m["chunk_kind"]
    if chunk_kind == "table":
        chunk_type = "table"
    elif chunk_kind in ("figure", "chart"):
        chunk_type = "figure"
    else:
        raise ValueError(f"未知 chunk_kind: {chunk_kind!r} (p{page_idx}#{block_idx})")

    rel_idx = f"{chunk_type}:p{page_idx}_b{block_idx}"
    cid = compute_chunk_id(source_id, heading_path_id, rel_idx)
    parent_cid = compute_parent_chunk_id(source_id, heading_path_id)

    chunk_raw_text = _build_chunk_raw_text(m)
    medical_statement = e["medical_statement"]

    return {
        "chunk_id": cid,
        "source_id": source_id,
        "heading_path_id": heading_path_id,
        "heading_path": heading_path,
        "relative_chunk_index": rel_idx,
        "parent_chunk_id": parent_cid,
        "chunk_type": chunk_type,
        "image_path": m.get("image_path"),
        "sub_type": m.get("mineru_sub_type"),
        "chunk_raw_text": chunk_raw_text,
        "medical_statement": medical_statement,
        "content_hash": compute_media_content_hash(chunk_raw_text, medical_statement),
        "title": e["title"],
        "summary": e["summary"],
        "hypothetical_questions": e["hypothetical_questions"],
        "embedding_status": "pending",
    }


# ─────────────────────────────────────────────────────────────────────
# 主流程:组装一本书的 records
# ─────────────────────────────────────────────────────────────────────


def build_records_for_book(book_dir: str) -> tuple[list[dict], dict[str, int]]:
    """返回 (records, stats)。records 直接喂 bulk_upsert_chunks。

    stats 统计:
    - n_manifest:manifest 原始记录数
    - n_orphan:heading_path_id is None 跳过数
    - n_merged_sibling:merge_role=="merged" 跳过数
    - n_no_enrichment:在 enrichment jsonl 里找不到对应行
    - n_enrichment_failed:enrichment.status != "ok" 跳过数
    - n_records:最终入库行数
    - by_type:{table: N, figure: M}
    """
    file_name = BOOK_TO_FILENAME[book_dir]
    source_id = compute_source_id(file_name)

    manifest = _read_jsonl(MANIFEST_DIR / f"{book_dir}.jsonl")
    enrichment_idx = _build_enrichment_index(book_dir)

    records: list[dict] = []
    n_orphan = 0
    n_merged_sibling = 0
    n_no_enrichment = 0
    n_enrichment_failed = 0
    by_type: Counter[str] = Counter()

    for m in manifest:
        if m.get("heading_path_id") is None:
            n_orphan += 1
            continue
        if m.get("merge_role") == "merged":
            n_merged_sibling += 1
            continue
        key = (m["page_idx"], m["block_idx"])
        e = enrichment_idx.get(key)
        if e is None:
            n_no_enrichment += 1
            continue
        if e.get("status") != "ok":
            n_enrichment_failed += 1
            continue
        rec = _build_record(m, e, source_id)
        records.append(rec)
        by_type[rec["chunk_type"]] += 1

    stats = {
        "n_manifest": len(manifest),
        "n_orphan": n_orphan,
        "n_merged_sibling": n_merged_sibling,
        "n_no_enrichment": n_no_enrichment,
        "n_enrichment_failed": n_enrichment_failed,
        "n_records": len(records),
        "source_id": source_id,
        **{f"by_type_{k}": v for k, v in by_type.items()},
    }
    return records, stats


def load_book(book_dir: str, dry_run: bool = False) -> dict[str, Any]:
    records, stats = build_records_for_book(book_dir)
    file_name = BOOK_TO_FILENAME[book_dir]

    if dry_run:
        print(f"[DRY] {book_dir}  file_name={file_name}  source_id={stats['source_id']}")
        print(
            f"  manifest={stats['n_manifest']}  "
            f"orphan={stats['n_orphan']}  merged_sibling={stats['n_merged_sibling']}  "
            f"no_enrichment={stats['n_no_enrichment']}  enrichment_failed={stats['n_enrichment_failed']}  "
            f"→ records={stats['n_records']}  "
            f"(table={stats.get('by_type_table', 0)} / figure={stats.get('by_type_figure', 0)})"
        )
        if records:
            r0 = records[0]
            print("  --- 第 1 条预览 ---")
            print(f"    chunk_id              = {r0['chunk_id']}")
            print(f"    chunk_type            = {r0['chunk_type']}")
            print(f"    relative_chunk_index  = {r0['relative_chunk_index']}")
            print(f"    heading_path          = {r0['heading_path']}")
            print(f"    image_path            = {r0['image_path']}")
            print(f"    sub_type              = {r0['sub_type']}")
            print(f"    chunk_raw_text[:120]  = {r0['chunk_raw_text'][:120]!r}")
            print(f"    medical_statement[:120]= {r0['medical_statement'][:120]!r}")
            print(f"    title                 = {r0['title']!r}")
            print(f"    hypothetical_questions= {r0['hypothetical_questions']}")
        return stats

    n = bulk_upsert_chunks(records)
    print(
        f"[OK] {book_dir}  upserted={n}  "
        f"(table={stats.get('by_type_table', 0)} / figure={stats.get('by_type_figure', 0)})  "
        f"skipped(orphan={stats['n_orphan']}, merged={stats['n_merged_sibling']}, "
        f"no_enrich={stats['n_no_enrichment']}, failed={stats['n_enrichment_failed']})"
    )
    return stats


def load_all(dry_run: bool = False) -> None:
    print(f'{"book":<45s} | manifest | records | table | figure | skipped')
    print("-" * 100)
    grand_records = 0
    grand_skipped = 0
    for book in BOOK_TO_FILENAME:
        try:
            s = load_book(book, dry_run=dry_run)
        except Exception as e:
            print(f"  {book:<43s} | ERROR: {type(e).__name__}: {e}")
            raise
        skipped = (
            s["n_orphan"] + s["n_merged_sibling"]
            + s["n_no_enrichment"] + s["n_enrichment_failed"]
        )
        print(
            f'  {book:<43s} | {s["n_manifest"]:8d} | {s["n_records"]:7d} | '
            f'{s.get("by_type_table", 0):5d} | {s.get("by_type_figure", 0):6d} | {skipped:7d}'
        )
        grand_records += s["n_records"]
        grand_skipped += skipped
    print("-" * 100)
    print(f"TOTAL records={grand_records}  skipped={grand_skipped}")


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("book_dir", nargs="?", help="POC 目录名;省略则跑全 12 本")
    p.add_argument("--dry-run", action="store_true", help="不写库,只打印汇总 + 头几条")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.book_dir:
        load_book(args.book_dir, dry_run=args.dry_run)
    else:
        load_all(dry_run=args.dry_run)
