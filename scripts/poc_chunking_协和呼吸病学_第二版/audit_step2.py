"""
Step 2 全量自动校验:目录 entry → 实际匹配位置是否正确

5 类硬校验:
  1. 位置唯一性:每个 dict entry 恰好被匹配到 1 个真实位置
  2. 顺序单调性:matched (pg, blk) 序列严格递增(防错位)
  3. 嵌套正确性:每个 章 的 path 头 = 它所在的 篇
  4. 声明页码 vs 实际位置:TOC 末尾"(数字)"是印刷页号,
     可推出 mineru 期望页 = printed_page + 印刷-mineru offset
     offset 由 1-2 个已知 anchor 标定,然后全量复算
  5. PAGE_HEADER_FB 真兜底列表:同 dict_key 没 AS_IS/CHAP_MERGED/FUZZY 同伴的
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from poc_build_toc_dict import (
    CONTENT_LIST_V2,
    _block_lines,
    _normalize,
    build_toc_dict,
    strict_key,
)
from poc_match_body_titles import _build_part_alias, _collect_candidates


# 重新从 TOC 原文里抽 (entry strict_key, 声明页码)
TAIL_PAGE_EXTRACT_RE = re.compile(r"[（(]?\s*(\d+)\s*[）)]?\s*$")


def _extract_declared_pages(toc_pages: list[int], data: list, lookup: dict) -> dict[str, int]:
    """从 TOC 原文末尾抽印刷页号。"""
    declared: dict[str, int] = {}
    for pg in toc_pages:
        for b in data[pg]:
            for line in _block_lines(b):
                if not line.strip():
                    continue
                m = TAIL_PAGE_EXTRACT_RE.search(line.strip())
                if not m:
                    continue
                printed_page = int(m.group(1))
                # 标题部分 = 行去掉尾页码,strict_key 后看是否匹配字典
                title_part = line[: m.start()].strip()
                key = strict_key(title_part)
                if key in lookup:
                    # 取首次出现(同标题被多 page 重复时)
                    if key not in declared:
                        declared[key] = printed_page
    return declared


def main() -> None:
    result = build_toc_dict()
    entries = result["entries"]
    lookup = result["lookup"]
    toc_pages = result["toc_pages"]

    data = json.loads(Path(CONTENT_LIST_V2).read_text())
    body_start = max(toc_pages) + 1
    part_alias = _build_part_alias(entries)
    candidates = _collect_candidates(data, body_start, part_alias, lookup)

    # 重做 matching(只保留 OK/DISAMBIG,排除 unmatched)
    matched: list[tuple] = []
    stack = ["", "", "", ""]
    for pg_idx, blk_idx, raw, key, action in candidates:
        cands = lookup.get(key, [])
        if not cands:
            continue
        if len(cands) == 1:
            level, parent_path, dict_title = cands[0]
        else:
            best = None
            for cand_level, cand_parent, cand_title in cands:
                expected_parent = " / ".join(x for x in stack[: cand_level - 1] if x)
                if expected_parent == cand_parent:
                    best = (cand_level, cand_parent, cand_title)
                    break
            best = best or cands[0]
            level, parent_path, dict_title = best
        from poc_build_toc_dict import _update_stack
        stack = _update_stack(stack, level, dict_title)
        full_path = (parent_path + " / " + dict_title) if parent_path else dict_title
        matched.append((pg_idx, blk_idx, raw, key, level, full_path, action))

    print(f"=== Total matched candidates: {len(matched)} ===\n")

    # ===== 校验 1: 位置唯一性 =====
    print("=== 1. 位置唯一性(每个 dict_key 应恰好 1 个 strong 位置)===")
    by_key: dict[str, list] = defaultdict(list)
    for m in matched:
        by_key[m[3]].append(m)

    strong_actions = {"AS_IS", "CHAP_MERGED", "FUZZY_TITLE", "PART_REBUILT", "HARDCODE"}
    multi_strong = []
    no_strong = []
    for k, ms in by_key.items():
        strong = [m for m in ms if m[6] in strong_actions]
        if len(strong) > 1:
            multi_strong.append((k, strong))
        if len(strong) == 0:
            no_strong.append((k, ms))
    print(f"  字典 entries: {len(lookup)}")
    print(f"  matched 唯一 keys: {len(by_key)}")
    print(f"  有多个 strong 位置的 key: {len(multi_strong)}")
    for k, strong in multi_strong[:10]:
        print(f"    {k}: {[(m[0], m[6]) for m in strong]}")
    print(f"  无任何 strong 位置(只有 PAGE_HEADER_FB)的 key: {len(no_strong)}  ← **真兜底**")
    for k, ms in no_strong[:20]:
        print(f"    {k}: {[(m[0], m[6]) for m in ms]}")

    # ===== 校验 2: 顺序单调性 =====
    print("\n=== 2. 顺序单调性(matched 序列 (pg, blk) 应严格递增)===")
    # 只取每个 key 的首个 strong 位置
    chosen: dict[str, tuple] = {}
    for m in matched:
        k = m[3]
        if m[6] not in strong_actions:
            continue
        if k not in chosen:
            chosen[k] = m

    # 按 entries 顺序铺开,看 (pg, blk) 是否递增
    ordered = []
    for level, title, path, _ in entries:
        k = strict_key(title)
        if k in chosen:
            ordered.append((k, chosen[k][0], chosen[k][1], level, title))

    bad_order = []
    for i in range(1, len(ordered)):
        prev_pg, prev_blk = ordered[i-1][1], ordered[i-1][2]
        cur_pg, cur_blk = ordered[i][1], ordered[i][2]
        if (cur_pg, cur_blk) <= (prev_pg, prev_blk):
            bad_order.append((ordered[i-1], ordered[i]))
    print(f"  字典顺序中位置递减/重复的相邻对: {len(bad_order)}")
    for a, b in bad_order[:10]:
        print(f"    L{a[3]} {a[4]} @ pg={a[1]} blk={a[2]}  →  L{b[3]} {b[4]} @ pg={b[1]} blk={b[2]}")

    # ===== 校验 3: 嵌套正确性 =====
    print("\n=== 3. 嵌套正确性(每个章的 path 头 = 当前所在篇)===")
    bad_nest = []
    current_pian = None
    for k, pg, blk, level, title in ordered:
        if level == 1:
            current_pian = title
        elif level == 2:
            full_path_entry = next((p for lvl, t, p, _ in entries if lvl == 2 and t == title), None)
            if full_path_entry:
                expected_pian = full_path_entry.split(" / ")[0]
                if current_pian != expected_pian:
                    bad_nest.append((title, current_pian, expected_pian, pg))
    print(f"  章错误归到非父篇下的: {len(bad_nest)}")
    for t, got, exp, pg in bad_nest[:10]:
        print(f"    pg={pg} {t}: 期望父篇={exp!r} 实际匹配序列里所在篇={got!r}")

    # ===== 校验 4: 声明页码 vs 实际匹配 =====
    print("\n=== 4. TOC 声明页码 vs 实际匹配位置(印刷-mineru offset 一致性)===")
    declared = _extract_declared_pages(toc_pages, data, lookup)
    print(f"  能从 TOC 抽出页码的 entries: {len(declared)}/{len(entries)}")
    if declared:
        # 拿首章定 offset(第一章 通常印刷页 = 3, mineru pg = 20)
        # offset = mineru_pg - printed_page
        # 但 印刷页 在不同卷可能不连续(上下册)→ 拆 by 篇/卷分组
        offsets = []
        for k, pg, blk, level, title in ordered:
            if k in declared:
                offsets.append((title, pg, declared[k], pg - declared[k]))
        # 看 offset 分布
        offset_values = [o[3] for o in offsets]
        offset_counter = Counter(offset_values)
        print(f"  offset (mineru_pg - printed_page) 分布(top 10):")
        for off, cnt in offset_counter.most_common(10):
            print(f"    offset={off}: {cnt} entries")
        # 找异常 offset(偏离 mode > 5 页)
        if offset_counter:
            mode_offset = offset_counter.most_common(1)[0][0]
            outliers = [(t, mp, pp, off) for t, mp, pp, off in offsets if abs(off - mode_offset) > 5]
            print(f"\n  偏离主流 offset({mode_offset}) > 5 页的 entries: {len(outliers)}")
            for t, mp, pp, off in outliers[:30]:
                print(f"    {t}: 印刷页={pp} mineru页={mp} offset={off}(主流={mode_offset})")

    # ===== 校验 5: 真兜底列表(从校验 1 重申)=====
    print("\n=== 5. 真兜底(只能靠 PAGE_HEADER_FB 找位置)的 章/篇 ===")
    print(f"  共 {len(no_strong)} 个 — 详见校验 1")


if __name__ == "__main__":
    main()
