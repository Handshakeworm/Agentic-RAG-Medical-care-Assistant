"""scripts/derive_chunks_for_pg.py — 派生 PG-ready chunk records 从 POC 输出。

不动 POC 代码,在此聚合:
- 调 POC chunk_book + helper 拿 parents/children + real_start_pos
- align parents 与 sections,补 sec_path 元数据
- 派生 heading_path(spec §3.1.4.2;split parent 同 sec_path 第二次起加 head 子级)
- 应用 PATCH_HEADING_PATH_OVERRIDES 修正 mineru 页眉误标 type=title 导致的 stack 污染错位

下游消费:P1 灌 PG 脚本 + Embedding pipeline。

用法:
    python scripts/derive_chunks_for_pg.py            # audit 全 12 本,打印重复统计
    python scripts/derive_chunks_for_pg.py <book_dir>  # 派生单本,JSON 输出
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# ─────────────────────────────────────────────────────────────────────
# 手动 patch:mineru 把页眉误标为 type=title,POC 内部 _real_start_positions
# stack 状态被污染,导致部分章节内容被错挂到上一 section 下产生错位 split。
# 因切分边界本身正确(字符守恒 ✓),只在派生层手动覆盖 heading_path 即可,
# chunk_raw_text 内容完整无损。每条 entry 表示:这个父块的 sec_title 标错,
# 真实 heading_path 应是这条字符串。
# ─────────────────────────────────────────────────────────────────────

PATCH_HEADING_PATH_OVERRIDES: dict[tuple[str, int], str] = {
    # 骨科 第三篇 第六章 第一节 胸椎黄韧带骨化症 — pg 730~745
    # 根因:第三篇页眉"第三篇 脊柱"被 mineru 误标 type=title,
    # 污染 stack[0] 后 二、病理及分型 / 三、临床表现 / 四、诊断 / 五、治疗与康复 4 个
    # REAL_START 丢失,内容被错挂到"一、病因"section 下产生 7 个 split parent。
    # 切分边界 OK,只是 sec_title 错;手动指向真实 section。
    ("poc_chunking_骨科", 440): "第三篇 脊柱 > 第六章 胸椎管狭窄症 > 第一节 胸椎黄韧带骨化症 > 三、临床表现 > (一) 症状和体征",
    ("poc_chunking_骨科", 441): "第三篇 脊柱 > 第六章 胸椎管狭窄症 > 第一节 胸椎黄韧带骨化症 > 三、临床表现 > （二）影像学表现",
    ("poc_chunking_骨科", 442): "第三篇 脊柱 > 第六章 胸椎管狭窄症 > 第一节 胸椎黄韧带骨化症 > 五、治疗与康复 > (一) 手术原则",
    ("poc_chunking_骨科", 443): "第三篇 脊柱 > 第六章 胸椎管狭窄症 > 第一节 胸椎黄韧带骨化症 > 五、治疗与康复 > （二）手术方法",
    ("poc_chunking_骨科", 444): "第三篇 脊柱 > 第六章 胸椎管狭窄症 > 第二节 胸椎后纵韧带骨化症",

    # 心血管 — 第十篇 第三十八章 心肌病防治 第二节
    # 字典只到节级,节内"一、扩张型 / 二、肥厚型 / 三、限制型"等数字编号子节没在字典里,
    # POC 把整节切了 14 个 split parents,(六)治疗 在 一、扩张型 和 二、肥厚型 各出现 1 次撞名。
    ("poc_chunking_心血管内科学_第3版", 374): "第十篇 心肌、心内膜、心包、肺血管疾病 > 第三十八章 心肌病防治 > 第二节 各类心肌病的诊治原则 > 一、扩张型心肌病 > (六) 治疗",
    ("poc_chunking_心血管内科学_第3版", 380): "第十篇 心肌、心内膜、心包、肺血管疾病 > 第三十八章 心肌病防治 > 第二节 各类心肌病的诊治原则 > 二、肥厚型心肌病 > (六) 治疗",

    # 消化 — pg 142~150 区域 stack 污染,sec_title 错位
    # pid=58 真实是 第七节 食管憩室 > 九、预后(POC 错挂第八节 七、治疗)
    # pid=59 真实是 第八节 食管囊肿 > 八、预后
    ("poc_chunking_消化系统与疾病_第2版", 58): "第二章 食管疾病 > 第七节 食管憩室 > 九、预后",
    ("poc_chunking_消化系统与疾病_第2版", 59): "第二章 食管疾病 > 第八节 食管囊肿 > 八、预后",

    # 普外 — mineru 把同一 title block 在两个 pg 各 emit 一次(双栏 OCR 重复或排版重复),
    # POC 切 split 时拿到两个相同 head 锚点,只能用 #N 序号区分。
    # pid=373 pg=536 + pid=375 pg=538 是 "6. 脾动脉结扎术" 双 emit
    ("poc_chunking_普通外科", 373): "第十章 脾脏及门静脉高压症 > 第三节 保脾手术的历史争议、共识与 手术方式 > 6. 脾动脉结扎术 #1",
    ("poc_chunking_普通外科", 375): "第十章 脾脏及门静脉高压症 > 第三节 保脾手术的历史争议、共识与 手术方式 > 6. 脾动脉结扎术 #2",
    # pid=390 pg=555 + pid=391 pg=557 是 "(二)主动脉夹层" 双 emit
    ("poc_chunking_普通外科", 390): "第十一章 血管外科疾病 > 第一节 主动脉瘤和主动脉夹层 > （二）主动脉夹层（aortic dissection, AD）的病理生理和血流动力学研究 #1",
    ("poc_chunking_普通外科", 391): "第十一章 血管外科疾病 > 第一节 主动脉瘤和主动脉夹层 > （二）主动脉夹层（aortic dissection, AD）的病理生理和血流动力学研究 #2",
}


# ─────────────────────────────────────────────────────────────────────
# 派生主流程
# ─────────────────────────────────────────────────────────────────────


def _load_poc(book_dir: str):
    """import 一本 POC,清缓存避免跨本污染。"""
    for m in list(sys.modules):
        if "poc_" in m or m == "poc_chunk_book" or m == "poc_build_toc_dict" or m == "poc_match_body_titles":
            sys.modules.pop(m, None)
    sys.path[:] = [p for p in sys.path if "poc_chunking_" not in p]
    sys.path.insert(0, str(REPO_ROOT / "scripts" / book_dir))
    return importlib.import_module("poc_chunk_book")


def _align_parents_with_sections(parents: list[dict], sections: list[tuple]) -> list[dict]:
    """对齐 parents 顺序与 real_start_pos 顺序,跳过 absorb 掉的 section。

    sections: [(level, dict_title, full_path), ...] 按 pos 升序。
    返回的 dict 多带一个 _sec_path 字段。
    """
    aligned = []
    j = 0
    for i, p in enumerate(parents):
        # 跳过 absorbed sections(real_start_pos 有但 POC 没产出 parent 的)
        while j < len(sections):
            if sections[j][0] == p["level"] and sections[j][1] == p["section_title"]:
                break
            j += 1
        if j >= len(sections):
            aligned.append({**p, "_sec_path": None})
            continue
        aligned.append({**p, "_sec_path": sections[j][2]})
        # 推进 j:下个 parent 不属于当前 section 时
        if i + 1 < len(parents):
            np = parents[i + 1]
            if np["level"] != p["level"] or np["section_title"] != p["section_title"]:
                j += 1
        else:
            j += 1
    return aligned


def _derive_heading_paths(book_dir: str, aligned: list[dict]) -> list[dict]:
    """A 方案:同 sec_path 内 split 第二次起加 head 子级,然后应用手动 override。"""
    out = []
    last_sec = None
    for p in aligned:
        sec_path = p["_sec_path"] or p["section_title"]
        sec_disp = sec_path.replace(" / ", " > ")

        # 应用 override(优先级最高,直接替换)
        override_key = (book_dir, p["parent_idx"])
        if override_key in PATCH_HEADING_PATH_OVERRIDES:
            heading_path = PATCH_HEADING_PATH_OVERRIDES[override_key]
        else:
            is_cont = p["is_split_from_section"] and sec_path == last_sec
            heading_path = sec_disp + " > " + p["head"] if is_cont else sec_disp

        out.append({**p, "heading_path": heading_path})
        last_sec = sec_path  # 跟踪 POC 视角的 sec_path,不受 override 影响
    return out


def derive_for_book(book_dir: str) -> dict[str, Any]:
    """派生一本书的 chunk records,返回 {parents, children, stats}。"""
    M = _load_poc(book_dir)
    chunk_result = M.chunk_book()
    parents = chunk_result["parents"]
    children = chunk_result["children"]

    # 调 POC helper 拿 real_start_pos 全量(含 full_path)
    toc_result = M.build_toc_dict()
    data = json.loads(Path(M.CONTENT_LIST_V2).read_text())
    body_start = max(toc_result["toc_pages"]) + 1
    flat_full = M._flatten_blocks(data, body_start)
    body_end = M._find_body_end(flat_full)
    flat = flat_full[:body_end]
    real_start_pos = M._real_start_positions(flat, toc_result)
    sections = [real_start_pos[a] for a in sorted(real_start_pos)]

    aligned_parents = _align_parents_with_sections(parents, sections)
    derived_parents = _derive_heading_paths(book_dir, aligned_parents)

    # children 继承 parent 的 heading_path(同 heading 节,只是 relative_chunk_index 不同)
    parent_hp_by_idx = {p["parent_idx"]: p["heading_path"] for p in derived_parents}
    derived_children = [
        {**c, "heading_path": parent_hp_by_idx[c["parent_idx"]]} for c in children
    ]

    return {
        "book_dir": book_dir,
        "parents": derived_parents,
        "children": derived_children,
        "stats": chunk_result["stats"],
    }


# ─────────────────────────────────────────────────────────────────────
# 全 12 本 audit
# ─────────────────────────────────────────────────────────────────────

ALL_BOOKS = [
    "poc_chunking_诊断学_第10版",
    "poc_chunking_内分泌代谢病学_第4版上册",
    "poc_chunking_心血管内科学_第3版",
    "poc_chunking_协和呼吸病学_第二版",
    "poc_chunking_内科学_第9版",
    "poc_chunking_神经内科学",
    "poc_chunking_神经外科学",
    "poc_chunking_消化系统与疾病_第2版",
    "poc_chunking_胸心外科",
    "poc_chunking_普通外科",
    "poc_chunking_骨科",
    "poc_chunking_泌尿外科",
]


def audit_all():
    print(f'{"书名":<45s} | n_p   | unique | dup | overrides')
    print("-" * 80)
    total_p, total_dup = 0, 0
    dups_by_book = {}
    for book in ALL_BOOKS:
        try:
            r = derive_for_book(book)
            parents = r["parents"]
            n_p = len(parents)
            n_u = len(set(p["heading_path"] for p in parents))
            n_overrides = sum(
                1 for p in parents if (book, p["parent_idx"]) in PATCH_HEADING_PATH_OVERRIDES
            )
            dup = n_p - n_u
            flag = "✓" if dup == 0 else "✗"
            print(
                f"{book:<45s} | {n_p:5d} | {n_u:6d} | {dup:3d} | {n_overrides:3d}  {flag}"
            )
            total_p += n_p
            total_dup += dup
            if dup > 0:
                from collections import Counter
                ctr = Counter(p["heading_path"] for p in parents)
                dups_by_book[book] = [(hp, n) for hp, n in ctr.items() if n > 1]
        except Exception as e:
            print(f"{book:<45s} | ERROR: {type(e).__name__}: {e}")
    print("-" * 80)
    print(f'{"TOTAL":<45s} | n_p={total_p}  dup={total_dup}')

    if dups_by_book:
        print("\n=== 剩余重复明细 ===")
        for book, items in dups_by_book.items():
            print(f"\n{book}:")
            for hp, n in items:
                print(f"  [{n}x] {hp!r}")


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        book = sys.argv[1]
        result = derive_for_book(book)
        print(f"book={book}")
        print(f"parents={len(result['parents'])} children={len(result['children'])}")
        print(f"stats={result['stats']}")
    else:
        audit_all()
