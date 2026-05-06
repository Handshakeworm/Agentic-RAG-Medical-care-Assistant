"""
POC: 目录权威清单提取(C2 chunking 第一步,DEV_SPEC §3.1.2)
====================================================================
**本规则只针对《胸心外科》(规划教材)实测有效**;通用方法论见
[`scripts/METHODOLOGY.md`](../METHODOLOGY.md),本书特定笔记见
[`BOOK_NOTES.md`](BOOK_NOTES.md)。

本书规则:
  L1 = 第N篇(2 个)
  L2 = 第N章(~15 个)
  L3 = 第N节(目录最细颗粒度,~74 个)
  字典深度到 L3 节 — 跟心血管类似的浅字典(无 一、 在字典)

正文起点:pg 21(第一章 胸外伤)
末尾截断:pg 513 起 `中英文名词对照索引` + pg 524 广告页
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CONTENT_LIST_V2 = (
    "/data/medical-resources/mineru-output/"
    "胸心外科/hybrid_auto/"
    "胸心外科_content_list_v2.json"
)

# 3 类 anchor — 本书字典到 L3 节(跟心血管同)
PATTERNS: list[tuple[int, re.Pattern]] = [
    (1, re.compile(r"^第\s*\S{1,4}\s*篇(?=\s|$)")),
    (2, re.compile(r"^第\s*\S{1,4}\s*章(?=\s|$)")),
    (3, re.compile(r"^第\s*\S{1,4}\s*节(?=\s|$)")),
]

SPLIT_ANCHOR = re.compile(
    r"(?=第\s*\S{1,4}\s*[篇章节]\s)"
    r"|(?<=\d)(?=第\s*\S{1,4}\s*[篇章节])"
)

BLACKLIST = {
    "上册", "下册", "目录",
    "胸心外科", "胸心外科学",
    "索引", "中英文名词对照索引",
    "公众号登录 >>", "网站登录 >>", "进入中华临床影像库首页",
    "注册或登录", "临床影像库", "登录中华临床影像库步骤",
}

PATCH_REPLACE_TITLE: dict[str, str] = {}
PATCH_LINE_PREPROCESS: list[tuple[str, str]] = []
PATCH_INJECT_CANDIDATES: list[tuple[int, int, str]] = []

# 兼容单个 `…` 尾页码 — `…67`(无空格)mineru 单字符识别(本书 1 处第四章第二节)
TAIL_PAGE_RE = re.compile(r"(?:[…\.]+|\s|/)\s*[（(]?\s*\d+\s*[）)]?\s*$")
TAIL_ELLIPSIS_RE = re.compile(r"\s*…+\s*$")
SECTION_NUM_RE = re.compile(r"第\s*(\S{1,4})\s*([篇章节])")


def _text_of(items: list) -> str:
    return "".join(
        s.get("content", "") for s in items
        if isinstance(s, dict) and s.get("type") == "text"
    )


def _block_lines(b: dict) -> list[str]:
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
    """本书 pg 17 (title='目录') + pg 18 (page_header='目录') 双 seed 已覆盖完整 TOC。
    只填充 seeds 间间隙,不向外延伸 — 防止把 pg 19 篇内章节简表(无尾页码)误纳入。
    """
    seeds = [i for i, p in enumerate(data) if _is_toc_page(p)]
    if not seeds:
        return []
    extended = set(seeds)

    TOC_EXTEND_THRESHOLD = 5

    for i in range(min(seeds), max(seeds) + 1):
        if i in extended:
            continue
        if _page_anchor_count(data[i]) >= TOC_EXTEND_THRESHOLD:
            extended.add(i)

    return sorted(extended)


def _normalize(s: str) -> str:
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


_PUNCT_NORMALIZE = str.maketrans({
    "？": "?", "！": "!", "，": ",", "：": ":", "；": ";",
    "（": "(", "）": ")", "．": ".",
})


def strict_key(s: str) -> str:
    return re.sub(r"\s+", "", _normalize(s)).translate(_PUNCT_NORMALIZE)


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
    data = json.loads(Path(CONTENT_LIST_V2).read_text())
    toc_pages = _detect_toc_pages(data)

    raw_lines: list[tuple[int, str]] = []
    for pg in toc_pages:
        for b in data[pg]:
            for line in _block_lines(b):
                if line:
                    flat = re.sub(r"\s+", " ", line.replace("\n", " ")).strip()
                    if flat:
                        raw_lines.append((pg, flat))

    # 同页相邻行合并:救 mineru 把"第N节 xxxx\n yyy 页码"切成两行的情况
    merged_lines: list[tuple[int, str]] = []
    i = 0
    while i < len(raw_lines):
        pg, line = raw_lines[i]
        while (i + 1 < len(raw_lines)
               and raw_lines[i + 1][0] == pg
               and not TAIL_PAGE_RE.search(line)
               and _classify(raw_lines[i + 1][1].strip()) is None):
            line = (line + " " + raw_lines[i + 1][1]).strip()
            i += 1
        merged_lines.append((pg, line))
        i += 1

    expanded: list[tuple[int, str]] = []
    for pg, line in merged_lines:
        for old, new in PATCH_LINE_PREPROCESS:
            if old in line:
                line = line.replace(old, new)
        for piece in _split_glued(line):
            expanded.append((pg, piece))

    stack = ["", "", "", ""]
    entries: list[tuple[int, str, str, int]] = []
    skipped_blacklist: list[str] = []
    skipped_unmatched: list[tuple[int, str]] = []

    for pg, line in expanded:
        s = line.strip()
        norm_s = _normalize(s)
        if s in BLACKLIST or norm_s in BLACKLIST:
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

    if PATCH_REPLACE_TITLE:
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
    for pg, s in skipped_unmatched[:50]:
        print(f"  [pg={pg}] {s[:100]}")


if __name__ == "__main__":
    main()
