"""
POC: 用权威字典给正文 title block 打 level + heading_path
====================================================================
**只针对《诊断学 第10版》POC** —— 字典构建依赖
poc_build_toc_dict.py(本书规则)。

输入:权威字典(L1-L3 共 ~212 条)
正文范围:page_idx > max(toc_pages),即 TOC 之后的所有页

跟内分泌的差异(详见 BOOK_NOTES.md):
  - 字典只有 L1-L3,没有 L4(本书无 N.N 编号 / 扩展资源)
  - A1 合并扩展到**篇**:本书 pg 29 有 "第一篇" + "常见症状" 两个 title block 拆开,
    必须合并成 "第一篇 常见症状" 才能命中字典
  - normalize() 已处理 `|` 分隔符(见 poc_build_toc_dict.py)

mineru 普遍行为带来的两类预处理(同内分泌,通用):
  A1. 篇/章拆分修复:正文里"第N篇/第N章 + 主标题"被拆成两个连续 title block,合并
  A2. 主标题反查:正文里只剩"主标题"(丢了"第N篇/章"前缀),从字典 L1 反构 alias dict 反查重建

输出三份清单:
  matched   ─ 成功匹配的正文 title(带 page/blk_idx + heading_path + 预处理标记)
  missing   ─ 字典里有但正文未出现(可能 mineru 漏识别正文标题)
  unmatched ─ 正文有 type=title 但字典找不到(预期含【...】/1./2.等更细子节)
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from difflib import SequenceMatcher

from poc_build_toc_dict import (
    CONTENT_LIST_V2,
    _normalize,
    _text_of,
    _update_stack,
    build_toc_dict,
    strict_key,
)

# A5 fuzzy title 匹配阈值:用于救 OCR 错字章标题
# 例:正文 "第二章 般检查" (mineru 漏识别"一") 对字典 "第二章 一般检查"
# SequenceMatcher.ratio() = 7/(7+1) = 0.875,阈值 0.85 可命中
FUZZY_RATIO_THRESHOLD = 0.85
CHAP_PATTERN = re.compile(r"^第\s*\S{1,4}\s*章")

# A1 篇/章合并:title block 文本是纯"第N篇" 或 "第N章" (无主标题)→ 找下一个相邻 title 合并
# 内分泌只处理 \d+ 章,本书扩展到中文数字 + 篇
TITLE_ALONE_RE = re.compile(r"^第\s*\S{1,4}\s*[篇章]\s*$")

# A2 主标题反查:从字典 L1 标题反提"主标题部分"用作 alias key
PART_PREFIX_RE = re.compile(r"^第\s*\S+\s*篇\s*(.+)$")

# A3 mini-TOC paragraph:正文里以 paragraph 形式出现的目录项,需带页码尾巴
TAIL_PAGE_HAS_NUM_RE = re.compile(r"(?:[…\.]{2,}|\s|/)\s*\(?\d+\)?\s*$")


def _build_part_alias(entries: list) -> dict[str, str]:
    """从字典 L1 entries 构造 {strict_key(主标题部分): 完整L1标题}。"""
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
    """收集正文标题候选(type=title 全收;type=paragraph 仅 mini-TOC 形式)。

    Returns: [(pg_idx, blk_idx, raw_or_merged, lookup_key, action), ...]
        action ∈ {"AS_IS", "CHAP_MERGED", "PART_REBUILT", "MINI_TOC_PARA"}
    """
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

                # A1 篇/章合并(诊断学扩展:也处理"第N篇"独立)
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
                    # 找不到合并目标,降级原样

                # A2 主标题反查(只对 L1 篇适用)
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
                # A4 (诊断学专属):某些章标题在正文里没有独立 title block,
                # 只出现在 page_header 里作为章页眉(每页重复)。
                # **关键**:把章边界设在该 page 的开头(pg_idx, 0),
                # 而不是 page_header 自身位置(可能在 page 末尾)。
                # 语义:page_header 表示"本页属于此章",所以本页全部内容应归此章。
                # 例:pg 115 blk 11 是 page_header,但 blk 0-10 已是本章正文,
                # 用 blk 11 作边界会把本章 paragraph 误归上一章。
                raw = _text_of(b.get("content", {}).get("page_header_content", []))
                if not raw:
                    continue
                key = strict_key(raw)
                if key not in lookup:
                    continue
                out.append((pg_idx, 0, raw, key, "PAGE_HEADER_FB"))

    # A5 fuzzy title 匹配(救 mineru OCR 错字章标题)
    # 例:pg 113 blk 0 title="第二章 般检查"(漏一字),fuzzy 匹配字典"第二章 一般检查"
    # 只对"含 第N章 模式但 strict_key 不命中"的 title 做(已 AS_IS 命中的不重做)
    chapter_dict_keys = {
        k: cands for k, cands in lookup.items()
        if any(c[0] == 2 for c in cands)  # 只考虑 L2 章
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
                continue  # 已被 AS_IS 命中
            # fuzzy 找最相似的 dict 章
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
    # 其他 action 保留全部候选,让下游用"strong 信号 + 最后位置"规则选 REAL_START
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

    # 字典只参与匹配的部分:L1-L3
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

    # ── matched 输出 ──────────────────────────────────────────────
    print(f"=== Matched: {len(matched)} ===")
    status_cnt = Counter(m[6] for m in matched)
    matched_action_cnt = Counter(m[7] for m in matched)
    print(f"  Status: {dict(status_cnt)}")
    print(f"  Action: {dict(matched_action_cnt)}\n")
    print("  (showing first 60)")
    for pg, blk, raw, key, level, path, status, action in matched[:60]:
        tag = f"{status}/{action}"
        print(f"  pg={pg:4d} blk={blk:3d} L{level} [{tag:20s}] {path}")
    if len(matched) > 60:
        print(f"  ... ({len(matched) - 60} more matched lines)")

    # ── missing 输出 ──────────────────────────────────────────────
    print(f"\n=== Missing (in L1-L3 dict but not found in body): {len(missing)} ===")
    miss_by_level = Counter(m[0] for m in missing)
    print(f"  By level: {dict(miss_by_level)}\n")
    for level, title, path, pg in missing[:50]:
        print(f"  L{level} (toc_pg={pg}) {path}")
    if len(missing) > 50:
        print(f"  ... ({len(missing) - 50} more missing entries)")

    # ── unmatched 输出 ────────────────────────────────────────────
    print(f"\n=== Unmatched (body type=title but lookup failed): {len(unmatched)} ===")
    print(f"  (预期含【...】/1./2./...等更细子节,不切到这粒度)\n")
    print("  (showing first 50 samples)")
    for pg, blk, raw, key, action in unmatched[:50]:
        raw_show = raw.replace("\n", "\\n")
        print(f"  pg={pg:4d} blk={blk:3d} raw='{raw_show[:70]}' norm='{key[:70]}'")
    if len(unmatched) > 50:
        print(f"  ... ({len(unmatched) - 50} more)")

    # ── 总结 ──────────────────────────────────────────────────────
    print(f"\n=== Summary ===")
    print(f"  Dict L1-L3:                  {len(lookup)}")
    print(f"  Body candidates (after pp):  {len(candidates)}")
    print(f"  Matched:                     {len(matched)}")
    print(f"    coverage of L1-L3 dict:    {100*(len(lookup)-len(missing))/max(len(lookup),1):.1f}%")
    print(f"  Missing in body (L1-L3):     {len(missing)}")
    print(f"  Unmatched in body:           {len(unmatched)}")


if __name__ == "__main__":
    main()
