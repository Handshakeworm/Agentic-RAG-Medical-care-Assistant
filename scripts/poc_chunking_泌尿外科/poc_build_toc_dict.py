"""
POC: 目录权威清单提取(C2 chunking 第一步,DEV_SPEC §3.1.2)
====================================================================
**本规则只针对《现代泌尿外科学》实测有效**;通用方法论见
[`scripts/METHODOLOGY.md`](../METHODOLOGY.md),本书特定笔记见
[`BOOK_NOTES.md`](BOOK_NOTES.md)。

本书规则:
  L1 = 第N篇(14 个)
  L2 = 第N章(93 个,11 本最大)
  L3 = 第N节(~458 个)
  L4 = 一、(TOC 列出子标题)
  字典深度到 L4 — 跟骨科同结构

正文起点:pg 51(`第一篇` alone)
末尾截断:pg 1678 type=title `索引`
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CONTENT_LIST_V2 = (
    "/data/medical-resources/mineru-output/"
    "泌尿外科/hybrid_auto/"
    "泌尿外科_content_list_v2.json"
)

# 4 类 anchor — 字典到 L4 一、(TOC 列出子标题);L3 含 `附:` 节同级
PATTERNS: list[tuple[int, re.Pattern]] = [
    (1, re.compile(r"^第\s*\S{1,4}\s*篇(?=\s|$)")),
    (2, re.compile(r"^第\s*\S{1,4}\s*章(?=\s|$)")),
    (3, re.compile(r"^第\s*\S{1,4}\s*节(?=\s|$)")),
    (3, re.compile(r"^附\s*[:：]\s*\S")),
    (4, re.compile(r"^[一二三四五六七八九十百零〇]+、")),
]

SPLIT_ANCHOR = re.compile(
    r"(?=第\s*\S{1,4}\s*[篇章节]\s)"
    r"|(?<=\d)(?=第\s*\S{1,4}\s*[篇章节])"
    r"|(?<=\d)(?=[一二三四五六七八九十百]+、)"  # 数字尾页码后紧跟"X、"另起一项 — 救 mineru 把多个 list items 拼成一行
)

BLACKLIST = {
    "上册", "下册", "目录", "视频资源目录",
    "现代泌尿外科学", "泌尿外科", "泌尿外科学",
    "索引", "中英文名词对照索引",
    "公众号登录 >>", "网站登录 >>", "进入中华临床影像库首页",
    "注册或登录", "临床影像库", "登录中华临床影像库步骤",
}

PATCH_REPLACE_TITLE: dict[str, str] = {}
# 字典 OCR 错字:TOC pg42 "三、无辜症"(辜 U+8F9C)实际正文 pg1362 "三、无睾症"(睾 U+777E,正确)
PATCH_LINE_PREPROCESS: list[tuple[str, str]] = [
    ("三、无辜症 1312", "三、无睾症 1312"),
]
PATCH_INJECT_CANDIDATES: list[tuple[int, int, str]] = []
PATCH_FORCE_LEVEL: dict[str, int] = {}

TAIL_PAGE_RE = re.compile(r"(?:[…\.]+|\s|/)\s*[（(]?\s*\d+\s*[）)]?\s*$")
TAIL_ELLIPSIS_RE = re.compile(r"\s*…+\s*$")
SECTION_NUM_RE = re.compile(r"第\s*(\S{1,4})\s*([篇章节])")


_LATEX_GREEK = {
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\epsilon": "ε", r"\zeta": "ζ", r"\eta": "η", r"\theta": "θ",
    r"\kappa": "κ", r"\lambda": "λ", r"\mu": "μ", r"\nu": "ν",
    r"\pi": "π", r"\rho": "ρ", r"\sigma": "σ", r"\tau": "τ",
    r"\phi": "φ", r"\omega": "ω",
}


def _convert_equation(content: str) -> str:
    """LaTeX 希腊字母 / 简单符号 → Unicode,救 mineru 把 α 等抓成 equation_inline 的情况"""
    for tex, uni in _LATEX_GREEK.items():
        content = content.replace(tex, uni)
    return content


def _text_of(items: list) -> str:
    out: list[str] = []
    for s in items:
        if not isinstance(s, dict):
            continue
        t = s.get("type")
        if t == "text":
            out.append(s.get("content", ""))
        elif t == "equation_inline":
            out.append(_convert_equation(s.get("content", "")))
    return "".join(out)


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
            if txt.strip() == "目录":  # 严格 == 防"视频资源目录"误判
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
    """本书 pg 17..48 全部 page_header='目录' 多 seed 已覆盖,但保留双向延伸防 mineru 漏标。"""
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
    "(": "(", ")": ")", ".": ".",
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

    # stitch:救 mineru 把节标题切到下一行/下一页
    # 同页 + 跨页(限相邻 1 页,防大段误合并):前行无 TAIL_PAGE 尾 + 后行不是新 anchor → 拼接
    merged_lines: list[tuple[int, str]] = []
    i = 0
    while i < len(raw_lines):
        pg, line = raw_lines[i]
        while (i + 1 < len(raw_lines)
               and raw_lines[i + 1][0] in (pg, pg + 1)
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

    print(f"=== TOC: {len(toc_pages)} pages ({toc_pages[0]}..{toc_pages[-1]}) ===")
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
