"""
Step 3 _real_start_pos 选择后的最终位置抽检(神经外科学)
====================================================================
按 chosen_action 分布抽样,确认每个字典 entry 最终位置是不是落在正文 type=title 块上。
重点抽:
  - PATCH 修复的 2 个章名(第二十四章 / 第三十章)
  - 5 个 PAGE_HEADER_FB 在 candidates 但被 strong-AS_IS 覆盖的(实际选 0 个)
  - 各 篇/章/节 各抽 3 个
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from poc_build_toc_dict import CONTENT_LIST_V2, build_toc_dict, _text_of, strict_key
from poc_chunk_book import _flatten_blocks, _real_start_positions
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
    elif t == "page_header":
        txt = _text_of(c.get("page_header_content", []))
    else:
        txt = ""
    return f"{t:13s} {txt[:90]!r}"


def main() -> None:
    result = build_toc_dict()
    data = json.loads(Path(CONTENT_LIST_V2).read_text())
    body_start = max(result["toc_pages"]) + 1
    flat = _flatten_blocks(data, body_start)
    real_start = _real_start_positions(flat, result)

    part_alias = _build_part_alias(result["entries"])
    cands = _collect_candidates(data, body_start, part_alias, result["lookup"])
    pos_map = {(b["pg"], b["blk"]): i for i, b in enumerate(flat)}
    pos_to_actions: dict[int, list[str]] = defaultdict(list)
    for pg, blk, raw, key, action in cands:
        p = pos_map.get((pg, blk))
        if p is not None:
            pos_to_actions[p].append(action)

    finals: list[dict] = []
    for flat_pos, (lvl, dict_title) in real_start.items():
        b = flat[flat_pos]
        actions = pos_to_actions.get(flat_pos, [])
        chosen_action = actions[0] if actions else "UNKNOWN"
        finals.append({
            "flat_pos": flat_pos, "level": lvl, "dict_title": dict_title,
            "pg": b["pg"], "blk": b["blk"], "type": b["type"],
            "actions_at_pos": actions, "chosen_action": chosen_action,
        })

    print(f"=== 最终选定 sections: {len(finals)} (字典 L1-L3 unique keys: {len(result['lookup'])}) ===")
    cnt = Counter(f["chosen_action"] for f in finals)
    print(f"按最终位置的 action 分布: {dict(cnt)}\n")

    # 统计:每种 type 的最终位置 + 是否真的是 title block
    by_type = Counter(f["type"] for f in finals)
    print(f"按最终位置 block type:    {dict(by_type)}")
    non_title = [f for f in finals if f["type"] != "title"]
    if non_title:
        print(f"  ⚠ {len(non_title)} 个 entry 最终位置不是 type=title:")
        for f in non_title[:10]:
            print(f"    L{f['level']} {f['dict_title']!r} → pg={f['pg']} blk={f['blk']} type={f['type']}")
    else:
        print("  ✓ 全部 entry 最终位置都是 type=title block")

    # 抽检 1:PATCH 修补的 2 章
    print("\n" + "=" * 75)
    print("## 抽检 1:硬编码 PATCH 修补的章 (核心,不能错)")
    print("=" * 75)
    patched = ["第二十四章 功能性神经外科与其他神经、精神疾病",
               "第三十章 小儿神经创伤与重症监护"]
    for t in patched:
        for f in finals:
            if f["dict_title"] == t:
                print(f"\n  L{f['level']} {t}")
                print(f"     最终位置: pg={f['pg']} blk={f['blk']}  type={f['type']}")
                print(f"     candidates actions: {f['actions_at_pos']}")
                blocks = data[f['pg']]
                for j in range(max(0, f['blk']-1), min(len(blocks), f['blk']+2)):
                    mk = " ▶" if j == f['blk'] else "  "
                    print(f"   {mk}pg={f['pg']:3d} blk={j:2d}  {block_brief(blocks[j])}")
                break

    # 抽检 2:7 个篇 anchor(L1)
    print("\n" + "=" * 75)
    print("## 抽检 2:全部 7 个篇 anchor (L1)")
    print("=" * 75)
    for f in finals:
        if f["level"] == 1:
            blocks = data[f['pg']]
            target_block = blocks[f['blk']] if f['blk'] < len(blocks) else None
            mark = "✓" if target_block and target_block.get("type") == "title" else "⚠"
            print(f"\n  {mark} {f['dict_title']!r} → pg={f['pg']} blk={f['blk']}")
            for j in range(max(0, f['blk']-1), min(len(blocks), f['blk']+3)):
                mk = " ▶" if j == f['blk'] else "  "
                print(f"   {mk}pg={f['pg']:3d} blk={j:2d}  {block_brief(blocks[j])}")

    # 抽检 3:章和节各等距抽 3 个
    print("\n" + "=" * 75)
    print("## 抽检 3:章 (L2) 等距抽 5 个")
    print("=" * 75)
    chapters = sorted([f for f in finals if f["level"] == 2], key=lambda x: x["flat_pos"])
    step = max(1, len(chapters) // 5)
    for f in chapters[::step][:5]:
        blocks = data[f['pg']]
        target = blocks[f['blk']] if f['blk'] < len(blocks) else None
        mark = "✓" if target and target.get("type") == "title" else "⚠"
        print(f"\n  {mark} {f['dict_title']!r} → pg={f['pg']} blk={f['blk']} type={f['type']}")
        for j in range(max(0, f['blk']-1), min(len(blocks), f['blk']+2)):
            mk = " ▶" if j == f['blk'] else "  "
            print(f"   {mk}pg={f['pg']:3d} blk={j:2d}  {block_brief(blocks[j])}")

    print("\n" + "=" * 75)
    print("## 抽检 4:节 (L3) 等距抽 5 个")
    print("=" * 75)
    sections = sorted([f for f in finals if f["level"] == 3], key=lambda x: x["flat_pos"])
    step = max(1, len(sections) // 5)
    for f in sections[::step][:5]:
        blocks = data[f['pg']]
        target = blocks[f['blk']] if f['blk'] < len(blocks) else None
        mark = "✓" if target and target.get("type") == "title" else "⚠"
        print(f"\n  {mark} {f['dict_title']!r} → pg={f['pg']} blk={f['blk']} type={f['type']}")
        for j in range(max(0, f['blk']-1), min(len(blocks), f['blk']+2)):
            mk = " ▶" if j == f['blk'] else "  "
            print(f"   {mk}pg={f['pg']:3d} blk={j:2d}  {block_brief(blocks[j])}")


if __name__ == "__main__":
    main()
