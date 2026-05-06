"""
POC: 用权威字典给正文 title block 打 level + heading_path
====================================================================
**只针对《消化系统与疾病》(第2版)POC** —— 字典构建依赖 poc_build_toc_dict.py。

输入:权威字典 627 条(17 章 + 96 节 + 5 附 + 509 一、),L1-L3 用于 title 匹配
正文范围:page_idx > max(toc_pages),即 pg 31 起
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from poc_build_toc_dict import (
    CONTENT_LIST_V2,
    PATCH_INJECT_CANDIDATES,
    _normalize,
    _text_of,
    _update_stack,
    build_toc_dict,
    strict_key,
)

FUZZY_RATIO_THRESHOLD = 0.85
CHAP_PATTERN = re.compile(r"^第\s*\S{1,4}\s*章")

# 兼容 `第N章` 单独 title + 章名紧跟另一 title 的情况
# 本书无篇,只可能匹配 `第N章`(保留 [篇章] 字符类无副作用)
TITLE_ALONE_RE = re.compile(r"^第\s*\S{1,4}\s*[篇章]\s*$")
PART_PREFIX_RE = re.compile(r"^第\s*\S+\s*篇\s*(.+)$")
TAIL_PAGE_HAS_NUM_RE = re.compile(r"(?:[…\.]{2,}|\s|/)\s*[（(]?\s*\d+\s*[）)]?\s*$")


# 正文 OCR 错字硬修(在 strict_key 前替换原 raw)
# 只修影响切割边界的 L1/L2(章/节)— L3 一、 由 Pass 1 正则定位,不依赖字典
PATCH_BODY_RAW_FIX: list[tuple[str, str]] = [
    # 第四章 第五节 肠瘘 — mineru OCR title 识"瘘"为"瘿"
    ("第五节 肠 瘿", "第五节 肠瘘"),
    # 同节 page_header — OCR 识"瘘"为"瘦"
    ("第五节 肠 瘦", "第五节 肠瘘"),
]


def _build_part_alias(entries: list) -> dict[str, str]:
    """L1 篇名 → 完整 `第N篇 名称` 反查;本书无篇,返回空 dict。"""
    alias: dict[str, str] = {}
    for level, title, _path, _pg in entries:
        if level != 1:
            continue
        m = PART_PREFIX_RE.match(title)
        if m:
            alias[strict_key(m.group(1).strip())] = title
    return alias


def _apply_body_raw_fix(raw: str) -> str:
    for old, new in PATCH_BODY_RAW_FIX:
        if old in raw:
            return raw.replace(old, new)
    return raw


def _collect_candidates(
    data: list,
    body_start: int,
    part_alias: dict[str, str],
    lookup: dict,
) -> list[tuple[int, int, str, str, str]]:
    out: list[tuple[int, int, str, str, str]] = []
    for pg_idx in range(body_start, len(data)):
        blocks = data[pg_idx]
        skip_until = -1
        for i, b in enumerate(blocks):
            if i <= skip_until:
                continue
            btype = b.get("type")

            if btype == "title":
                raw = _text_of(b.get("content", {}).get("title_content", []))
                if not raw:
                    continue

                # 章 alone — 找下一 title 拼起来
                if TITLE_ALONE_RE.match(raw.strip()):
                    nxt = None
                    for j in range(i + 1, len(blocks)):
                        if blocks[j].get("type") == "title":
                            nxt = j
                            break
                    if nxt is not None:
                        nxt_raw = _text_of(
                            blocks[nxt]["content"].get("title_content", [])
                        )
                        merged = raw.strip() + " " + nxt_raw.strip()
                        merged = _apply_body_raw_fix(merged)
                        out.append((pg_idx, i, merged, strict_key(merged), "CHAP_MERGED"))
                        skip_until = nxt
                        continue

                raw = _apply_body_raw_fix(raw)
                key = strict_key(raw)
                if key in part_alias:
                    out.append((pg_idx, i, raw, strict_key(part_alias[key]), "PART_REBUILT"))
                    continue

                out.append((pg_idx, i, raw, key, "AS_IS"))

            elif btype == "paragraph":
                raw = _text_of(b.get("content", {}).get("paragraph_content", []))
                if not raw:
                    continue
                if not TAIL_PAGE_HAS_NUM_RE.search(raw.strip()):
                    continue
                key = strict_key(raw)
                if key not in lookup:
                    continue
                out.append((pg_idx, i, raw, key, "MINI_TOC_PARA"))

            elif btype == "page_header":
                raw = _text_of(b.get("content", {}).get("page_header_content", []))
                if not raw:
                    continue
                key = strict_key(raw)
                if key not in lookup:
                    continue
                # blk_idx=-1 让 PAGE_HEADER_FB 排在该页 b0 之前
                out.append((pg_idx, -1, raw, key, "PAGE_HEADER_FB"))

    # FUZZY:救 OCR 错字章名;只跑 L1 章字典
    chapter_dict_keys = {
        k: cands for k, cands in lookup.items()
        if any(c[0] == 1 for c in cands)
    }
    for pg_idx in range(body_start, len(data)):
        for i, b in enumerate(data[pg_idx]):
            if b.get("type") != "title":
                continue
            raw = _text_of(b.get("content", {}).get("title_content", []))
            if not raw or not CHAP_PATTERN.match(raw.strip()):
                continue
            raw = _apply_body_raw_fix(raw)
            key = strict_key(raw)
            if key in lookup:
                continue
            best_ratio = 0.0
            best_key = None
            for dict_key in chapter_dict_keys:
                ratio = SequenceMatcher(None, key, dict_key).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_key = dict_key
            if best_ratio >= FUZZY_RATIO_THRESHOLD and best_key:
                out.append((pg_idx, i, raw, best_key, "FUZZY_TITLE"))

    seen_dedup: set[tuple[str, str]] = set()
    deduped: list[tuple[int, int, str, str, str]] = []
    for c in out:
        if c[4] in ("PAGE_HEADER_FB", "FUZZY_TITLE"):
            sig = (c[4], c[3])
            if sig in seen_dedup:
                continue
            seen_dedup.add(sig)
        deduped.append(c)

    for inj_pg, inj_blk, inj_raw in PATCH_INJECT_CANDIDATES:
        deduped.append((inj_pg, inj_blk, inj_raw, strict_key(inj_raw), "HARDCODE"))

    hardcode_keys = {c[3] for c in deduped if c[4] == "HARDCODE"}
    deduped = [c for c in deduped if not (c[4] == "PAGE_HEADER_FB" and c[3] in hardcode_keys)]

    deduped.sort(key=lambda c: (c[0], c[1]))
    return deduped


def main() -> None:
    result = build_toc_dict()
    entries: list = result["entries"]
    lookup: dict = result["lookup"]
    toc_pages: list[int] = result["toc_pages"]

    data = json.loads(Path(CONTENT_LIST_V2).read_text())
    body_start = max(toc_pages) + 1
    part_alias = _build_part_alias(entries)

    dict_l13_entries = [e for e in entries if e[0] <= 3]

    print(f"=== Dict for matching: {len(lookup)} unique keys (L1-L3) ===")
    print(f"=== Total entries: {len(entries)} ===")
    print(f"=== Body scan range: page_idx {body_start}..{len(data) - 1} ===")
    print(f"=== Part aliases: {len(part_alias)} entries (本书无篇,应为 0)===")

    conflicts = {k: v for k, v in lookup.items() if len(v) > 1}
    print(f"=== Dict key conflicts: {len(conflicts)} ===\n")

    candidates = _collect_candidates(data, body_start, part_alias, lookup)
    action_cnt = Counter(c[4] for c in candidates)
    print(f"=== Body title candidates after preprocessing: {len(candidates)} ===")
    print(f"  Action breakdown: {dict(action_cnt)}\n")

    matched: list[tuple] = []
    unmatched: list[tuple] = []
    stack = ["", "", "", ""]

    for pg_idx, blk_idx, raw, key, action in candidates:
        cands = lookup.get(key, [])

        if len(cands) == 0:
            unmatched.append((pg_idx, blk_idx, raw, key, action))
            continue

        if len(cands) == 1:
            level, parent_path, dict_title = cands[0]
            status = "OK"
        else:
            best = None
            for cand_level, cand_parent, cand_title in cands:
                expected_parent = " / ".join(
                    x for x in stack[: cand_level - 1] if x
                )
                if expected_parent == cand_parent:
                    best = (cand_level, cand_parent, cand_title)
                    break
            if best is not None:
                level, parent_path, dict_title = best
                status = "DISAMBIG"
            else:
                level, parent_path, dict_title = cands[0]
                status = "AMBIGUOUS"

        stack = _update_stack(stack, level, dict_title)
        full_path = (parent_path + " / " + dict_title) if parent_path else dict_title
        matched.append((pg_idx, blk_idx, raw, key, level, full_path, status, action))

    matched_keys = {m[3] for m in matched}
    missing = [
        (level, title, path, pg)
        for level, title, path, pg in dict_l13_entries
        if strict_key(title) not in matched_keys
    ]

    print(f"=== Matched: {len(matched)} ===")
    status_cnt = Counter(m[6] for m in matched)
    matched_action_cnt = Counter(m[7] for m in matched)
    print(f"  Status: {dict(status_cnt)}")
    print(f"  Action: {dict(matched_action_cnt)}\n")
    print("  (showing first 20)")
    for pg, blk, raw, key, level, path, status, action in matched[:20]:
        tag = f"{status}/{action}"
        print(f"  pg={pg:4d} blk={blk:3d} L{level} [{tag:20s}] {path}")

    print(f"\n=== Missing (in L1-L3 dict but not found in body): {len(missing)} ===")
    miss_by_level = Counter(m[0] for m in missing)
    print(f"  By level: {dict(miss_by_level)}\n")
    for level, title, path, pg in missing[:50]:
        print(f"  L{level} (toc_pg={pg}) {path}")
    if len(missing) > 50:
        print(f"  ... ({len(missing) - 50} more missing entries)")

    print(f"\n=== Unmatched (body type=title but lookup failed): {len(unmatched)} ===")
    print(f"  (预期含 (一) / 1. 等更细子节,Step 3 Pass 1/2 切到这粒度)\n")

    print(f"\n=== Summary ===")
    print(f"  Dict L1-L3:                  {len(lookup)}")
    print(f"  Body candidates (after pp):  {len(candidates)}")
    print(f"  Matched:                     {len(matched)}")
    print(f"    coverage of L1-L3 dict:    {100*(len(lookup)-len(missing))/max(len(lookup),1):.1f}%")
    print(f"  Missing in body (L1-L3):     {len(missing)}")
    print(f"  Unmatched in body:           {len(unmatched)}")


if __name__ == "__main__":
    main()
