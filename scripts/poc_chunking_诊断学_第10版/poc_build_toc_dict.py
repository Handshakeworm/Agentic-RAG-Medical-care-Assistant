"""
POC: 目录权威清单提取(C2 chunking 第一步,DEV_SPEC §3.1.2)
====================================================================
**本规则只针对《诊断学 第10版》实测有效**;通用方法论见
[`scripts/METHODOLOGY.md`](../METHODOLOGY.md),本书特定笔记见
[`BOOK_NOTES.md`](BOOK_NOTES.md)。

本书规则:
  L1 = 第N篇
  L2 = 第N章 / 第N节(mixed depth — 第一篇下直接列节,无章)
  L3 = (本书暂未观察到 L3 anchor — 节内子结构走【】+ 1./2.,见 BOOK_NOTES §3)

跟内分泌的差异(详见 BOOK_NOTES.md §1-§2):
  - **mixed depth 结构**:第一篇 → 节;第二~八篇 → 章。stack[: level-1] 隐式处理
  - **节 anchor 风格**:`第N节  |  发 热`(`|` 分隔 + 字间空格)。normalize 加 `|` 折叠规则
  - 暂无"扩展资源 N"和"N.N"层级
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CONTENT_LIST_V2 = (
    "/data/medical-resources/mineru-output/"
    "诊断学 第10版/hybrid_auto/"
    "诊断学 第10版_content_list_v2.json"
)

# 3 类 anchor(顺序敏感:先匹配先生效)
# 节用 L3 vs L2:本书"第一篇"下节直接挂篇,但第二~八篇下章挂篇。
# 用统一 L3 标节,stack 在 mixed depth 下空 L2 槽,parent_path 用 filter(empty) 自然处理。
PATTERNS: list[tuple[int, re.Pattern]] = [
    (1, re.compile(r"^第\s*\S{1,4}\s*篇(?=\s|$)")),
    (2, re.compile(r"^第\s*\S{1,4}\s*章(?=\s|$)")),
    (3, re.compile(r"^第\s*\S{1,4}\s*节(?=\s|$)")),
]

# 跨条目粘连二次拆分:在行内任意位置 lookahead 上述 anchor
SPLIT_ANCHOR = re.compile(
    r"(?=第\s*\S{1,4}\s*篇\s|第\s*\S{1,4}\s*章\s|第\s*\S{1,4}\s*节\s)"
)

# 黑名单(strip 后完全匹配)
BLACKLIST = {"上册", "下册", "全书概览", "目录", "绪论"}

# 剥行尾"页码"尾巴
TAIL_PAGE_RE = re.compile(r"(?:[…\.]{2,}|\s|/)\s*\(?\d+\)?\s*$")

# 剥行尾"裸省略号"
TAIL_ELLIPSIS_RE = re.compile(r"\s*…+\s*$")

# 剥行尾"裸句点"(剥页码后剩下的"标题."尾点,本书目录大量出现"第N节 X X." 形式)
TAIL_DOT_RE = re.compile(r"\s*[\.。]\s*$")

# 清理章节号内部空格
SECTION_NUM_RE = re.compile(r"第\s*(\S{1,4})\s*([篇章节])")

# 本书新增:`|` 字面分隔符(见 BOOK_NOTES §2)
# 正文中 `第N节  |  发 热` → 折叠成单空格,strict_key 后变 "第N节发热"
PIPE_SEP_RE = re.compile(r"\s*\|\s*")


def _text_of(items: list) -> str:
    return "".join(
        s.get("content", "") for s in items
        if isinstance(s, dict) and s.get("type") == "text"
    )


def _block_lines(b: dict) -> list[str]:
    """返回 block 包含的所有 TOC 行(PARA/TITLE 各 1 行,list 按 item 多行)。"""
    t = b.get("type")
    c = b.get("content", {})
    if t == "title":
        return [_text_of(c.get("title_content", []))]
    if t == "paragraph":
        return [_text_of(c.get("paragraph_content", []))]
    if t == "list":
        return [
            _text_of(it.get("item_content", []))
            for it in c.get("list_items", [])
            if isinstance(it, dict)
        ]
    return []


def _is_toc_page(page_blocks: list) -> bool:
    """page_header 含'目录'字样 → 是 TOC 页起始。"""
    for b in page_blocks:
        if b.get("type") == "page_header":
            txt = _text_of(b.get("content", {}).get("page_header_content", []))
            if "目录" in txt:
                return True
    return False


# 本书 TOC 跨 pg 15-21,但 mineru 只在 pg 15 标 page_header "目录"。
# pg 16-21 没 marker(版面无 "目录" 字样),需要靠 anchor 匹配启发式自动延伸。
def _page_anchor_count(page_blocks: list) -> int:
    """统计本页有多少 block 命中 PATTERNS(第N篇/章/节)。"""
    count = 0
    for b in page_blocks:
        for line in _block_lines(b):
            s = line.strip()
            if not s:
                continue
            for _, pat in PATTERNS:
                if pat.match(s):
                    count += 1
                    break
    return count


def _detect_toc_pages(data: list) -> list[int]:
    """目录页扫描:从 page_header '目录' 起点,后续页只要 anchor 块 >= 2 就算延续。

    停止条件:连续页 anchor 块 < 2(本书 pg 22 空 + pg 23 起绪论,自然停)。
    """
    seeds = [i for i, p in enumerate(data) if _is_toc_page(p)]
    if not seeds:
        return []
    extended = set(seeds)
    i = max(seeds) + 1
    while i < len(data):
        if _page_anchor_count(data[i]) >= 2:
            extended.add(i)
            i += 1
        else:
            break
    return sorted(extended)


def _normalize(s: str) -> str:
    """归一化:删 PDF 换行残留 + 去 `|` 分隔符 + 折叠空白 + 剥页码尾 + 剥裸省略号。

    保留语义分隔空格(节号 vs 节名之间、中文 vs ASCII 之间),用于显示。
    匹配 lookup 时用 strict_key()(下方)进一步去掉所有空格。
    """
    s = s.replace("\n", "")
    s = PIPE_SEP_RE.sub(" ", s)            # `|` 分隔符 → 单空格
    s = re.sub(r"\s+", " ", s).strip()
    s = SECTION_NUM_RE.sub(r"第\1\2", s)
    while True:
        new = TAIL_PAGE_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    s = TAIL_ELLIPSIS_RE.sub("", s).strip()
    # 反复剥多次:可能是 ".." 这种 (TAIL_PAGE_RE 不动 ≥2点+数字组合,留下来的孤儿)
    while True:
        new = TAIL_DOT_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    return s


def strict_key(s: str) -> str:
    """匹配用 lookup key:在 _normalize 基础上去掉所有空白。"""
    return re.sub(r"\s+", "", _normalize(s))


def _classify(line: str) -> tuple[int, str] | None:
    line = line.strip()
    if not line:
        return None
    for level, pat in PATTERNS:
        if pat.match(line):
            return level, _normalize(line)
    return None


def _split_glued(line: str) -> list[str]:
    return [p.strip() for p in SPLIT_ANCHOR.split(line) if p.strip()]


def _update_stack(stack: list[str], level: int, title: str) -> list[str]:
    new_stack = stack[: level - 1] + [title]
    while len(new_stack) < 4:
        new_stack.append("")
    return new_stack


def build_toc_dict() -> dict:
    """构建权威字典(可被其他 POC 脚本 import 复用)。

    Returns dict with keys:
        entries:           [(level, normalized_title, full_path, page_idx), ...]
        lookup:            {strict_key: [(level, parent_path, dict_title), ...]}
        skipped_blacklist: list[str]
        skipped_unmatched: list[(page_idx, raw_text)]
        toc_pages:         list[int]
    """
    data = json.loads(Path(CONTENT_LIST_V2).read_text())
    toc_pages = _detect_toc_pages(data)

    raw_lines: list[tuple[int, str]] = []
    for pg in toc_pages:
        for b in data[pg]:
            for line in _block_lines(b):
                if line:
                    raw_lines.append((pg, line))

    expanded: list[tuple[int, str]] = []
    for pg, line in raw_lines:
        for piece in _split_glued(line):
            expanded.append((pg, piece))

    stack = ["", "", "", ""]
    entries: list[tuple[int, str, str, int]] = []
    skipped_blacklist: list[str] = []
    skipped_unmatched: list[tuple[int, str]] = []

    for pg, line in expanded:
        s = line.strip()
        if s in BLACKLIST:
            skipped_blacklist.append(s)
            continue
        result = _classify(s)
        if result is None:
            skipped_unmatched.append((pg, s))
            continue
        level, title = result
        if title in BLACKLIST:
            skipped_blacklist.append(title)
            continue
        stack = _update_stack(stack, level, title)
        # filter empty:mixed depth 时 L2 槽空,path 自然成 "第一篇 / 第一节"
        path = " / ".join(x for x in stack[:level] if x)
        entries.append((level, title, path, pg))

    lookup: dict[str, list[tuple[int, str, str]]] = {}
    for level, title, path, _pg in entries:
        if level > 3:
            continue
        parts = path.split(" / ")
        parent_path = " / ".join(parts[:-1])
        lookup.setdefault(strict_key(title), []).append((level, parent_path, title))

    return {
        "entries": entries,
        "lookup": lookup,
        "skipped_blacklist": skipped_blacklist,
        "skipped_unmatched": skipped_unmatched,
        "toc_pages": toc_pages,
    }


def main() -> None:
    result = build_toc_dict()
    entries = result["entries"]
    skipped_blacklist = result["skipped_blacklist"]
    skipped_unmatched = result["skipped_unmatched"]
    toc_pages = result["toc_pages"]

    print(f"=== TOC pages identified: {toc_pages} ===\n")
    print(f"=== Extracted {len(entries)} TOC entries (tree) ===\n")
    for lvl, title, path, pg in entries:
        indent = "    " * (lvl - 1)
        print(f"  L{lvl} pg={pg:3d}  {indent}{title}")

    print("\n=== Counts by level ===")
    cnt = Counter(e[0] for e in entries)
    for lvl in sorted(cnt):
        print(f"  L{lvl}: {cnt[lvl]} entries")

    if skipped_blacklist:
        bcnt = Counter(skipped_blacklist)
        print("\n=== Blacklist hits ===")
        for k, v in bcnt.items():
            print(f"  {k}: {v}")

    print(f"\n=== Unmatched lines: {len(skipped_unmatched)} (samples) ===")
    for pg, s in skipped_unmatched[:30]:
        print(f"  [pg={pg}] {s[:100]}")


if __name__ == "__main__":
    main()
