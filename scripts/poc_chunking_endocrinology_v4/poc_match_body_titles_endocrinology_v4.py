"""
POC: 用权威字典给正文 title block 打 level + heading_path
====================================================================
**只针对《内分泌代谢病学 第4版上册》POC** —— 字典构建依赖
poc_build_toc_dict_endocrinology_v4.py(本书规则)。

输入:权威字典(只含 L1-L3 的 184 条)
正文范围:page_idx > max(toc_pages),即 TOC 之后的所有页
候选范围:仅 type=="title" 的 block

字典只放 L1-L3 的原因
---------------------
N.N(L4)在正文中根本不展开:扩展资源在正文里就是 L3 父块下几段 paragraph,
不再细分小节。L4 不参与 body 匹配。

mineru 普遍行为带来的两类预处理(非本书特例,通用):
  A1. 章拆分修复:正文里"第N章 + 章名"被拆成两个连续 title block,合并
  A2. 篇前缀重建:正文里篇标题只剩"主标题"(丢失了"第N篇"前缀),
                  从字典 L1 反构 alias dict 反查重建

匹配流程:
  正文 title raw → [章合并 / 篇重建] → _normalize() → lookup
    ├─ 0 候选 → unmatched(预期含【...】/(一)等 L5/L6 子节,不切到这粒度)
    ├─ 1 候选 → 直接采纳
    └─ N 候选(同名) → stack[:level-1] 父链消歧

输出三份清单:
  matched   ─ 成功匹配的正文 title(带 page/blk_idx + heading_path + 预处理标记)
  missing   ─ 字典里有但正文未出现(可能 mineru 漏识别正文标题)
  unmatched ─ 正文有 type=title 但字典找不到(预期含【...】/(一)等更细子节)
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from poc_build_toc_dict_endocrinology_v4 import (
    CONTENT_LIST_V2,
    TAIL_PAGE_RE,
    _normalize,
    _text_of,
    _update_stack,
    build_toc_dict,
    strict_key,
)

# A1 章合并:title block 文本是纯"第N章"(无章名)→ 找下一个相邻 title 合并
CHAP_ALONE_RE = re.compile(r"^第\s*\d+\s*章\s*$")

# A2 篇前缀重建:从字典 L1 标题反提"主标题部分"用作 alias key
PART_PREFIX_RE = re.compile(r"^第\s*\S+\s*篇\s*(.+)$")

# A3 mini-TOC paragraph:正文里以 paragraph 形式出现的目录项,需带页码尾巴
# 才被采纳(避免长 paragraph 含节名子串被误识别为标题)
TAIL_PAGE_HAS_NUM_RE = re.compile(r"(?:[…\.]{2,}|\s|/)\s*\(?\d+\)?\s*$")


def _build_part_alias(entries: list) -> dict[str, str]:
    """从字典 L1 entries 构造 {strict_key(主标题部分): 完整L1标题}。
    例:{"内分泌代谢病学技术": "第1篇 内分泌代谢病学技术", ...}
    """
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

    预处理:
      A1 章合并:'第N章' + '章名' 两个相邻 title block → 合并
      A2 篇前缀重建:'主标题' (丢失'第N篇') → 反查字典补全
      A3 mini-TOC paragraph:paragraph 末尾带页码 + strict_key 字典命中 → 采纳

    Returns: [(pg_idx, blk_idx, raw_or_merged, lookup_key, action), ...]
        action ∈ {"AS_IS", "CHAP_MERGED", "PART_REBUILT", "MINI_TOC_PARA"}
        lookup_key 是 strict_key(去全部空白)形式
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

                # A1 章合并
                if CHAP_ALONE_RE.match(raw.strip()):
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

                # A2 篇前缀重建
                key = strict_key(raw)
                if key in part_alias:
                    out.append((pg_idx, i, raw, strict_key(part_alias[key]), "PART_REBUILT"))
                    continue

                out.append((pg_idx, i, raw, key, "AS_IS"))

            elif btype == "paragraph":
                # A3 mini-TOC paragraph:严格双重条件,误识别风险低
                raw = _text_of(b.get("content", {}).get("paragraph_content", []))
                if not raw:
                    continue
                # 条件 a:末尾必须像页码尾巴
                if not TAIL_PAGE_HAS_NUM_RE.search(raw.strip()):
                    continue
                # 条件 b:strict_key 命中字典
                key = strict_key(raw)
                if key not in lookup:
                    continue
                out.append((pg_idx, i, raw, key, "MINI_TOC_PARA"))

    return out


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

    print(f"=== Dict for matching: {len(lookup)} unique keys (L1-L3 only) ===")
    print(f"=== Total entries (L1-L4 incl. for tree view): {len(entries)} ===")
    print(f"=== Body scan range: page_idx {body_start}..{len(data) - 1} ===")
    print(f"=== Part aliases (for A2 rebuild): {part_alias} ===\n")

    # 字典 key 冲突
    conflicts = {k: v for k, v in lookup.items() if len(v) > 1}
    print(f"=== Dict key conflicts: {len(conflicts)} ===\n")

    # 收集预处理后的候选
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

        # stack / path 用字典原 title(带空格的展示形式),保持显示干净
        stack = _update_stack(stack, level, dict_title)
        full_path = (parent_path + " / " + dict_title) if parent_path else dict_title
        matched.append((pg_idx, blk_idx, raw, key, level, full_path, status, action))

    matched_keys = {m[3] for m in matched}  # 都是 strict_key 形式
    # missing 只看 L1-L3(L4 字典里就没放,不参与)
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
    print(f"  (预期含【...】/(一)/...等 L5/L6 子节,不切到这粒度)\n")
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
