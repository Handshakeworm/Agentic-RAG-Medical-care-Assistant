"""
POC: 用权威字典给正文 title block 打 level + heading_path
====================================================================
**只针对《内科学 第9版》POC** —— 字典构建依赖 poc_build_toc_dict.py(本书规则)。

输入:权威字典(L1-L3 共 309 条:8 篇 + 137 章 + 164 节)
正文范围:page_idx > max(toc_pages)即 pg 35+

跟诊断学的差异:
  - **TOC 16 页全标"目录"**:不需要 anchor 启发延伸,但 Step 2 不感知此差异
  - **章名干净**:没有诊断学的"... 70" 页码尾 → AS_IS 命中率应更高
  - **正文"第N篇"独立 bug 同样存在**(pg 41 是 `第二篇` 单独成行 → A1 CHAP_MERGED 接篇名)
  - **OCR 错字章名同样存在**(L1 已发现 "肺血栓栓寒症" 应为 "肺血栓栓塞症" → A5 FUZZY_TITLE)

5 类 candidate action(继承诊断学,通用):
  AS_IS         直接命中(主力)
  CHAP_MERGED   篇/章拆分修复 — `第N篇` 独立成行接下一 title
  PART_REBUILT  L1 主标题反查 — 正文只剩"主标题",从字典 L1 alias 反构
  FUZZY_TITLE   OCR 错字章名 — SequenceMatcher.ratio() ≥ 0.85
  PAGE_HEADER_FB 章无 type=title 兜底 — 改用 page_header,边界放本页起点 (pg, 0)
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
    _normalize,
    _text_of,
    _update_stack,
    build_toc_dict,
    strict_key,
)

FUZZY_RATIO_THRESHOLD = 0.85
CHAP_PATTERN = re.compile(r"^第\s*\S{1,4}\s*章")

# A1 篇/章合并:title block 文本是纯"第N篇" 或 "第N章" → 找下一个相邻 title 合并
TITLE_ALONE_RE = re.compile(r"^第\s*\S{1,4}\s*[篇章]\s*$")

# A2 主标题反查:从字典 L1 标题反提"主标题部分"用作 alias key
PART_PREFIX_RE = re.compile(r"^第\s*\S+\s*篇\s*(.+)$")

# A3 mini-TOC paragraph
TAIL_PAGE_HAS_NUM_RE = re.compile(r"(?:[…\.]{2,}|\s|/)\s*\(?\d+\)?\s*$")


def _build_part_alias(entries: list) -> dict[str, str]:
    alias: dict[str, str] = {}
    for level, title, _path, _pg in entries:
        if level != 1:
            continue
        m = PART_PREFIX_RE.match(title)
        if m:
            alias[strict_key(m.group(1).strip())] = title
    return alias


def _collect_candidates(
    data: list,
    body_start: int,
    part_alias: dict[str, str],
    lookup: dict,
) -> list[tuple[int, int, str, str, str]]:
    """收集正文标题候选;返回 [(pg, blk, raw, key, action), ...]"""
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
                        out.append((pg_idx, i, merged, strict_key(merged), "CHAP_MERGED"))
                        skip_until = nxt
                        continue

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
                out.append((pg_idx, 0, raw, key, "PAGE_HEADER_FB"))

    # A5 fuzzy title 匹配(救 OCR 错字章名,如"肺血栓栓寒症")
    chapter_dict_keys = {
        k: cands for k, cands in lookup.items()
        if any(c[0] == 2 for c in cands)
    }
    for pg_idx in range(body_start, len(data)):
        for i, b in enumerate(data[pg_idx]):
            if b.get("type") != "title":
                continue
            raw = _text_of(b.get("content", {}).get("title_content", []))
            if not raw or not CHAP_PATTERN.match(raw.strip()):
                continue
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

    # 选择性去重:对 PAGE_HEADER_FB 和 FUZZY_TITLE 去重(可能多次出现同 key)
    seen_dedup: set[tuple[str, str]] = set()
    deduped: list[tuple[int, int, str, str, str]] = []
    for c in out:
        if c[4] in ("PAGE_HEADER_FB", "FUZZY_TITLE"):
            sig = (c[4], c[3])
            if sig in seen_dedup:
                continue
            seen_dedup.add(sig)
        deduped.append(c)
    return deduped


def main() -> None:
    result = build_toc_dict()
    entries: list = result["entries"]
    lookup: dict[str, list[tuple[int, str]]] = result["lookup"]
    toc_pages: list[int] = result["toc_pages"]

    data = json.loads(Path(CONTENT_LIST_V2).read_text())
    body_start = max(toc_pages) + 1
    part_alias = _build_part_alias(entries)

    dict_l13_entries = [e for e in entries if e[0] <= 3]

    print(f"=== Dict for matching: {len(lookup)} unique keys (L1-L3) ===")
    print(f"=== Total entries (L1-L3): {len(entries)} ===")
    print(f"=== Body scan range: page_idx {body_start}..{len(data) - 1} ===")
    print(f"=== Part aliases (for A2 rebuild): {part_alias} ===\n")

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
    print("  (showing first 30)")
    for pg, blk, raw, key, level, path, status, action in matched[:30]:
        tag = f"{status}/{action}"
        print(f"  pg={pg:4d} blk={blk:3d} L{level} [{tag:20s}] {path}")
    if len(matched) > 30:
        print(f"  ... ({len(matched) - 30} more matched lines)")

    print(f"\n=== Missing (in L1-L3 dict but not found in body): {len(missing)} ===")
    miss_by_level = Counter(m[0] for m in missing)
    print(f"  By level: {dict(miss_by_level)}\n")
    for level, title, path, pg in missing[:50]:
        print(f"  L{level} (toc_pg={pg}) {path}")
    if len(missing) > 50:
        print(f"  ... ({len(missing) - 50} more missing entries)")

    print(f"\n=== Unmatched (body type=title but lookup failed): {len(unmatched)} ===")
    print(f"  (预期含【...】/1./2./...等更细子节,不切到这粒度)\n")
    print("  (showing first 30 samples)")
    for pg, blk, raw, key, action in unmatched[:30]:
        raw_show = raw.replace("\n", "\\n")
        print(f"  pg={pg:4d} blk={blk:3d} raw='{raw_show[:70]}' norm='{key[:70]}'")
    if len(unmatched) > 30:
        print(f"  ... ({len(unmatched) - 30} more)")

    print(f"\n=== Summary ===")
    print(f"  Dict L1-L3:                  {len(lookup)}")
    print(f"  Body candidates (after pp):  {len(candidates)}")
    print(f"  Matched:                     {len(matched)}")
    print(f"    coverage of L1-L3 dict:    {100*(len(lookup)-len(missing))/max(len(lookup),1):.1f}%")
    print(f"  Missing in body (L1-L3):     {len(missing)}")
    print(f"  Unmatched in body:           {len(unmatched)}")


if __name__ == "__main__":
    main()
