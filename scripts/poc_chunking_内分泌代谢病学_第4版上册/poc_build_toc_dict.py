"""
POC: 目录权威清单提取(C2 chunking 第一步,DEV_SPEC §3.1.2)
====================================================================
**本规则只针对《内分泌代谢病学 第4版上册》实测有效**;其他书目录
格式可能不同(章节命名 / 缩进 / 是否有"扩展资源" / N.N 是否使用),
都需要单独适配,不要直接复用本脚本。

本书规则(用户决策):
  L1 = 第N篇
  L2 = 第N章
  L3 = 第N节 / 扩展资源 N(并列,父都是当前 L2)
  L4 = N.N(父是当前 L3,即上一个"扩展资源 N")

实现要点:
  1. 目录页定位:page_header 含"目录"字样
  2. PARA + TITLE + LIST 都扫(mineru 在目录里 type 标记不一致)
  3. 跨条目粘连二次拆分(mineru 会把"第2节...56第3节..."焊一行)
  4. 黑名单剔除分册标识("上册"/"下册"/"全书概览"/"目录")
  5. normalize:换行残留 \n 直接删(PDF 排版换行无语义);其他空白折叠
  6. stack 维护:遇到 Lk 截到 k-1 再 append,后续 L>k 全清

输出:`/tmp/poc_toc_v4.txt` 完整目录树 + 统计 + 未匹配行清单
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CONTENT_LIST_V2 = (
    "/data/medical-resources/mineru-output/"
    "内分泌代谢病学 第4版上册/hybrid_auto/"
    "内分泌代谢病学 第4版上册_content_list_v2.json"
)

# 5 类 anchor(顺序敏感:先匹配先生效)
PATTERNS: list[tuple[int, re.Pattern]] = [
    (1, re.compile(r"^第\s*\S{1,4}\s*篇(?=\s|$)")),
    (2, re.compile(r"^第\s*\S{1,4}\s*章(?=\s|$)")),
    (3, re.compile(r"^第\s*\S{1,4}\s*节(?=\s|$)")),
    (3, re.compile(r"^扩展资源\s*\d+(?=\s|$)")),
    (4, re.compile(r"^\d+\.\d+(?=\s|$)")),
]

# 跨条目粘连二次拆分:在行内任意位置 lookahead 上述 anchor
SPLIT_ANCHOR = re.compile(
    r"(?=第\s*\S{1,4}\s*篇\s|第\s*\S{1,4}\s*章\s|第\s*\S{1,4}\s*节\s|扩展资源\s*\d+\s|(?<![\d.])\d+\.\d+\s)"
)

# 分册标识黑名单(strip 后完全匹配)
BLACKLIST = {"上册", "下册", "全书概览", "目录"}

# 剥行尾"页码"尾巴(支持 "…… 24" / " 40 " / " / 1209" 等)
TAIL_PAGE_RE = re.compile(r"(?:[…\.]{2,}|\s|/)\s*\(?\d+\)?\s*$")

# 剥行尾"裸省略号"(单个或多个 …,后面没数字),如 "...综合征 …"
TAIL_ELLIPSIS_RE = re.compile(r"\s*…+\s*$")

# 清理章节号内部空格:"第 5 节" → "第5节"(节号后面的分隔空格不动)
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
    for b in page_blocks:
        if b.get("type") == "page_header":
            txt = _text_of(b.get("content", {}).get("page_header_content", []))
            if "目录" in txt:
                return True
    return False


def _normalize(s: str) -> str:
    """归一化:删 PDF 换行残留 + 折叠空白 + 剥页码尾 + 剥裸省略号。

    保留语义分隔空格(节号 vs 节名之间、中文 vs ASCII 之间),用于显示。
    匹配 lookup 时用 strict_key()(下方)进一步去掉所有空格。
    """
    s = s.replace("\n", "")           # 换行残留无语义,直接删
    s = re.sub(r"\s+", " ", s).strip()  # 其他空白(空格/制表符)折叠为单空格
    s = SECTION_NUM_RE.sub(r"第\1\2", s)  # "第 5 节" → "第5节"
    while True:
        new = TAIL_PAGE_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    s = TAIL_ELLIPSIS_RE.sub("", s).strip()
    return s


def strict_key(s: str) -> str:
    """匹配用 lookup key:在 _normalize 基础上去掉所有空白。

    必要性:mineru 在目录 vs 正文里对"中文 ↔ ASCII 之间是否插空格"风格
    不一致(同一标题在 TOC 是"1 型多发性",在正文是"1型多发性"),保留
    空格的 normalized 形式无法对齐。匹配时一律去空白。
    """
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
        lookup:            {normalized_title: [(level, parent_path), ...]}
                           same-name conflicts → list of >1 candidates
        skipped_blacklist: list[str]  分册标识等被剔除的
        skipped_unmatched: list[(page_idx, raw_text)]  目录页内未识别的零散行
        toc_pages:         list[int]  TOC 页 page_idx
    """
    data = json.loads(Path(CONTENT_LIST_V2).read_text())
    toc_pages = [i for i, p in enumerate(data) if _is_toc_page(p)]

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

    # lookup 只收 L1-L3,且剔除"扩展资源 N":
    #   - L4 (N.N):正文中不展开,扩展资源的子标题
    #   - L3 扩展资源 N:正文中只是二维码占位 + 外部资源,无实际正文
    # entries 仍保留全部给人审目录树用,只是不参与 body 匹配。
    # key 用 strict_key (去全部空白) 解决 mineru 目录/正文空格风格不一致。
    lookup: dict[str, list[tuple[int, str, str]]] = {}
    for level, title, path, _pg in entries:
        if level > 3:
            continue
        if title.startswith("扩展资源"):
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
