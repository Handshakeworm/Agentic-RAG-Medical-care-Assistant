"""scripts/merge_multipanel_figures.py — 同页 multi-panel chart/figure 合并(方案 A')。

mineru 把同一概念图(如"图 X-Y" 含 Panel A 和 Panel B)切成多个 image block,只有最后
一个挂 caption。本脚本扫每本 figure manifest,识别 multi-panel 组,在原 record 上加
合并标记字段,**不删任何记录**(保留可审计性)。下游 figure_enrichment_generation.py
按 merge_role 决定是否调 LLM。

# 合并规则(方案 A',2026-05-08 论证后定型)

候选 sibling(无 caption 块)向后扫,直到找到一个**真 caption 块**作 anchor。条件:

1. 同 `page_idx`(只做同页合并 — chart/figure 极少跨页,跨页探测脆弱已放弃)
2. 同 `heading_path`(精确相等)
3. 在 vision_records(chart/figure)序列里**严格相邻**——sibling 与 anchor 之间
   没有别的 chart/figure 块(table/text 块不在 vision_records 序列里,自然不计入)
4. anchor 候选的 caption 必须匹配 `图 + 数字` 的正则模式——过滤"A.运动前"这类
   panel 子标签被 mineru 误抓为 caption 的情形

注:**不再检查 chunk_kind / mineru_sub_type 一致性**——mineru 对科学示意图
(基因结构、流程示意、解剖图)的 chunk_kind 分类不可靠(同一图的 Panel A 标
flowchart、Panel B 标 chart bar_stacked 是常态),严格匹配会漏召大量真合并。
2026-05-08 实测放宽后从 24 组涨到 27 组,新增 3 组全部为真 multi-panel,零误判。

# manifest 新增字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `merge_role` | "anchor" / "sibling" / "standalone" | 块在合并组中的角色 |
| `merge_group_id` | str / None | 组 ID(格式 "p{page}-b{anchor_block}");standalone 为 None |
| `merged_image_abs_paths` | list[str] / None | **anchor 块独有**,组内所有截图绝对路径,按 bbox y→x 排序(top-to-bottom, left-to-right);standalone 和 sibling 为 None |
| `merged_block_idxs` | list[int] / None | **anchor 块独有**,组内所有成员 block_idx 列表(含 anchor 自己) |

table 块统一标 `merge_role="standalone"`,不参与合并。

# 已知局限

- **跨页图**:5-15 条真跨页 figure 无法合并(本脚本不处理),各自独立 summary,损失部分语境
- **跨页误判**:协和呼吸 p1255 这种"前页末尾跨过来 + 同页另有 caption" 会被错合到当页 caption 头上(实测 1/27 误判,已知)

用法:
    python scripts/merge_multipanel_figures.py <book_dir>          # 单本
    python scripts/merge_multipanel_figures.py --all               # 全 12 本
    python scripts/merge_multipanel_figures.py --all --dry-run     # 只汇报,不写回
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from load_chunks_to_pg import BOOK_TO_FILENAME  # noqa: E402

INPUT_DIR = REPO_ROOT / "scripts" / "figure_extract_output"

# anchor caption 必须匹配的模式("图 X" / "图1-2" / "图 2-5-4-4" 等)
RE_REAL_CAP = re.compile(r"图\s*[\d]")


def _is_real_caption(caption_list: list[str] | None) -> bool:
    if not caption_list:
        return False
    return bool(RE_REAL_CAP.search(" ".join(caption_list)))


def _bbox_sort_key(rec: dict[str, Any]) -> tuple[float, float]:
    """anchor 组内成员排序:先 y(top→bottom)再 x(left→right)。"""
    bbox = rec.get("bbox") or [0, 0, 0, 0]
    return (bbox[1], bbox[0])


def merge_book(book_dir: str, dry_run: bool = False) -> dict[str, int]:
    manifest_path = INPUT_DIR / f"{book_dir}.jsonl"
    if not manifest_path.exists():
        print(f"[SKIP] {book_dir}: manifest 不存在")
        return {"book": book_dir}

    records: list[dict[str, Any]] = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    records.sort(key=lambda r: (r["page_idx"], r["block_idx"]))

    # 只在 chart/figure 序列上跑合并算法;table 单独标 standalone
    vision_records = [r for r in records if r["chunk_kind"] in ("chart", "figure")]

    # sibling_block_key → anchor_block_key 映射
    sibling_to_anchor: dict[tuple[int, int], tuple[int, int]] = {}

    for i, r in enumerate(vision_records):
        if r.get("caption"):
            continue
        for j in range(i + 1, len(vision_records)):
            n = vision_records[j]
            if n["page_idx"] != r["page_idx"]:
                break
            if n.get("heading_path") != r.get("heading_path"):
                break
            if n.get("caption"):
                if _is_real_caption(n["caption"]):
                    sibling_to_anchor[(r["page_idx"], r["block_idx"])] = (
                        n["page_idx"], n["block_idx"]
                    )
                    break
                # 假 caption(panel 子标签等),跳过继续向后扫
                continue

    # anchor → 所有 siblings(组员,不含 anchor 自己)
    anchor_to_siblings: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for sk, ak in sibling_to_anchor.items():
        anchor_to_siblings[ak].append(sk)

    anchor_keys = set(anchor_to_siblings.keys())
    sibling_keys = set(sibling_to_anchor.keys())

    # 索引:(page, block) → record
    rec_idx = {(r["page_idx"], r["block_idx"]): r for r in records}

    # 标注每条 record
    n_anchor = 0
    n_sibling = 0
    n_standalone = 0
    n_table = 0
    for r in records:
        key = (r["page_idx"], r["block_idx"])

        if r["chunk_kind"] == "table":
            r["merge_role"] = "standalone"
            r["merge_group_id"] = None
            r["merged_image_abs_paths"] = None
            r["merged_block_idxs"] = None
            n_table += 1
            continue

        if key in anchor_keys:
            sib_keys = anchor_to_siblings[key]
            members = [r] + [rec_idx[sk] for sk in sib_keys]
            members.sort(key=_bbox_sort_key)
            r["merge_role"] = "anchor"
            r["merge_group_id"] = f"p{r['page_idx']}-b{r['block_idx']}"
            r["merged_image_abs_paths"] = [m["image_abs_path"] for m in members]
            r["merged_block_idxs"] = [m["block_idx"] for m in members]
            n_anchor += 1
        elif key in sibling_keys:
            ak = sibling_to_anchor[key]
            r["merge_role"] = "sibling"
            r["merge_group_id"] = f"p{ak[0]}-b{ak[1]}"
            r["merged_image_abs_paths"] = None
            r["merged_block_idxs"] = None
            n_sibling += 1
        else:
            r["merge_role"] = "standalone"
            r["merge_group_id"] = None
            r["merged_image_abs_paths"] = None
            r["merged_block_idxs"] = None
            n_standalone += 1

    # 组规模分布(用于审计)
    group_sizes = defaultdict(int)
    for sib_keys in anchor_to_siblings.values():
        group_sizes[len(sib_keys) + 1] += 1

    print(f"\n[{book_dir}]")
    print(f"  table standalone:           {n_table}")
    print(f"  chart/figure anchor:        {n_anchor}  (合并组数)")
    print(f"  chart/figure sibling:       {n_sibling}  (被合并的子图)")
    print(f"  chart/figure standalone:    {n_standalone}  (无合并的独立图)")
    if group_sizes:
        size_str = ", ".join(f"{s}图×{c}组" for s, c in sorted(group_sizes.items()))
        print(f"  组规模分布:                {size_str}")

    if dry_run:
        print("  [DRY] 不写回 manifest")
    else:
        with manifest_path.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  → 已写回 {manifest_path.name}")

    return {
        "book": book_dir,
        "table_standalone": n_table,
        "anchor": n_anchor,
        "sibling": n_sibling,
        "vision_standalone": n_standalone,
    }


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("book_dir", nargs="?", help="POC 目录名")
    p.add_argument("--all", action="store_true", help="全 12 本顺序处理")
    p.add_argument("--dry-run", action="store_true", help="只汇报,不写回")
    return p.parse_args()


def main():
    args = _parse_args()
    if args.all and args.book_dir:
        sys.exit("--all 与 book_dir 不能同时指定")
    if not args.all and not args.book_dir:
        sys.exit("用法:python scripts/merge_multipanel_figures.py <book_dir> | --all  [--dry-run]")

    targets = list(BOOK_TO_FILENAME.keys()) if args.all else [args.book_dir]
    grand = {"table_standalone": 0, "anchor": 0, "sibling": 0, "vision_standalone": 0}
    for book in targets:
        if book not in BOOK_TO_FILENAME:
            print(f"[ERROR] 未知 book_dir: {book}")
            continue
        s = merge_book(book, dry_run=args.dry_run)
        for k in grand:
            grand[k] += s.get(k, 0)
    print(
        f"\n=== 汇总 books={len(targets)}  "
        f"table={grand['table_standalone']}  anchor={grand['anchor']}  "
        f"sibling={grand['sibling']}  vision_standalone={grand['vision_standalone']} ==="
    )


if __name__ == "__main__":
    main()
