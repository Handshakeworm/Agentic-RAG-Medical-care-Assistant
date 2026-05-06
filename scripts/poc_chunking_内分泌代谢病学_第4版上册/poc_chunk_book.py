"""
POC 切分主流程(end-to-end,2026-05-03 final)
====================================================================
**只针对《内分泌代谢病学 第4版上册》POC**(规则书本特化,详见 METHODOLOGY)。

依赖:
  - poc_build_toc_dict(Step 1 目录字典)
  - poc_match_body_titles(Step 2 正文匹配)

本脚本职责(详见 METHODOLOGY §2 / §5):

  【全书层面】
    ① 书末截断(中文名词索引/英文缩略语索引/彩色插图标题作 body_end)

  【每节内】
    ② 参考文献丢弃(节内"参考文献"标题后整段丢弃,含 ref 条目+扩展资源占位)

  【父块构建,每节】
    ③ 节本身就是父块
    ④ 节 > 4000 字 → 三遍切【】+(一)+1.(逐级细化,带 level 标记)
    ⑤ 小父块(< 500 字)按级别合并:吸收方 level ≤ 被吸收方
       (禁止下级跨上级:1.→(二) / (一)→【】 等)

  【子块构建,每父块】
    ⑥ 父块 ≤ 1200 字 → 不切,1 child = parent 整段
    ⑦ 父块 > 1200 字 → 按 mineru block 累积切,目标 ~600 字/child
       (最小 200 字防孤儿,末段 < 300 字 backward 并入上一 child)

输出:
  - parents:    list[ParentChunk]  父块
  - children:   list[ChildChunk]   子块
  - stats:      含书末/参考文献丢弃统计 + 父子覆盖完整性

本书结果:1204 父块 (median 1346) + 3012 子块 (median 616),mismatch=0
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
RE_BRACE = re.compile(r"^【[^】]+】")                              # 粗:【临床表现】
RE_PAREN_CN = re.compile(r"^[（(][一二三四五六七八九十百]+[)）]")    # 中:(一)(二)
RE_NUM_DOT = re.compile(r"^\d+\s*[.、]\s")                          # 细:1. xxx / 2、xxx
RE_TABLE_TITLE = re.compile(r"^表\s*[\d\-]+")                      # 排除表
RE_FIG_TITLE = re.compile(r"^图\s*[\d\-]+")                        # 排除图

# 书末截断 marker(在节内出现这些 title 即截断,后续 block 全部丢弃)
BODY_END_MARKERS = ("中文名词索引", "英文缩略语索引", "彩色插图")
RE_BODY_END = re.compile(r"^(?:" + "|".join(BODY_END_MARKERS) + r")\s*$")

# 参考文献丢弃:节内出现 title 文本以"参考文献"开头,该位置之后的所有 block 全部丢弃
# (参考文献条目本身无医学知识价值,且后接的"扩展资源 N + N.N 列表"也是外部链接占位
#  对 RAG 召回是噪声;本书参考文献始终在扩展资源前,一刀切干净两个问题)
RE_REF_MARKER = re.compile(r"^参考文献\s*$")

# ─────────────────────────────────────────────────────────────────────
# 阈值(用户拍板,2026-05-03)
# ─────────────────────────────────────────────────────────────────────
PARENT_SPLIT_THRESHOLD = 5000       # user 拍板 2026-05-05:从 4000 调高(三本书统一)
                                    # 父块 > 5000 字 (~3597 token) → Pass 1+2: 【】+(一)(二)
                                    # 实测 Qwen tokenizer 1 token ≈ 1.39 字符
PARENT_PASS3_THRESHOLD = 6000       # 父块 > 6000 字 (~4317 token) → Pass 3: 加 1./2. 细化
                                    # 5000-5999 接受不再切(避免把医学小节切碎)
PARENT_MERGE_TINY_THRESHOLD = 500   # 父块 < 500 字 → 严格层级合并
                                    # 用户拍板 2026-05-03:吸收方 level ≤ 被吸收方
                                    # 同级兄弟(BRACE/PAREN/NUM)、上级吸子主题、节首段允许
                                    # 禁止下级跨上级(1.→(二)、(一)→【】 等)
CHILD_SPLIT_THRESHOLD = 1200        # 父块 ≤ 1200 字(~864 token)→ 不切子块,1 child = parent
                                    # > 1200 字才走父子索引(按大小累积切 child)
                                    # 用户拍板 2026-05-03:小父块自身就够 embedding,父子索引
                                    # 仅对真正大的父块有意义
CHILD_TARGET_SIZE = 600             # 大父块切子块的目标 size(~432 token)
                                    # 用户拍板 2026-05-03:子块切分不再用标题 pattern,
                                    # 完全按 mineru block 累积到 ~600 字,
                                    # 选"加 vs 不加下一块"中离 600 更近的方案
CHILD_MIN_SIZE = 200                # 子块强制最小:当前累积 < 200 时无视"离 600 多近"的判断,
                                    # 必须 force-add 下个 block。避免"小标题段 + 紧邻大 block"
                                    # 算法选择"留小段独立"产生 43 字孤儿子块。
                                    # 代价:某些子块会 200~1500 字,而非严格围绕 600。

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
    """书末截断:第一个出现 BODY_END_MARKER 的 title block 位置(返回索引);
    若无,返回 len(flat)。"""
    for i, b in enumerate(flat):
        if b["type"] == "title" and RE_BODY_END.match(b["text"].strip()):
            return i
    return len(flat)


# ─────────────────────────────────────────────────────────────────────
# 节(L1-L3)边界:复用 Step 2 匹配 + REAL_START 选取
# ─────────────────────────────────────────────────────────────────────


def _real_start_positions(flat: list[dict], result: dict) -> dict[int, tuple[int, str]]:
    """计算 REAL_START_pos → (level, dict_title)。

    沿用 audit 逻辑:
      同节多次匹配时,选 strong (PART_REBUILT/CHAP_MERGED 或 AS_IS gap≥50) 的
      最后一个;无 strong 则选最后一个。
    """
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
            if r["action"] in ("PART_REBUILT", "CHAP_MERGED")
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
# 子标题边界识别(用于父块三遍切)
# ─────────────────────────────────────────────────────────────────────


def _is_brace_subheading(text: str, *, block_type: str | None = None) -> bool:
    """是否仅命中【】(父块二次切 Pass 1)"""
    if block_type == "list":
        return False
    s = text.strip()
    if len(s) < 4:
        return False
    if RE_TABLE_TITLE.match(s) or RE_FIG_TITLE.match(s):
        return False
    return bool(RE_BRACE.match(s))


def _is_paren_subheading(text: str, *, block_type: str | None = None) -> bool:
    """是否仅命中 (一)(二) 中文括号编号(父块二次切 Pass 2)"""
    if block_type == "list":
        return False
    s = text.strip()
    if len(s) < 4:
        return False
    if RE_TABLE_TITLE.match(s) or RE_FIG_TITLE.match(s):
        return False
    return bool(RE_PAREN_CN.match(s))


def _is_num_dot_subheading(text: str, *, block_type: str | None = None) -> bool:
    """是否仅命中 1./2. 阿拉伯数字编号(父块二次切 Pass 3)。

    list 类 block 跳过(避免列表首项 '1. xxx\\n2. xxx' 被误识别为子节边界)。
    """
    if block_type == "list":
        return False
    s = text.strip()
    if len(s) < 4:
        return False
    if RE_TABLE_TITLE.match(s) or RE_FIG_TITLE.match(s):
        return False
    return bool(RE_NUM_DOT.match(s))


def _find_ref_idx(section_blocks: list[dict]) -> int | None:
    """找节内"参考文献"标题位置(若有)。"""
    for idx in range(1, len(section_blocks)):
        if RE_REF_MARKER.match(section_blocks[idx]["text"].strip()):
            return idx
    return None


# ─────────────────────────────────────────────────────────────────────
# 大父块二次切(Step 5)
# ─────────────────────────────────────────────────────────────────────


# 父块边界级别(数字越小,主题切换越大,越不允许被合并跨越)
LEVEL_SECTION = 0   # 节首,永不可删
LEVEL_BRACE = 1     # 【】,主题切换,不允许合并跨越
LEVEL_PAREN = 2     # (一)(二),同【】下兄弟,可合并
LEVEL_NUM = 3       # 1./2.,同(一)下兄弟,可合并


def _split_big_parent(section_blocks: list[dict], threshold: int,
                       ref_idx: int | None,
                       pass3_threshold: int) -> list[tuple[int, int]]:
    """父块过大时,逐级细化切分,返回带级别的边界。

    Pass 1: 段 > threshold 用【】切,边界级别 1
    Pass 2: 段仍 > threshold 用 (一)(二) 切,边界级别 2
    Pass 3: 段仍 > pass3_threshold 用 1./2. 切,边界级别 3

    级别用于后续 _merge_tiny_parents 决定哪些边界可被合并跨越:
      level 0/1 永不可跨(节边界 / 主题边界)
      level 2/3 同上级父级范围内可跨(兄弟合并)

    返回 [(pos, level), ...] 升序。第一个永远是 (0, LEVEL_SECTION)。
    """
    section_len = sum(blk["len"] for blk in section_blocks)
    if section_len <= threshold:
        # 整节作 1 父块,边界用 LEVEL_BRACE 视为不可跨(它对应"节首"也是主题)
        return [(0, LEVEL_BRACE)]

    upper = ref_idx if ref_idx is not None else len(section_blocks)

    def _refine(boundaries: list[tuple[int, int]], predicate,
                  new_level: int, thr: int) -> list[tuple[int, int]]:
        """对 boundaries 里 size > thr 的段加 predicate 命中位置(标 new_level)。"""
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
        # 去重 + 升序;若同位置出现多个级别,取最小级别(更高优先级)
        seen: dict[int, int] = {}
        for pos, lvl in out:
            if pos not in seen or lvl < seen[pos]:
                seen[pos] = lvl
        return sorted(seen.items())

    pass1 = _refine([(0, LEVEL_SECTION)], _is_brace_subheading, LEVEL_BRACE, threshold)
    pass2 = _refine(pass1, _is_paren_subheading, LEVEL_PAREN, threshold)
    pass3 = _refine(pass2, _is_num_dot_subheading, LEVEL_NUM, pass3_threshold)
    return pass3


def _split_parent_to_children_by_size(parent_blocks: list[dict],
                                        target: int,
                                        min_size: int = 0) -> list[list[dict]]:
    """大父块按 block 累积切子块,目标 ~target 字符/子块。

    决策(方案 c):每加一个 block 前,看"加 vs 不加"哪个 acc_len 更接近 target,
    选更近的。不加 → 关闭当前 child 开新 child。

    强制最小约束(min_size > 0 时):当前累积 < min_size 时无视"离 target 多近",
    必须 force-add 下个 block。避免"小段 + 紧邻大 block"产生孤儿子块。

    边界:
      - 单 block 即使比 target 大也独立成 child(block 是不可分的最小语义单元)
      - 末尾 child 若 < target/2 (太小),backward 并入上一 child
    """
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
        # 强制最小:当前段还没到 min_size,无条件加(避免孤儿小段)
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
        # 末尾 child 太小则 backward
        if children and current_len < target // 2:
            children[-1].extend(current)
        else:
            children.append(current)
    return children


def _merge_tiny_parents(boundaries: list[tuple[int, int]],
                          section_blocks: list[dict],
                          min_size: int) -> list[tuple[int, int]]:
    """合并 < min_size 的小父块,严格按层级关系判断方向:

    核心原则(用户拍板 2026-05-03):
      合并相当于"吸收方"扩展吃掉"被吸收方"。吸收方的级别必须 ≤ 被吸收方,
      否则就是下级跨上级边界(违反主题层级)。

    Forward(cur 吸收 next):cur_level ≤ next_level
      允许:同级兄弟(BRACE/PAREN/NUM 各级)、上级吸子主题、节首段(SECTION=0 自动满足)
      禁止:1.→(二) 跨(一)、(一)→【B】 跨【】、1.→【B】

    Backward(prev 吸收 cur):prev_level ≤ cur_level
      允许:同级兄弟、上级吸子主题
      禁止:小段被深级 prev 吸收(等价于 prev 跨上级边界)

    forward 优先(小段并入下一段,head 保留 cur 的标题信息),否则 backward。
    section 边界绝对不可破(在 _split_big_parent 外部已硬切,本函数永远不接触)。
    """
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
            # 尝试 forward: 删 bs[i+1] (next 的起始边界)
            if i + 1 < len(bs):
                next_level = bs[i + 1][1]
                if cur_level <= next_level:
                    bs = bs[:i + 1] + bs[i + 2:]
                    merged = True
                    break
            # 尝试 backward: 删 bs[i] (cur 自己的起始边界)
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
    """完整切分流程,返回 {parents, children, stats}。"""
    result = build_toc_dict()
    data = json.loads(Path(CONTENT_LIST_V2).read_text())
    body_start = max(result["toc_pages"]) + 1
    flat_full = _flatten_blocks(data, body_start)

    # 书末截断
    body_end = _find_body_end(flat_full)
    flat = flat_full[:body_end]
    truncated_blocks = len(flat_full) - body_end
    truncated_chars = sum(b["len"] for b in flat_full[body_end:])

    real_start_pos = _real_start_positions(flat, result)
    section_splits = sorted(real_start_pos.keys())

    # first section 起点之前的前置内容(BLACKLIST 命中行 + 章节大纲段等)— 后期 SOP 加的字段
    preface_blocks = flat[: section_splits[0]] if section_splits else []
    preface_dropped_blocks = len(preface_blocks)
    preface_dropped_chars = sum(b["len"] for b in preface_blocks)

    parents: list[dict] = []
    children: list[dict] = []
    ref_dropped_blocks = 0
    ref_dropped_chars = 0

    for i in range(len(section_splits)):
        a = section_splits[i]
        b_end = section_splits[i + 1] if i + 1 < len(section_splits) else len(flat)
        section_blocks_full = flat[a:b_end]
        level, sec_title = real_start_pos[a]

        # 参考文献丢弃:扫到"参考文献"标题就截断,该位置及之后全部丢弃
        # (包括 ref 条目 + 扩展资源占位列表)
        ref_idx = _find_ref_idx(section_blocks_full)
        if ref_idx is not None:
            ref_dropped_blocks += len(section_blocks_full) - ref_idx
            ref_dropped_chars += sum(b["len"] for b in section_blocks_full[ref_idx:])
            section_blocks = section_blocks_full[:ref_idx]
        else:
            section_blocks = section_blocks_full

        if not section_blocks:
            continue

        # 大父块二次切边界(带级别);ref_idx 已截断,无需再传
        boundaries = _split_big_parent(section_blocks,
                                         PARENT_SPLIT_THRESHOLD, None,
                                         PARENT_PASS3_THRESHOLD)
        # 按级别约束合并小父块(不跨节/不跨【】)
        boundaries = _merge_tiny_parents(boundaries, section_blocks,
                                          PARENT_MERGE_TINY_THRESHOLD)
        parent_starts = [b[0] for b in boundaries]

        # 把 sub_idx 按 parent_starts 分组(每父块管自己范围内的子块)
        parent_starts_with_end = parent_starts + [len(section_blocks)]
        for pi in range(len(parent_starts)):
            pa = parent_starts_with_end[pi]
            pb = parent_starts_with_end[pi + 1]

            # 父块文本 / head
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

            # 父块 ≤ CHILD_SPLIT_THRESHOLD 不切子块,1 child = parent;
            # > CHILD_SPLIT_THRESHOLD 按 block 累积到 ~CHILD_TARGET_SIZE 切多 child
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
        "preface_dropped_blocks": preface_dropped_blocks,
        "preface_dropped_chars": preface_dropped_chars,
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


def main() -> None:
    res = chunk_book()
    parents, children, stats = res["parents"], res["children"], res["stats"]

    print("=" * 70)
    print("切分流程结果")
    print("=" * 70)
    print(f"书末截断:    丢弃 {stats['truncated_blocks']} blocks "
          f"/ {stats['truncated_chars']} 字符")
    print(f"前言丢弃:     丢弃 {stats['preface_dropped_blocks']} blocks "
          f"/ {stats['preface_dropped_chars']} 字符")
    print(f"参考文献丢弃: 丢弃 {stats['ref_dropped_blocks']} blocks "
          f"/ {stats['ref_dropped_chars']} 字符 (参考文献条目+扩展资源占位)")
    print(f"父块数(节):  {stats['n_sections']}")
    print(f"父块数(切): {stats['n_parents']}  "
          f"(被【】二次切的节: {sum(1 for p in parents if p['is_split_from_section'])} 个新增父块)")
    print(f"子块数:       {stats['n_children']}")

    parent_lens = [p["len"] for p in parents]
    child_lens = [c["len"] for c in children]
    # 分桶边界对齐阈值
    parent_buckets = [
        (0, 499), (500, 1999),
        (2000, PARENT_SPLIT_THRESHOLD - 1),       # 阈值以下
        (PARENT_SPLIT_THRESHOLD, 4999),           # 略超阈值
        (5000, 9999), (10000, 19999), (20000, 79999),
    ]
    child_buckets = [
        (0, CHILD_MIN_SIZE - 1),                  # 强制最小以下(理论上只剩导航页 case)
        (CHILD_MIN_SIZE, 499),
        (500, 999), (1000, 1999),
        (2000, 4999), (5000, 9999), (10000, 10**9),
    ]
    _print_distribution("父块", parent_lens, parent_buckets)
    _print_distribution("子块", child_lens, child_buckets)

    # ─── 还过大的父块 ───
    big_parents = sorted([p for p in parents if p["len"] > PARENT_SPLIT_THRESHOLD],
                         key=lambda p: -p["len"])
    print(f"\n仍 > {PARENT_SPLIT_THRESHOLD} 字父块: {len(big_parents)} 个")
    print("(三遍切已经走过【】+(一)+1.,这些都是无更细子结构的医学整段,接受)\n")
    for p in big_parents[:15]:
        print(f"  pg={p['pg_start']:>4}  size={p['len']:>6}  {p['head'][:50]}  "
              f"(节={p['section_title'][:25]})")

    # ─── 极端 case ───
    print(f"\n[最大 5 父块]")
    for p in sorted(parents, key=lambda x: -x["len"])[:5]:
        print(f"  size={p['len']:>6}  {p['title']}")

    print(f"\n[最小 5 父块]")
    for p in sorted(parents, key=lambda x: x["len"])[:5]:
        print(f"  size={p['len']:>6}  {p['title']}")

    print(f"\n[最大 5 子块]")
    for c in sorted(children, key=lambda x: -x["len"])[:5]:
        ref = " [REF]" if c["is_reference"] else ""
        print(f"  size={c['len']:>6}  节={c['section_title'][:25]}  head={c['head'][:40]}{ref}")

    # ─── 字符守恒检查(后期 SOP 标配)───
    flat_full = _flatten_blocks(json.loads(Path(CONTENT_LIST_V2).read_text()),
                                max(build_toc_dict()["toc_pages"]) + 1)
    body_end = _find_body_end(flat_full)
    flat_kept_chars = sum(b["len"] for b in flat_full[:body_end])
    p_sum = sum(p["len"] for p in parents)
    c_sum = sum(c["len"] for c in children)
    expected = flat_kept_chars - stats["ref_dropped_chars"] - stats["preface_dropped_chars"]
    print(f"\n=== 字符守恒检查 ===")
    print(f"  body kept (after BODY_END trim): {flat_kept_chars}")
    print(f"  preface dropped:                 {stats['preface_dropped_chars']}")
    print(f"  ref dropped:                     {stats['ref_dropped_chars']}")
    print(f"  expected (body - preface - ref): {expected}")
    print(f"  parents sum:                     {p_sum}  mismatch={p_sum - expected}")
    print(f"  children sum:                    {c_sum}  mismatch={c_sum - expected}")

    # ─── Cushing 节详情 ───
    cushing_parents = [p for p in parents if p["section_title"] == "第4节 Cushing综合征"]
    print(f"\n=== Cushing 综合征:{len(cushing_parents)} 父块 ===")
    for p in cushing_parents:
        kids = [c for c in children if c["parent_idx"] == p["parent_idx"]]
        print(f"\n父块[{p['parent_idx']}] size={p['len']}  {p['head']}")
        for c in kids:
            ref = " [REF]" if c["is_reference"] else ""
            print(f"    └ 子块 size={c['len']:>5} blocks={c['blocks']:>2}  {c['head'][:50]}{ref}")


if __name__ == "__main__":
    main()
