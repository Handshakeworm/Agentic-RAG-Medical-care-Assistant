"""
POC 切分主流程(end-to-end)
====================================================================
**只针对《内科学 第9版》POC**(规则书本特化,通用方法论见
[`scripts/METHODOLOGY.md`](../METHODOLOGY.md),本书笔记见 BOOK_NOTES.md)。

依赖:
  - poc_build_toc_dict(Step 1 目录字典,L1-L4)
  - poc_match_body_titles(Step 2 正文匹配)

跟诊断学的关键差异:
  - **字典扩到 L4 (一、/[附])**:section 边界从节级扩到 L4 级,sections 总数 412 而非诊断学 212。
    section 内**不再含 一、 子标题**(都已切成 L4 section),所以 Pass 1 改用 (一);
    Pass 2 改用 1./2.;Pass 3 不再有效(取消)。
  - **CHAPTER_ABSORB 扩到 L1/L2/L3**:L3 节内容被 L4 切走后,节级只剩前言/附引,
    < 阈值时并入第一个 L4 section。
  - BODY_END_MARKERS:同诊断学(`中英文名词对照索引`)
  - strong 信号:同诊断学(加 FUZZY_TITLE)
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from poc_build_toc_dict import (
    CONTENT_LIST_V2,
    _text_of,
    build_toc_dict,
)
from poc_match_body_titles import (
    _build_part_alias,
    _collect_candidates,
)

# ─────────────────────────────────────────────────────────────────────
# 子标题 pattern(切分点)
# ─────────────────────────────────────────────────────────────────────
RE_TABLE_TITLE = re.compile(r"^表\s*[\d\-]+")
RE_FIG_TITLE = re.compile(r"^图\s*[\d\-]+")

# 本书节内子标题 pattern(L4 一、 已成 section 边界,不再用作 Pass 切分)
# Pass 1: 【】 (中括号子方面如【病因】【临床表现】【治疗】) — 本书主力,1680 个
# Pass 2: (一)(二) (中文括号编号) — 救 Pass 1 后仍超大的(少数节【】下还有(一))
RE_BRACE = re.compile(r"^【.+?】")
RE_PAREN_CN = re.compile(r"^[（(][一二三四五六七八九十百]+[)）]")

# 书末截断:本书用 `中英文名词对照索引`(pg 967 出现 type=title)
BODY_END_MARKERS = ("中英文名词对照索引",)
RE_BODY_END = re.compile(r"^(?:" + "|".join(BODY_END_MARKERS) + r")\s*$")

# 节/篇末"参考文献"或"推荐阅读"丢弃(本书每篇末有 8 处"推荐阅读"+ 一段 list 全英文 references)
# 走 _find_ref_idx 截断 section 到 marker 位置之前,marker 之后 blocks 全丢
RE_REF_MARKER = re.compile(r"^(?:参考文献|推荐阅读)\s*$")

# ─────────────────────────────────────────────────────────────────────
# 阈值
# ─────────────────────────────────────────────────────────────────────
PARENT_SPLIT_THRESHOLD = 5000        # > 5000 触发 Pass 1 【】(user 拍板 2026-05-05,从 4000 调高)
PARENT_REFINE_THRESHOLD = 6000       # > 6000 触发 Pass 2 (一) 救超大
PARENT_MERGE_TINY_THRESHOLD = 500
CHILD_SPLIT_THRESHOLD = 1200
CHILD_TARGET_SIZE = 600
CHILD_MIN_SIZE = 200

# 跨 section 吸收阈值:L1/L2/L3 < 500 字 → 并入下一 section
# L3 也加入(本书 L4 把节内容切走后,节级常只剩短引言)
# L4 永远保留(L4 是最小独立语义单元)
CHAPTER_ABSORB_THRESHOLD = 500


# ─────────────────────────────────────────────────────────────────────
# block 序列化
# ─────────────────────────────────────────────────────────────────────


def _block_text_safe(b: dict) -> str:
    t = b.get("type")
    c = b.get("content", {})
    if t == "title":
        return _text_of(c.get("title_content", []))
    if t == "paragraph":
        return _text_of(c.get("paragraph_content", []))
    if t == "list":
        return "\n".join(
            _text_of(it.get("item_content", []))
            for it in c.get("list_items", [])
            if isinstance(it, dict)
        )
    return ""


def _flatten_blocks(data: list, body_start: int) -> list[dict]:
    flat = []
    for pg_idx in range(body_start, len(data)):
        for blk_idx, b in enumerate(data[pg_idx]):
            txt = _block_text_safe(b)
            flat.append({
                "pg": pg_idx, "blk": blk_idx, "type": b.get("type"),
                "text": txt, "len": len(txt),
            })
    return flat


def _find_body_end(flat: list[dict]) -> int:
    for i, b in enumerate(flat):
        if b["type"] == "title" and RE_BODY_END.match(b["text"].strip()):
            return i
    return len(flat)


# ─────────────────────────────────────────────────────────────────────
# 节(L1-L4)边界:复用 Step 2 匹配 + REAL_START 选取
# ─────────────────────────────────────────────────────────────────────


def _real_start_positions(flat: list[dict], result: dict) -> dict[int, tuple[int, str]]:
    lookup = result["lookup"]
    data = json.loads(Path(CONTENT_LIST_V2).read_text())
    body_start = max(result["toc_pages"]) + 1
    part_alias = _build_part_alias(result["entries"])
    candidates = _collect_candidates(data, body_start, part_alias, lookup)

    pos_map = {(b["pg"], b["blk"]): i for i, b in enumerate(flat)}
    prefix = [0]
    for b in flat:
        prefix.append(prefix[-1] + b["len"])

    matched = []
    prev_pos = None
    for pg_idx, blk_idx, raw, key, action in candidates:
        cands = lookup.get(key, [])
        if not cands:
            continue
        level, parent_path, dict_title = cands[0]
        pos = pos_map.get((pg_idx, blk_idx))
        if pos is None:
            continue
        gap = prefix[pos] - prefix[prev_pos + 1] if prev_pos is not None else 0
        matched.append({"pos": pos, "level": level, "title": dict_title,
                        "action": action, "gap": gap})
        prev_pos = pos

    groups = defaultdict(list)
    for m in matched:
        groups[(m["level"], m["title"])].append(m)
    real_start_pos: dict[int, tuple[int, str]] = {}
    for key, recs in groups.items():
        recs.sort(key=lambda r: r["pos"])
        strong = [
            i for i, r in enumerate(recs)
            if r["action"] in ("PART_REBUILT", "CHAP_MERGED", "FUZZY_TITLE")
            or (r["action"] == "AS_IS" and r["gap"] >= 50)
        ]
        if strong:
            chosen = strong[-1]
        else:
            # 退化:AS_IS 永远优先 PAGE_HEADER_FB(神经外科学 2026-05-05 发现 bug 后修)
            non_fb = [i for i, r in enumerate(recs) if r["action"] != "PAGE_HEADER_FB"]
            chosen = non_fb[-1] if non_fb else len(recs) - 1
        real_start_pos[recs[chosen]["pos"]] = (recs[chosen]["level"], recs[chosen]["title"])
    return real_start_pos


# ─────────────────────────────────────────────────────────────────────
# 子标题边界识别(用于父块二次切)
# ─────────────────────────────────────────────────────────────────────


def _is_brace_subheading(text: str, *, block_type: str | None = None) -> bool:
    """Pass 1 切分点:【】(本书 type=title 主力,1680 个)"""
    if block_type == "list":
        return False
    s = text.strip()
    if len(s) < 3:
        return False
    if RE_TABLE_TITLE.match(s) or RE_FIG_TITLE.match(s):
        return False
    return bool(RE_BRACE.match(s))


def _is_paren_subheading(text: str, *, block_type: str | None = None) -> bool:
    """Pass 2 切分点:(一)(二)"""
    if block_type == "list":
        return False
    s = text.strip()
    if len(s) < 4:
        return False
    if RE_TABLE_TITLE.match(s) or RE_FIG_TITLE.match(s):
        return False
    return bool(RE_PAREN_CN.match(s))


def _find_ref_idx(section_blocks: list[dict]) -> int | None:
    for idx in range(1, len(section_blocks)):
        if RE_REF_MARKER.match(section_blocks[idx]["text"].strip()):
            return idx
    return None


# ─────────────────────────────────────────────────────────────────────
# 大父块二次切
# ─────────────────────────────────────────────────────────────────────

LEVEL_SECTION = 0
LEVEL_BRACE = 1    # 【病因】【临床表现】等子方面
LEVEL_PAREN = 2    # (一) (二)


def _split_big_parent(section_blocks: list[dict], threshold: int,
                      ref_idx: int | None) -> list[tuple[int, int]]:
    section_len = sum(blk["len"] for blk in section_blocks)
    if section_len <= threshold:
        return [(0, LEVEL_SECTION)]

    upper = ref_idx if ref_idx is not None else len(section_blocks)

    def _refine(boundaries: list[tuple[int, int]], predicate,
                new_level: int, thr: int) -> list[tuple[int, int]]:
        positions = [b[0] for b in boundaries] + [len(section_blocks)]
        out = list(boundaries)
        for i in range(len(boundaries)):
            sa, sb = positions[i], positions[i + 1]
            seg_len = sum(b["len"] for b in section_blocks[sa:sb])
            if seg_len <= thr:
                continue
            seg_upper = min(sb, upper)
            for idx in range(sa + 1, seg_upper):
                b = section_blocks[idx]
                if predicate(b["text"], block_type=b.get("type")):
                    out.append((idx, new_level))
        seen: dict[int, int] = {}
        for pos, lvl in out:
            if pos not in seen or lvl < seen[pos]:
                seen[pos] = lvl
        return sorted(seen.items())

    # Pass 1: 【】 (主切分,触发阈值 4000) — 本书子标题主力
    pass1 = _refine([(0, LEVEL_SECTION)], _is_brace_subheading, LEVEL_BRACE, threshold)
    # Pass 2: (一) (二) (二次细化,只对超 6000 字段触发) — 救节内还有 (一) 的极少数
    pass2 = _refine(pass1, _is_paren_subheading, LEVEL_PAREN, PARENT_REFINE_THRESHOLD)
    return pass2


def _split_parent_to_children_by_size(parent_blocks: list[dict],
                                      target: int,
                                      min_size: int = 0) -> list[list[dict]]:
    if not parent_blocks:
        return []
    children: list[list[dict]] = []
    current: list[dict] = []
    current_len = 0
    for b in parent_blocks:
        blen = b["len"]
        if not current:
            current.append(b)
            current_len = blen
            continue
        with_b = current_len + blen
        if current_len < min_size:
            current.append(b)
            current_len = with_b
            continue
        if abs(with_b - target) <= abs(current_len - target):
            current.append(b)
            current_len = with_b
        else:
            children.append(current)
            current = [b]
            current_len = blen
    if current:
        if children and current_len < target // 2:
            children[-1].extend(current)
        else:
            children.append(current)
    return children


def _merge_tiny_parents(boundaries: list[tuple[int, int]],
                        section_blocks: list[dict],
                        min_size: int) -> list[tuple[int, int]]:
    if len(boundaries) <= 1:
        return list(boundaries)
    n = len(section_blocks)

    def seg_size(positions: list[int], i: int) -> int:
        sa = positions[i]
        sb = positions[i + 1] if i + 1 < len(positions) else n
        return sum(b["len"] for b in section_blocks[sa:sb])

    bs = list(boundaries)
    while True:
        positions = [b[0] for b in bs] + [n]
        merged = False
        for i in range(len(bs)):
            size = seg_size(positions, i)
            if size >= min_size:
                continue
            cur_level = bs[i][1]
            if i + 1 < len(bs):
                next_level = bs[i + 1][1]
                if cur_level <= next_level:
                    bs = bs[:i + 1] + bs[i + 2:]
                    merged = True
                    break
            if i >= 1:
                prev_level = bs[i - 1][1]
                if prev_level <= cur_level:
                    bs = bs[:i] + bs[i + 1:]
                    merged = True
                    break
        if not merged:
            break
    return bs


# ─────────────────────────────────────────────────────────────────────
# Pipeline 主入口
# ─────────────────────────────────────────────────────────────────────


def chunk_book() -> dict:
    result = build_toc_dict()
    data = json.loads(Path(CONTENT_LIST_V2).read_text())
    body_start = max(result["toc_pages"]) + 1
    flat_full = _flatten_blocks(data, body_start)

    body_end = _find_body_end(flat_full)
    flat = flat_full[:body_end]
    truncated_blocks = len(flat_full) - body_end
    truncated_chars = sum(b["len"] for b in flat_full[body_end:])

    real_start_pos = _real_start_positions(flat, result)
    section_splits = sorted(real_start_pos.keys())

    parents: list[dict] = []
    children: list[dict] = []
    ref_dropped_blocks = 0
    ref_dropped_chars = 0
    pending_blocks: list[dict] = []

    for i in range(len(section_splits)):
        a = section_splits[i]
        b_end = section_splits[i + 1] if i + 1 < len(section_splits) else len(flat)
        section_blocks_full = flat[a:b_end]
        section_len_raw = sum(b["len"] for b in section_blocks_full)
        level_now, _sec_title_preview = real_start_pos[a]

        # L1/L2/L3 section < 阈值 → 累积到 pending,跳过本轮
        # L4 永远保留(本书 L4 是最小独立语义单元,即使内容少也是真实)
        if (level_now in (1, 2, 3) and section_len_raw < CHAPTER_ABSORB_THRESHOLD
                and i + 1 < len(section_splits)):
            pending_blocks.extend(section_blocks_full)
            continue

        if pending_blocks:
            section_blocks_full = pending_blocks + section_blocks_full
            pending_blocks = []

        level, sec_title = real_start_pos[a]

        ref_idx = _find_ref_idx(section_blocks_full)
        if ref_idx is not None:
            ref_dropped_blocks += len(section_blocks_full) - ref_idx
            ref_dropped_chars += sum(b["len"] for b in section_blocks_full[ref_idx:])
            section_blocks = section_blocks_full[:ref_idx]
        else:
            section_blocks = section_blocks_full

        if not section_blocks:
            continue

        boundaries = _split_big_parent(section_blocks, PARENT_SPLIT_THRESHOLD, None)
        boundaries = _merge_tiny_parents(boundaries, section_blocks,
                                         PARENT_MERGE_TINY_THRESHOLD)
        parent_starts = [b[0] for b in boundaries]

        parent_starts_with_end = parent_starts + [len(section_blocks)]
        for pi in range(len(parent_starts)):
            pa = parent_starts_with_end[pi]
            pb = parent_starts_with_end[pi + 1]

            parent_blocks = section_blocks[pa:pb]
            parent_len = sum(blk["len"] for blk in parent_blocks)
            head = parent_blocks[0]["text"].strip().replace("\n", " ")[:60]
            is_split = len(parent_starts) > 1
            parent_title = f"{sec_title} >> {head}" if is_split else sec_title

            parent_idx = len(parents)
            parents.append({
                "parent_idx": parent_idx,
                "section_title": sec_title, "level": level,
                "title": parent_title, "head": head,
                "pg_start": parent_blocks[0]["pg"],
                "len": parent_len,
                "is_split_from_section": is_split,
            })

            if parent_len <= CHILD_SPLIT_THRESHOLD:
                children.append({
                    "parent_idx": parent_idx,
                    "section_title": sec_title,
                    "head": head,
                    "pg_start": parent_blocks[0]["pg"],
                    "len": parent_len,
                    "blocks": pb - pa,
                    "is_reference": False,
                })
            else:
                child_groups = _split_parent_to_children_by_size(
                    parent_blocks, CHILD_TARGET_SIZE, CHILD_MIN_SIZE
                )
                for cblocks in child_groups:
                    clen = sum(blk["len"] for blk in cblocks)
                    chead = cblocks[0]["text"].strip().replace("\n", " ")[:60]
                    children.append({
                        "parent_idx": parent_idx,
                        "section_title": sec_title,
                        "head": chead,
                        "pg_start": cblocks[0]["pg"],
                        "len": clen,
                        "blocks": len(cblocks),
                        "is_reference": False,
                    })

    stats = {
        "body_blocks_total": len(flat_full),
        "body_blocks_kept": len(flat),
        "truncated_blocks": truncated_blocks,
        "truncated_chars": truncated_chars,
        "ref_dropped_blocks": ref_dropped_blocks,
        "ref_dropped_chars": ref_dropped_chars,
        "n_sections": len(section_splits),
        "n_parents": len(parents),
        "n_children": len(children),
    }
    return {"parents": parents, "children": children, "stats": stats}


# ─────────────────────────────────────────────────────────────────────
# 报表
# ─────────────────────────────────────────────────────────────────────


def _percentiles(vals: list[int], pcts: list[float]) -> dict[float, int]:
    s = sorted(vals)
    n = len(s)
    return {p: s[min(n - 1, int(n * p))] for p in pcts}


def _print_distribution(name: str, vals: list[int],
                        buckets: list[tuple[int, int]]) -> None:
    pp = _percentiles(vals, [0.0, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0])
    print(f"\n{name} size 字符:")
    print(f"  min={pp[0.0]} med={pp[0.5]} p75={pp[0.75]} p90={pp[0.9]} "
          f"p95={pp[0.95]} p99={pp[0.99]} max={pp[1.0]}")
    n = len(vals)
    print(f"\n{name} 分桶:")
    for lo, hi in buckets:
        c = sum(1 for x in vals if lo <= x <= hi)
        print(f"  [{lo:>5}, {hi:>9}]: {c:>5}  ({100*c/n:.1f}%)")


def _verify_invariant(parents: list[dict], children: list[dict],
                      stats: dict, flat_kept_chars: int) -> None:
    p_sum = sum(p["len"] for p in parents)
    c_sum = sum(c["len"] for c in children)
    expected = flat_kept_chars - stats["ref_dropped_chars"]
    print(f"\n=== 字符守恒检查 ===")
    print(f"  body kept (after BODY_END trim): {flat_kept_chars}")
    print(f"  ref dropped:                     {stats['ref_dropped_chars']}")
    print(f"  expected (body - ref):           {expected}")
    print(f"  parents sum:                     {p_sum}  mismatch={p_sum - expected}")
    print(f"  children sum:                    {c_sum}  mismatch={c_sum - expected}")


def main() -> None:
    res = chunk_book()
    parents, children, stats = res["parents"], res["children"], res["stats"]

    # 重算 flat_kept_chars 用于 invariant 检查
    result = build_toc_dict()
    data = json.loads(Path(CONTENT_LIST_V2).read_text())
    body_start = max(result["toc_pages"]) + 1
    flat_full = _flatten_blocks(data, body_start)
    body_end = _find_body_end(flat_full)
    flat_kept = flat_full[:body_end]
    flat_kept_chars = sum(b["len"] for b in flat_kept)

    print("=" * 70)
    print("切分流程结果")
    print("=" * 70)
    print(f"书末截断:    丢弃 {stats['truncated_blocks']} blocks "
          f"/ {stats['truncated_chars']} 字符")
    print(f"参考文献丢弃: 丢弃 {stats['ref_dropped_blocks']} blocks "
          f"/ {stats['ref_dropped_chars']} 字符")
    print(f"父块数(节):  {stats['n_sections']}")
    print(f"父块数(切): {stats['n_parents']}  "
          f"(被二次切的 section: {sum(1 for p in parents if p['is_split_from_section'])} 个新增父块)")
    print(f"子块数:       {stats['n_children']}")

    parent_lens = [p["len"] for p in parents]
    child_lens = [c["len"] for c in children]
    parent_buckets = [
        (0, 499), (500, 1999),
        (2000, PARENT_SPLIT_THRESHOLD - 1),
        (PARENT_SPLIT_THRESHOLD, 4999),
        (5000, 9999), (10000, 19999), (20000, 79999),
    ]
    child_buckets = [
        (0, CHILD_MIN_SIZE - 1),
        (CHILD_MIN_SIZE, 499),
        (500, 999), (1000, 1999),
        (2000, 4999), (5000, 9999), (10000, 10**9),
    ]
    _print_distribution("父块", parent_lens, parent_buckets)
    _print_distribution("子块", child_lens, child_buckets)

    big_parents = sorted([p for p in parents if p["len"] > PARENT_SPLIT_THRESHOLD],
                         key=lambda p: -p["len"])
    print(f"\n仍 > {PARENT_SPLIT_THRESHOLD} 字父块: {len(big_parents)} 个")
    for p in big_parents[:15]:
        print(f"  pg={p['pg_start']:>4}  size={p['len']:>6}  {p['head'][:50]}  "
              f"(节={p['section_title'][:25]})")

    print(f"\n[最大 5 父块]")
    for p in sorted(parents, key=lambda x: -x["len"])[:5]:
        print(f"  size={p['len']:>6}  {p['title']}")

    print(f"\n[最小 5 父块]")
    for p in sorted(parents, key=lambda x: x["len"])[:5]:
        print(f"  size={p['len']:>6}  {p['title']}")

    print(f"\n[最大 5 子块]")
    for c in sorted(children, key=lambda x: -x["len"])[:5]:
        print(f"  size={c['len']:>6}  节={c['section_title'][:25]}  head={c['head'][:40]}")

    _verify_invariant(parents, children, stats, flat_kept_chars)


if __name__ == "__main__":
    main()
