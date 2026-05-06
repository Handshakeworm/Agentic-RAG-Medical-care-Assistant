"""
Step 2 定位准确性抽检
====================================================================
对每种 action 抽样,展示 (pg, blk) 前后 3 个 block 的内容,
对照 dict_title 看位置是否落在标题块上(或之后正文起点)。

对每种 action 抽 5 个样本(若不足全打):
  AS_IS / CHAP_MERGED / PART_REBUILT / PAGE_HEADER_FB / FUZZY_TITLE
  + L4 (一、/[附]) 单独抽 5 个(因为是本书新增层级)

输出每个抽样:
  dict_title (字典预期)
  ──────────────
  pg=X blk=Y-2 type=... text=...
  pg=X blk=Y-1 type=... text=...
  ▶pg=X blk=Y   type=... text=...   ← matched 位置
  pg=X blk=Y+1 type=... text=...
  pg=X blk=Y+2 type=... text=...
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from poc_build_toc_dict import CONTENT_LIST_V2, _text_of, build_toc_dict, strict_key
from poc_match_body_titles import _build_part_alias, _collect_candidates


def block_brief(b: dict) -> str:
    t = b.get("type", "?")
    c = b.get("content", {})
    if t == "title":
        txt = _text_of(c.get("title_content", []))
    elif t == "paragraph":
        txt = _text_of(c.get("paragraph_content", []))
    elif t == "list":
        items = c.get("list_items", [])
        txt = " | ".join(_text_of(it.get("item_content", [])) for it in items[:2])
        if len(items) > 2:
            txt += f" ...(+{len(items)-2})"
    elif t == "page_header":
        txt = _text_of(c.get("page_header_content", []))
    elif t == "image":
        txt = "[image]"
    elif t == "table":
        txt = "[table]"
    else:
        txt = ""
    return f"{t:13s} len={len(txt):4d}  {txt[:90]!r}"


def audit() -> None:
    result = build_toc_dict()
    lookup = result["lookup"]
    entries = result["entries"]
    toc_pages = result["toc_pages"]

    data = json.loads(Path(CONTENT_LIST_V2).read_text())
    body_start = max(toc_pages) + 1
    part_alias = _build_part_alias(entries)
    candidates = _collect_candidates(data, body_start, part_alias, lookup)

    # 收集 matched + dict_title
    matched: list[dict] = []
    for pg, blk, raw, key, action in candidates:
        cands = lookup.get(key, [])
        if not cands:
            continue
        level, parent_path, dict_title = cands[0]
        matched.append({
            "pg": pg, "blk": blk, "raw": raw, "key": key,
            "action": action, "level": level, "dict_title": dict_title,
            "parent_path": parent_path,
        })

    by_action: dict[str, list[dict]] = defaultdict(list)
    by_l4: list[dict] = []
    for m in matched:
        by_action[m["action"]].append(m)
        if m["level"] == 4:
            by_l4.append(m)

    print(f"matched 总数: {len(matched)}")
    print(f"by action: {dict((a, len(v)) for a, v in by_action.items())}")
    print(f"L4 (一、/[附]): {len(by_l4)}")
    print()

    def show(samples: list[dict], group_name: str, n: int = 5) -> None:
        print("=" * 75)
        print(f"## {group_name} (抽 {min(n, len(samples))} / {len(samples)})")
        print("=" * 75)
        # 等距抽样,避免都是头部样本
        if len(samples) <= n:
            picks = samples
        else:
            step = len(samples) // n
            picks = [samples[i * step] for i in range(n)]
        for m in picks:
            pg, blk = m["pg"], m["blk"]
            print(f"\n  dict_title: {m['dict_title']!r}  (L{m['level']}, action={m['action']})")
            print(f"  parent_path: {m['parent_path']!r}")
            print(f"  matched at: pg={pg} blk={blk}")
            blocks = data[pg]
            for j in range(max(0, blk - 2), min(len(blocks), blk + 3)):
                marker = " ▶" if j == blk else "  "
                print(f"  {marker}pg={pg:3d} blk={j:2d}  {block_brief(blocks[j])}")

    show(by_action.get("AS_IS", []), "AS_IS (主力)", 5)
    show(by_action.get("CHAP_MERGED", []), "CHAP_MERGED (篇/章合并)", 5)
    show(by_action.get("PART_REBUILT", []), "PART_REBUILT (主标题反查)", 5)
    show(by_action.get("FUZZY_TITLE", []), "FUZZY_TITLE (OCR 错字救)", 5)
    show(by_action.get("PAGE_HEADER_FB", []), "PAGE_HEADER_FB (兜底)", 5)
    show(by_l4, "L4 一、/[附] (本书新层级)", 6)


if __name__ == "__main__":
    audit()
