"""
POC: 目录权威清单提取(C2 chunking 第一步,DEV_SPEC §3.1.2)
====================================================================
**本规则只针对《神经内科学》(第 2 版)实测有效**;通用方法论见
[`scripts/METHODOLOGY.md`](../METHODOLOGY.md),本书特定笔记见
[`BOOK_NOTES.md`](BOOK_NOTES.md)。

本书规则:
  L1 = 第N篇(10 个)
  L2 = 第N章(110 个,跨篇连续编号)
  L3 = 第N节(317 个) + 附录:xxx(独立条目,L3 级)
  L4 = 一、二、三、(顿号编号,目录里出现)

跟神经外科学(同 L1-L3)的差异:
  - **新增 L4 anchor**:目录里出现 "一、概念中的分歧" 这种条目
  - **新增附录 anchor**:挂 L3 同级,3 处独立附录
  - **目录 11 页**(pg 10..20),比神经外科学(pg 5-7)长得多,多行换行严重
  - **节内子标题 4 种共存**:【】=0 / (一)=463 / (1)=340 / 1.=765 / 一、=732
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CONTENT_LIST_V2 = (
    "/data/medical-resources/mineru-output/"
    "神经内科学 第2版/hybrid_auto/"
    "神经内科学 第2版_content_list_v2.json"
)

# 4 类 anchor(顺序敏感:先匹配先生效,L4 必须在 L3 之后避免误吃 一、xxx)
PATTERNS: list[tuple[int, re.Pattern]] = [
    (1, re.compile(r"^第\s*\S{1,4}\s*篇(?=\s|$)")),
    (2, re.compile(r"^第\s*\S{1,4}\s*章(?=\s|$)")),
    (3, re.compile(r"^第\s*\S{1,4}\s*节(?=\s|$)")),
    (3, re.compile(r"^附\s*录\s*[:：]")),                # 附录:xxx 挂 L3 同级
    (4, re.compile(r"^[一二三四五六七八九十百]+\s*[、.]\s*\S")),  # L4 一、xxx
]

# 跨条目粘连二次拆分:复用神经外科学的双规则
SPLIT_ANCHOR = re.compile(
    r"(?=第\s*\S{1,4}\s*[篇章节]\s)"
    r"|(?<=\d)(?=第\s*\S{1,4}\s*[篇章节])"
)

BLACKLIST = {
    "上册", "下册", "全书概览", "目录", "绪论",
    "神经内科学",
}

# 暂无硬编码补丁,等 baseline 跑出来再决定
PATCH_REPLACE_TITLE: dict[str, str] = {}

# 行级预处理补丁:在 _classify 之前作用,救 mineru OCR 把开头"第"字漏掉的章号
# 神经内科学 pg 14 blk 24:`'十五章 急性炎性脱髓鞘性多发性 神经病 247'` 缺"第"字
# 正文 pg 255 paragraph 是完整 "第十五章 急性炎性脱髓鞘性多发性神经病"
PATCH_LINE_PREPROCESS: list[tuple[str, str]] = [
    # 章号被 OCR 漏掉"第"字
    ("十五章 急性炎性脱髓鞘性多发性 神经病 247",
     "第十五章 急性炎性脱髓鞘性多发性神经病 247"),
    # 跨 page L3 切碎(pg 14 末 + pg 15 首)
    # 字典里需要它合并成完整的"第三节 与其他慢性获得性炎性周围神经病的关系"
    ("第三节 与其他慢性获得性炎性",
     "第三节 与其他慢性获得性炎性周围神经病的关系 260"),
]


# 正文 candidate 硬编码 inject:救 mineru 在正文里完全没标 type=title 的字典 entry
# 神经内科学:第十章末 page_idx 215 是 TABLE block(附录"抗癫痫药物中英文名称及缩写对照"
# 整个章节就是表格,无 title block),无法通过常规匹配恢复 → inject (pg, blk) 坐标
# 格式:(pg_idx, blk_idx, raw_title) — raw_title 必须能 strict_key 命中字典 lookup
PATCH_INJECT_CANDIDATES: list[tuple[int, int, str]] = [
    (215, 0, "附录:抗癫痫药物中英文名称及缩写对照"),
]

TAIL_PAGE_RE = re.compile(r"(?:[…\.]{2,}|\s|/)\s*\(?\d+\)?\s*$")
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
    """匹配 key:_normalize + 去空白 + 标点全/半角统一(救 mineru 目录/正文 OCR 不一致)。

    神经内科学:目录里 `第六节 神经保护路在何方？`(全角 ?),正文里 `?`(半角)
    跨 page 合并:加 PATCH_LINE_PREPROCESS 处理 L3 切碎到下一 page 的情况
    """
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
                    raw_lines.append((pg, line))

    # 同 page 相邻行合并 — 只救 L4 切碎(L1/L2/L3 anchor 本身完整,不应吃延续行)
    # 救:`'二、获得性因素——不同人群中的'` + `'差异 122'` → `'二、获得性因素——不同人群中的差异 122'`
    # 不救:`'第一篇 脑血管疾病'` + `'概述 4'` → 保留两行(后者本应单独 _classify,但当前规则不识别"概述")
    PAGE_TAIL_RE = re.compile(r"\d+\s*$")
    # 救 L3/L4 切碎:前一行能 _classify 成 L3 节 / 附录 / L4(一、),且无页码 → 吃后面延续
    # L1/L2(篇/章)anchor 一律不吃延续(它们后跟"概述"等真独立小标题)
    L3_OR_L4_RE = re.compile(
        r"^第\s*\S{1,4}\s*节(?=\s|$)"
        r"|^附\s*录\s*[:：]"
        r"|^[一二三四五六七八九十百]+\s*[、.]"
    )
    merged_lines: list[tuple[int, str]] = []
    i = 0
    while i < len(raw_lines):
        pg, line = raw_lines[i]
        flat = re.sub(r"\s+", " ", line.replace("\n", " ")).strip()
        is_eligible = bool(L3_OR_L4_RE.match(flat))
        while is_eligible and not PAGE_TAIL_RE.search(flat) and i + 1 < len(raw_lines):
            npg, nline = raw_lines[i + 1]
            if npg != pg:
                break
            nflat = re.sub(r"\s+", " ", nline.replace("\n", " ")).strip()
            # 后行是任何 anchor(L1/L2/L3/附录/L4)→ 独立条目,不吞
            if any(pat.match(nflat) for _, pat in PATTERNS):
                break
            flat = (flat + " " + nflat).strip()
            i += 1
        merged_lines.append((pg, flat))
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
