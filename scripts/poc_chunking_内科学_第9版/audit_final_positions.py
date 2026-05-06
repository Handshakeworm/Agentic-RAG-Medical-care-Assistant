"""
Step 3 _real_start_pos 选择后的最终位置抽检
====================================================================
candidates 阶段 PAGE_HEADER_FB 看似错位,实际上 _real_start_pos 用
"strong + last position" 规则会优选 AS_IS(章首 gap 大算 strong),
PAGE_HEADER_FB 只在该章没有 AS_IS 时才会被选(真兜底)。

本脚本展示**最终位置** — 即 Step 3 实际会用的 (pg, blk) — 并按 action 抽样。
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from poc_build_toc_dict import CONTENT_LIST_V2, build_toc_dict, _text_of
from poc_chunk_book import _flatten_blocks, _real_start_positions


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

    # 重跑 _real_start_positions 但保留 action 信息
    # 这里我直接复用 _real_start_positions 拿到 chosen 位置 + 反查 action
    real_start = _real_start_positions(flat, result)

    # 拿 candidates 反查每个 chosen 的 action
    from poc_match_body_titles import _build_part_alias, _collect_candidates
    from poc_build_toc_dict import strict_key
    part_alias = _build_part_alias(result["entries"])
    cands = _collect_candidates(data, body_start, part_alias, result["lookup"])
    pos_map = {(b["pg"], b["blk"]): i for i, b in enumerate(flat)}
    # (flat_pos) → list of (action, key)
    pos_to_actions: dict[int, list[str]] = defaultdict(list)
    for pg, blk, raw, key, action in cands:
        p = pos_map.get((pg, blk))
        if p is not None:
            pos_to_actions[p].append(action)

    # final positions:每个 dict entry 的 chosen flat_pos
    finals: list[dict] = []
    for flat_pos, (lvl, dict_title) in real_start.items():
        b = flat[flat_pos]
        actions = pos_to_actions.get(flat_pos, [])
        # 这个 chosen pos 是哪个 action?(取 candidate 里命中此 pos 的第一个)
        chosen_action = actions[0] if actions else "UNKNOWN"
        finals.append({
            "flat_pos": flat_pos, "level": lvl, "dict_title": dict_title,
            "pg": b["pg"], "blk": b["blk"], "type": b["type"],
            "actions_at_pos": actions, "chosen_action": chosen_action,
        })

    print(f"=== 最终选定 sections: {len(finals)} (字典 L1-L4 共 {len(result['lookup'])}) ===")
    cnt = Counter(f["chosen_action"] for f in finals)
    print(f"按最终位置的 action 分布: {dict(cnt)}\n")

    # 抽检:看几个原本担心 PAGE_HEADER_FB 错位的章,确认最终选了 AS_IS
    print("=" * 75)
    print("## 关键章节最终定位(原 PAGE_HEADER_FB 抽样问题)")
    print("=" * 75)
    targets = [
        "第九章 心包疾病", "第二十三章 慢性腹泻", "第十三章 脾功能亢进",
        "第十二章 骨髓增殖性肿瘤", "第十八章 原发性甲状旁腺功能亢进症",
    ]
    for t in targets:
        for f in finals:
            if f["dict_title"] == t:
                marker = "✅" if f["chosen_action"] == "AS_IS" else "⚠️"
                print(f"\n  {marker} {t} (L{f['level']})")
                print(f"     最终位置: pg={f['pg']} blk={f['blk']}  type={f['type']}")
                print(f"     位置上 candidates 的 actions: {f['actions_at_pos']}")
                # 显示该位置前后内容
                blocks = data[f['pg']]
                for j in range(max(0, f['blk']-1), min(len(blocks), f['blk']+2)):
                    mk = " ▶" if j == f['blk'] else "  "
                    print(f"    {mk}pg={f['pg']:3d} blk={j:2d}  {block_brief(blocks[j])}")
                break

    # 还要看真正只能靠 PAGE_HEADER_FB 的章(没有 AS_IS 命中)— 这些才是潜在问题
    print("\n" + "=" * 75)
    print("## 真兜底:最终位置只有 PAGE_HEADER_FB 命中的章(可能确实错位)")
    print("=" * 75)
    only_fb = [f for f in finals if f["chosen_action"] == "PAGE_HEADER_FB"]
    print(f"\n共 {len(only_fb)} 个,前 10 个详情:\n")
    for f in only_fb[:10]:
        print(f"  ⚠ {f['dict_title']} (L{f['level']})")
        print(f"    最终位置: pg={f['pg']} blk={f['blk']}  type={f['type']}")
        print(f"    位置上 actions: {f['actions_at_pos']}")
        blocks = data[f['pg']]
        for j in range(max(0, f['blk']-1), min(len(blocks), f['blk']+2)):
            mk = " ▶" if j == f['blk'] else "  "
            print(f"   {mk}pg={f['pg']:3d} blk={j:2d}  {block_brief(blocks[j])}")
        print()


if __name__ == "__main__":
    main()
