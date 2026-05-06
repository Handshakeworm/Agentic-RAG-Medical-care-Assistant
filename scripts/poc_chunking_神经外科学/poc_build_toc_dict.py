"""
POC: 目录权威清单提取(C2 chunking 第一步,DEV_SPEC §3.1.2)
====================================================================
**本规则只针对《神经外科学》(杨新宇主编,2025 科学出版社)实测有效**;
通用方法论见 [`scripts/METHODOLOGY.md`](../METHODOLOGY.md),本书特定笔记见
[`BOOK_NOTES.md`](BOOK_NOTES.md)。

本书规则:
  L1 = 第N篇(7 个,第一~第七)
  L2 = 第N章(33 个,1-33 跨篇连续编号)
  L3 = 第N节(153 个)
  无 L4(目录中无 一、/[附] 独立条目)

跟诊断学(同 L1-L3 三层 anchor)的差异:
  - **TOC 多页延伸方向相反**:本书 mineru 在 TOC 末页 pg 7 标 page_header="目录",
    pg 5-6 没标 → _detect_toc_pages 需要**双向延伸**(往前找)
  - **新增 type=title="目录" 也作为 seed**(本书 pg 5 没 page_header 但 blk 0 是 title="目录")
  - **篇 anchor 干净**:7 篇全是单 title block 完整匹配,**不需要 CHAP_MERGED**
  - **章 TOC 行末用 `……`(中文连续省略号)+ 页码**:已被 TAIL_PAGE_RE `[…\.]{2,}` 覆盖
  - **节内子标题主力 (一)**(50.9%)>  一、(35.5%),完全无【】
  - **SPLIT_ANCHOR 加 lookbehind**:救 mineru TOC 黏行
    `第三节 X 219第四节 Y 221`(两条目用页码数字分隔无空格)
  - **无 BODY_END marker / 无 ref marker**(末页 pg 319 直接是正文末)
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CONTENT_LIST_V2 = (
    "/data/medical-resources/mineru-output/"
    "神经外科学/hybrid_auto/"
    "神经外科学_content_list_v2.json"
)

# 3 类 anchor(顺序敏感:先匹配先生效)
PATTERNS: list[tuple[int, re.Pattern]] = [
    (1, re.compile(r"^第\s*\S{1,4}\s*篇(?=\s|$)")),
    (2, re.compile(r"^第\s*\S{1,4}\s*章(?=\s|$)")),
    (3, re.compile(r"^第\s*\S{1,4}\s*节(?=\s|$)")),
]

# 跨条目粘连二次拆分:本书 mineru 把"第三节 X 219第四节 Y 221" 黏成一段
#   旧规则:`(?=第N节\s)` 要求后跟空白,这里"219第四节" 中间无空白匹配不上
#   新规则:用 lookbehind `(?<=\d)` 允许"页码末尾紧跟新条目"作为切点
SPLIT_ANCHOR = re.compile(
    r"(?=第\s*\S{1,4}\s*[篇章节]\s)"            # 旧:后跟空白(常见)
    r"|(?<=\d)(?=第\s*\S{1,4}\s*[篇章节])"      # 新:前面是数字(页码黏连,神经外科学发现)
)

# 黑名单(strip 后完全匹配)
BLACKLIST = {
    "上册", "下册", "全书概览", "目录", "绪论",
    "神经外科学",          # 书名,在 TOC pg 5 blk 0 是 title="目录" 之后,blk 1 是 title="第一篇..."
                          # 但 page_header 里 pg 6 出现书名"神经外科学",防止误进
}

# ─────────────────────────────────────────────────────────────────────
# 本书 mineru TOC 提取的硬错(对照 PDF + 正文人审,2026-05-05)
# 算法救不了的(章名跨多 block + 正文完整),走硬编码补丁
# ─────────────────────────────────────────────────────────────────────

# 修改 entries:把残缺章名补全(strict_key 旧 → 完整 title)
# **关键**:正文里这两章 mineru 是完整单 title block,所以字典必须用完整章名才能 AS_IS 命中
PATCH_REPLACE_TITLE: dict[str, str] = {
    # TOC pg 7 blk 3+4+5 章名拆 3 段,正文 pg 233 是完整单 title
    "第二十四章功能性神经外科与": "第二十四章 功能性神经外科与其他神经、精神疾病",
    # TOC pg 7 blk 33+34 章名拆 2 段,正文 pg 281 是完整单 title
    "第三十章小儿神经创伤与重症": "第三十章 小儿神经创伤与重症监护",
}

# 剥行尾"页码"尾巴(`第N章 X X……N` 中文连续省略号+页码)
TAIL_PAGE_RE = re.compile(r"(?:[…\.]{2,}|\s|/)\s*\(?\d+\)?\s*$")

# 剥行尾"裸省略号"
TAIL_ELLIPSIS_RE = re.compile(r"\s*…+\s*$")

# 清理章节号内部空格
SECTION_NUM_RE = re.compile(r"第\s*(\S{1,4})\s*([篇章节])")


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
    """page_header 含'目录'字样 OR 任一 type=title block 文本是'目录' → 是 TOC 页 seed。

    本书 pg 5 blk 0 是 title='目录'(无 page_header),pg 7 page_header='目录' → 双 seed。
    """
    for b in page_blocks:
        if b.get("type") == "page_header":
            txt = _text_of(b.get("content", {}).get("page_header_content", []))
            if "目录" in txt:
                return True
        elif b.get("type") == "title":
            txt = _text_of(b.get("content", {}).get("title_content", [])).strip()
            if txt == "目录":
                return True
    return False


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
    """目录页扫描:从 seed 起点(可能是首页/末页)**双向延伸**(前 + 后)。

    停止条件:邻近页 anchor 块 < 2 即停。

    本书:seed = [pg 5 (title='目录'), pg 7 (page_header='目录')]
    往前从 pg 5 → pg 4 (前言,无 anchor) 停
    往后从 pg 7 → pg 8 (正文起点,只有 第一篇/第一章 2 个 anchor 临界,可能加入,需看是否真目录)
    """
    seeds = [i for i, p in enumerate(data) if _is_toc_page(p)]
    if not seeds:
        return []
    extended = set(seeds)

    # 阈值 5 而非 2:目录页每页通常 10+ anchor 条目,正文页只有零星
    # (本书 pg 8 正文起点有第一篇/第一章/第一节 3 个 anchor,阈值 2 会误吃)
    TOC_EXTEND_THRESHOLD = 5

    # 1) 填充 seeds 之间的空隙(本书 pg 5 + pg 7 是 seed 但 pg 6 不是,需要补)
    #    假设目录页连续,只要 [min, max] 之间页面 anchor 命中 ≥ 阈值就并入
    for i in range(min(seeds), max(seeds) + 1):
        if i in extended:
            continue
        if _page_anchor_count(data[i]) >= TOC_EXTEND_THRESHOLD:
            extended.add(i)

    # 2) 往后延(从已收范围最大值之外)
    i = max(extended) + 1
    while i < len(data):
        if _page_anchor_count(data[i]) >= TOC_EXTEND_THRESHOLD:
            extended.add(i)
            i += 1
        else:
            break

    # 3) 往前延(从已收范围最小值之外,救 mineru 末页才标的情况)
    i = min(extended) - 1
    while i >= 0:
        if _page_anchor_count(data[i]) >= TOC_EXTEND_THRESHOLD:
            extended.add(i)
            i -= 1
        else:
            break

    return sorted(extended)


def _normalize(s: str) -> str:
    """归一化:删 PDF 换行残留 + 折叠空白 + 章节号去内部空格 + 反复剥页码尾 + 剥省略号。"""
    s = s.replace("\n", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = SECTION_NUM_RE.sub(r"第\1\2", s)
    while True:
        new = TAIL_PAGE_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    s = TAIL_ELLIPSIS_RE.sub("", s).strip()
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
    """构建权威字典(可被其他 POC 脚本 import 复用)。"""
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
        path = " / ".join(x for x in stack[:level] if x)
        entries.append((level, title, path, pg))

    # 应用本书硬编码补丁(replace 残缺章名为完整章名)
    # 同时同步修 path:下游 L3 节的 path 含旧章名也要替换
    if PATCH_REPLACE_TITLE:
        # 建 旧 title 字符串 → 新 title 字符串 的反查表(用于 path replace)
        old_to_new_title: dict[str, str] = {}
        for lvl, title, path, pg in entries:
            if strict_key(title) in PATCH_REPLACE_TITLE:
                old_to_new_title[title] = PATCH_REPLACE_TITLE[strict_key(title)]

        new_entries = []
        for lvl, title, path, pg in entries:
            new_title = old_to_new_title.get(title, title)
            new_path = path
            for old, new in old_to_new_title.items():
                if old in new_path:
                    new_path = new_path.replace(old, new)
            new_entries.append((lvl, new_title, new_path, pg))
        entries = new_entries

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
