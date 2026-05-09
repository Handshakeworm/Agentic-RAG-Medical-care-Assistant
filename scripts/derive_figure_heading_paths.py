"""scripts/derive_figure_heading_paths.py — 给图表 manifest 关联 heading_path。

复用 derive_chunks_for_pg.derive_for_book(拿 derived_parents.heading_path,带
PATCH_HEADING_PATH_OVERRIDES 修补);用 POC parent 的 (pg_start, head) 在 flat 里
反查每个 parent 的起始 flat_idx,把每条 figure manifest 按 (page_idx, block_idx)
归属到覆盖它的 parent,继承其 heading_path。

# 为什么不复刻 POC 主循环

12 本 POC 各有书本特化(诊断学用 `RE_NUM_CN_DUN` 一、二、 + `PARENT_REFINE_THRESHOLD`
6000;内分泌用三遍切 `RE_BRACE/RE_PAREN_CN/RE_NUM_DOT` + `PARENT_PASS3_THRESHOLD`)。
复刻外层主循环必然 drift(实测诊断学 330 vs 300 个 parent),所以改用"反查"策略:
直接调 POC `chunk_book()` 拿其权威 parents 输出,通过 parent.head 字段(parent 第一个
block 的前 60 字)在 flat 里精确定位起点。POC 内部怎么切随它,我们只接受输出。

"在 derive 层 patch 不动 POC"是项目惯例(见 derive_chunks_for_pg.py 的
PATCH_HEADING_PATH_OVERRIDES)。本脚本同样不改 POC。

# 写回 manifest 的 2 个新字段

- `heading_path`(str | None):图表所属节的 heading 路径,跟同 heading 下 parent/child 共享
- `heading_path_id`(str | None):SHA256(heading_path) 的 hex,跟 chunks 表对齐

孤儿场景(heading_path=None):
- preface 区(第一个 section 之前的封面/版权页区间)
- 参考文献区(被 _find_ref_idx 截断后的区间)
- 这两类落 PG 时丢弃,不入图表 chunk

用法:
    python scripts/derive_figure_heading_paths.py <book_dir>             # 单本写回
    python scripts/derive_figure_heading_paths.py <book_dir> --dry-run   # 只验证不写回
    python scripts/derive_figure_heading_paths.py --all                  # 全 12 本
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from derive_chunks_for_pg import _load_poc, derive_for_book  # noqa: E402
from load_chunks_to_pg import BOOK_TO_FILENAME  # noqa: E402

OUT = REPO_ROOT / "scripts" / "figure_extract_output"


# ─────────────────────────────────────────────────────────────────────
# 用 POC parent 字段在 flat 里反查每个 parent 的起始 flat_idx
# (复用 derive_for_book 的权威 parents 输出,不依赖任何 POC 内部常量)
# ─────────────────────────────────────────────────────────────────────


def _build_parent_flat_ranges(M, parents: list[dict]) -> tuple[list[dict], list[dict]]:
    """用 (pg_start, head 前缀) 在 flat 里反查每个 parent 的起始 flat_idx。

    parents 来自 POC chunk_book() 的权威输出(由 derive_for_book 转交,带 heading_path)。
    跨书统一接口字段(parent_idx / pg_start / head / blocks / ...);本函数不调用任何
    POC 内部私有切分函数(_split_big_parent / _merge_tiny_parents 等)和常量
    (PARENT_SPLIT_THRESHOLD 等),因此对 12 本各自的书本特化策略零依赖。

    匹配规则:
      - 在 [last_match_idx + 1, len(flat)) 范围内找第一个满足
        (b.pg == parent.pg_start) AND (b.text 以 parent.head 前缀开头) 的 block
      - search_from 单调推进,避免同 head 撞名错位(如普外"6. 脾动脉结扎术"双 emit)

    返回 (parent_ranges, flat)。flat 含 image/table/chart 全部 block(text 为空)。
    """
    result = M.build_toc_dict()
    data = json.loads(Path(M.CONTENT_LIST_V2).read_text())
    body_start = max(result["toc_pages"]) + 1
    flat_full = M._flatten_blocks(data, body_start)
    body_end = M._find_body_end(flat_full)
    flat = flat_full[:body_end]

    parent_starts: list[int] = []
    search_from = 0
    for p in parents:
        head = p["head"].strip().replace("\n", " ")
        head_prefix = head[: min(30, len(head))]
        found_idx = None
        if head_prefix:
            # 主路径:用 (pg_start, head 前缀) 精确匹配
            for i in range(search_from, len(flat)):
                b = flat[i]
                if b["pg"] != p["pg_start"]:
                    continue
                if not b["text"].strip():
                    continue
                t = b["text"].strip().replace("\n", " ")
                if t.startswith(head_prefix):
                    found_idx = i
                    break
        else:
            # fallback:head 空(parent_blocks[0] 是空 text block,实测 ~1/533)
            # 在 search_from 之后找 pg >= parent.pg_start 的第一个非空 text block
            for i in range(search_from, len(flat)):
                b = flat[i]
                if b["pg"] < p["pg_start"]:
                    continue
                if b["text"].strip():
                    found_idx = i
                    break
        if found_idx is None:
            raise RuntimeError(
                f"parent_idx={p['parent_idx']} 在 flat 里找不到起点:"
                f"pg={p['pg_start']}  head={head_prefix!r}"
            )
        parent_starts.append(found_idx)
        search_from = found_idx + 1

    parent_ranges: list[dict] = []
    for i, p in enumerate(parents):
        flat_start = parent_starts[i]
        flat_end = parent_starts[i + 1] if i + 1 < len(parents) else len(flat)
        parent_ranges.append({
            "parent_idx": p["parent_idx"],
            "flat_start": flat_start,
            "flat_end": flat_end,
        })
    return parent_ranges, flat


# ─────────────────────────────────────────────────────────────────────
# 单本书:figure manifest 关联 heading_path
# ─────────────────────────────────────────────────────────────────────


def assign_figure_heading_paths(book_dir: str, dry_run: bool = False) -> dict[str, Any]:
    M = _load_poc(book_dir)
    derived = derive_for_book(book_dir)
    parents = derived["parents"]
    parent_idx_to_hp = {p["parent_idx"]: p["heading_path"] for p in parents}

    parent_ranges, flat = _build_parent_flat_ranges(M, parents)

    pg_blk_to_flat = {(b["pg"], b["blk"]): i for i, b in enumerate(flat)}

    manifest_path = OUT / f"{book_dir}.jsonl"
    if not manifest_path.exists():
        print(f"[SKIP] {book_dir}: 无 manifest")
        return {"book": book_dir}
    records = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    n_assigned = 0
    n_orphan_outside_flat = 0  # (pg, blk) 不在 body flat(preface 或 body_end 后)
    n_orphan_no_parent = 0     # 在 body flat 但落在 ref 截断区
    samples_assigned: list[dict] = []
    samples_orphan: list[dict] = []

    for r in records:
        key = (r["page_idx"], r["block_idx"])
        idx = pg_blk_to_flat.get(key)
        if idx is None:
            r["heading_path"] = None
            r["heading_path_id"] = None
            n_orphan_outside_flat += 1
            if len(samples_orphan) < 3:
                samples_orphan.append({**r, "_orphan_kind": "outside_flat"})
            continue

        match = next(
            (pr for pr in parent_ranges if pr["flat_start"] <= idx < pr["flat_end"]),
            None,
        )
        if match is None:
            r["heading_path"] = None
            r["heading_path_id"] = None
            n_orphan_no_parent += 1
            if len(samples_orphan) < 3:
                samples_orphan.append({**r, "_orphan_kind": "no_parent_ref_zone"})
            continue

        hp = parent_idx_to_hp[match["parent_idx"]]
        r["heading_path"] = hp
        r["heading_path_id"] = hashlib.sha256(hp.encode("utf-8")).hexdigest()
        n_assigned += 1
        if len(samples_assigned) < 6:
            samples_assigned.append(r)

    print(f"\n[{book_dir}]")
    print(
        f"  total={len(records)}  assigned={n_assigned}  "
        f"orphan_outside_flat={n_orphan_outside_flat}  orphan_no_parent={n_orphan_no_parent}"
    )

    if samples_assigned:
        print("\n  --- 命中样本 (前 6) ---")
        for r in samples_assigned:
            cap = (r["caption"][0] if r["caption"] else "(无 caption)")[:55]
            print(f"    p{r['page_idx']:<5} {r['chunk_kind']:<7} {cap}")
            print(f"        → {r['heading_path']}")

    if samples_orphan:
        print("\n  --- 孤儿样本 ---")
        for r in samples_orphan:
            cap = (r["caption"][0] if r["caption"] else "(无 caption)")[:55]
            print(f"    [{r['_orphan_kind']}] p{r['page_idx']:<5} {r['chunk_kind']:<7} {cap}")

    if dry_run:
        print("  [DRY] 不写回 manifest")
    else:
        with manifest_path.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  → 写回 {manifest_path}")

    return {
        "book": book_dir,
        "total": len(records),
        "assigned": n_assigned,
        "orphan_outside_flat": n_orphan_outside_flat,
        "orphan_no_parent": n_orphan_no_parent,
    }


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("book_dir", nargs="?", help="POC 目录名;省略需配 --all")
    p.add_argument("--all", action="store_true", help="全 12 本顺序跑")
    p.add_argument("--dry-run", action="store_true", help="不写回 manifest,只打印")
    return p.parse_args()


def main():
    args = _parse_args()
    if args.all and args.book_dir:
        sys.exit("--all 与 book_dir 不能同时指定")
    if not args.all and not args.book_dir:
        sys.exit(
            "用法:python scripts/derive_figure_heading_paths.py <book_dir> | --all  [--dry-run]"
        )
    targets = list(BOOK_TO_FILENAME.keys()) if args.all else [args.book_dir]
    grand = {
        "total": 0, "assigned": 0,
        "orphan_outside_flat": 0, "orphan_no_parent": 0,
    }
    for book in targets:
        if book not in BOOK_TO_FILENAME:
            print(f"[ERROR] 未知 book_dir: {book}")
            continue
        s = assign_figure_heading_paths(book, dry_run=args.dry_run)
        for k in grand:
            grand[k] += s.get(k, 0)
    print(
        f"\n=== 汇总 books={len(targets)}  total={grand['total']}  "
        f"assigned={grand['assigned']}  orphan_outside={grand['orphan_outside_flat']}  "
        f"orphan_no_parent={grand['orphan_no_parent']} ==="
    )


if __name__ == "__main__":
    main()
