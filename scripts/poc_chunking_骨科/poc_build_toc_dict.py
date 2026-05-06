"""
POC: 目录权威清单提取(C2 chunking 第一步,DEV_SPEC §3.1.2)
====================================================================
**本规则只针对《骨科》(规划教材)实测有效**;通用方法论见
[`scripts/METHODOLOGY.md`](../METHODOLOGY.md),本书特定笔记见
[`BOOK_NOTES.md`](BOOK_NOTES.md)。

本书规则:
  L1 = 第N篇(6 个)
  L2 = 第N章(51 个)
  L3 = 第N节(248 个)
  L4 = 一、(TOC 也列了子标题,~982 个)
  字典深度到 L4 — 跟内分泌/神经内科/消化字典含 一、 风格类似

正文起点:pg 44(第一篇 骨科学基础 alone)
末尾截断:pg 1444 paragraph "索引"(注意:本书末尾混乱,见 BOOK_NOTES §3.x)

跟胸心外科 build 差异:**保留双向延伸**(本书单 seed pg 11,需要延伸覆盖 33 页 TOC)
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CONTENT_LIST_V2 = (
    "/data/medical-resources/mineru-output/"
    "骨科/hybrid_auto/"
    "骨科_content_list_v2.json"
)

# 5 类 anchor — 字典到 L4 一、(TOC 列出子标题);L3 含 `附:` 节同级
PATTERNS: list[tuple[int, re.Pattern]] = [
    (1, re.compile(r"^第\s*\S{1,4}\s*篇(?=\s|$)")),
    (2, re.compile(r"^第\s*\S{1,4}\s*章(?=\s|$)")),
    (3, re.compile(r"^第\s*\S{1,4}\s*节(?=\s|$)")),
    (3, re.compile(r"^附\s*[:：]\s*\S")),  # `附:xxx` 作 L3 节同级(跟消化同)
    (4, re.compile(r"^[一二三四五六七八九十百零〇]+、")),
]

SPLIT_ANCHOR = re.compile(
    r"(?=第\s*\S{1,4}\s*[篇章节]\s)"
    r"|(?<=\d)(?=第\s*\S{1,4}\s*[篇章节])"
)

BLACKLIST = {
    "上册", "下册", "目录",
    "骨科", "骨科学",
    "索引", "中英文名词对照索引",
    "公众号登录 >>", "网站登录 >>", "进入中华临床影像库首页",
    "注册或登录", "临床影像库", "登录中华临床影像库步骤",
}

PATCH_REPLACE_TITLE: dict[str, str] = {}
PATCH_LINE_PREPROCESS: list[tuple[str, str]] = []
PATCH_INJECT_CANDIDATES: list[tuple[int, int, str]] = []

# 硬编码 L4 — TOC 行无 "一、" 前缀(书本省略 1 个子项的序号),但正文也是无 "一、" 的 type=title,
# 直接给这些 strict_key 强行打 L4 标签纳入字典,正文 type=title 通过 strict_key 自动匹配
PATCH_FORCE_LEVEL: dict[str, int] = {
    "非骨化性纤维瘤": 4,
    "骨内脂肪瘤": 4,
}

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
    """本书单 seed pg 11(`title='目录'`),pg 12-43 后续 TOC 无 page_header,需双向延伸。
    复用原 SOP(神经外科学/神经内科学风格)。
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

    i = max(extended) + 1
    while i < len(data):
        if _page_anchor_count(data[i]) >= TOC_EXTEND_THRESHOLD:
            extended.add(i)
            i += 1
        else:
            break

    i = min(extended) - 1
    while i >= 0:
        if _page_anchor_count(data[i]) >= TOC_EXTEND_THRESHOLD:
            extended.add(i)
            i -= 1
        else:
            break

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
    # 硬编码 fallback:strict_key 命中 PATCH_FORCE_LEVEL 时强行打标签
    if PATCH_FORCE_LEVEL:
        norm = _normalize(line)
        key = re.sub(r"\s+", "", norm).translate(_PUNCT_NORMALIZE)
        if key in PATCH_FORCE_LEVEL:
            return PATCH_FORCE_LEVEL[key], norm
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

    print(f"=== TOC pages identified: {toc_pages[:5]}..{toc_pages[-3:]} (total {len(toc_pages)} pages) ===\n")
    print(f"=== Extracted {len(entries)} TOC entries ===\n")

    print("=== Counts by level ===")
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
