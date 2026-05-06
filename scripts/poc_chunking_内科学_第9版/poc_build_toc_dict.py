"""
POC: 目录权威清单提取(C2 chunking 第一步,DEV_SPEC §3.1.2)
====================================================================
**本规则只针对《内科学 第9版》(葛均波/徐永健/王辰主编,2018)实测有效**;
通用方法论见 [`scripts/METHODOLOGY.md`](../METHODOLOGY.md),本书特定笔记见
[`BOOK_NOTES.md`](BOOK_NOTES.md)。

本书规则:
  L1 = 第N篇
  L2 = 第N章
  L3 = 第N节(mixed depth — 第一篇 绪论 下直接挂 L4,无章无节)
  L4 = 一、xxx / [附] xxx / [附1] xxx(目录中明确标出的子条目,
        节下/章下的具体疾病或主题。**注意**:跟节内子标题"一、"形式相同
        但语义不同 — 字典里只收 TOC 实际记录的 93 个 一、 + 10 个 [附];
        节内子标题"一、"在 Step 2 里 strict_key 不命中,自然进 unmatched,
        交给 Step 3 Pass 1 切分逻辑处理。)

跟诊断学(同 mixed depth)的差异:
  - **TOC 全 16 页都标了 page_header="目录"**:不需要 _detect_toc_pages 启发延伸
  - **节 anchor 干净**:`第一节 急性上呼吸道感染`(无 `|` 分隔符)→ 不需要 PIPE_SEP_RE
  - **章名干净**:`第一章 总论`(无诊断学的页码尾 `... 70`)
  - **新增 L4 一、/[附] anchor**(诊断学/内分泌都没有这个层级):
    本书 TOC 把节下疾病/主题列到了 一、级,真实是独立内容单元,字典必须收
  - **黑名单扩展**:每篇末尾"推荐阅读"8 处 + 末尾"中英文名词对照索引"+"本书测试卷"
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CONTENT_LIST_V2 = (
    "/data/medical-resources/mineru-output/"
    "内科学 第9版_葛均波、徐永健、王辰主编2018年（可复制文字）/hybrid_auto/"
    "内科学 第9版_葛均波、徐永健、王辰主编2018年（可复制文字）_content_list_v2.json"
)

# 5 类 anchor(顺序敏感:先匹配先生效;L4 在 节/章/篇 之后,避免误吃)
# L4 一、:中文数字 + 顿号(常见 一、二、三、 直至 二十、)
# L4 [附]:[附]/[附1]/[附2]/【附】 形式,节末附录主题
PATTERNS: list[tuple[int, re.Pattern]] = [
    (1, re.compile(r"^第\s*\S{1,4}\s*篇(?=\s|$)")),
    (2, re.compile(r"^第\s*\S{1,4}\s*章(?=\s|$)")),
    (3, re.compile(r"^第\s*\S{1,4}\s*节(?=\s|$)")),
    (4, re.compile(r"^[一二三四五六七八九十百]{1,4}、")),
    (4, re.compile(r"^[\[【]\s*附\s*\d*\s*[\]】]")),
]

# 跨条目粘连二次拆分:在行内任意位置 lookahead 上述 anchor
SPLIT_ANCHOR = re.compile(
    r"(?=第\s*\S{1,4}\s*篇\s|第\s*\S{1,4}\s*章\s|第\s*\S{1,4}\s*节\s)"
)

# 黑名单(strip 后完全匹配)
BLACKLIST = {
    "上册", "下册", "全书概览", "目录", "绪论",
    "推荐阅读",                # 8 处篇末附录,不算正文章节
    "中英文名词对照索引",      # BODY_END marker,也防止误进 TOC dict
    "本书测试卷",
}

# 剥行尾"页码"尾巴(`第一节 X X 14` / `(空格 + 数字)`)
TAIL_PAGE_RE = re.compile(r"(?:[…\.]{2,}|\s|/)\s*\(?\d+\)?\s*$")

# 剥行尾"裸省略号"
TAIL_ELLIPSIS_RE = re.compile(r"\s*…+\s*$")

# 清理章节号内部空格
SECTION_NUM_RE = re.compile(r"第\s*(\S{1,4})\s*([篇章节])")

# 本书 mineru OCR 孤立 bug:`第四篇 消化系统疾病` 被识成 `(4) 消化系统疾病`
# 救:把行首 `(N) 内容` (N=1-9) 救成 `第N篇 内容`(中文数字)
# 限制后面必须有 ≥ 2 个非空白字符,避免误吃 `(4)` 这种纯子标题编号
PIAN_PAREN_RE = re.compile(r"^[\(（]\s*([1-9])\s*[\)）]\s+(\S{2,})")
_NUM_TO_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
              6: "六", 7: "七", 8: "八", 9: "九"}


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
    """page_header 含'目录'字样 → 是 TOC 页。"""
    for b in page_blocks:
        if b.get("type") == "page_header":
            txt = _text_of(b.get("content", {}).get("page_header_content", []))
            if "目录" in txt:
                return True
    return False


# 本书 16 页 TOC 全部都标了 page_header="目录",不需要诊断学的 anchor 启发延伸,
# 但保留启发逻辑以防 mineru 偶发漏标(命中 ≥ 2 anchor 仍认延续)。
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
    i = max(seeds) + 1
    while i < len(data):
        if _page_anchor_count(data[i]) >= 2:
            extended.add(i)
            i += 1
        else:
            break
    return sorted(extended)


def _normalize(s: str) -> str:
    """归一化:删 PDF 换行残留 + 折叠空白 + 章节号去内部空格 + 反复剥页码尾 + 剥省略号。

    本书额外处理:孤立 OCR bug `(4) 消化系统疾病` → `第四篇 消化系统疾病`(只针对篇)
    """
    s = s.replace("\n", "")
    s = re.sub(r"\s+", " ", s).strip()
    # 救孤立 OCR bug:行首 `(N) 内容` 看作"第N篇 内容"
    m = PIAN_PAREN_RE.match(s)
    if m:
        s = f"第{_NUM_TO_CN[int(m.group(1))]}篇 {m.group(2)}{s[m.end():]}"
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
    # 先做 OCR 救:`(4) 消化系统疾病` → `第四篇 消化系统疾病`,这样 PATTERNS L1 才命中
    m = PIAN_PAREN_RE.match(line)
    if m:
        line = f"第{_NUM_TO_CN[int(m.group(1))]}篇 {m.group(2)}{line[m.end():]}"
    for level, pat in PATTERNS:
        if pat.match(line):
            return level, _normalize(line)
    return None


def _split_glued(line: str) -> list[str]:
    return [p.strip() for p in SPLIT_ANCHOR.split(line) if p.strip()]


def _update_stack(stack: list[str], level: int, title: str) -> list[str]:
    new_stack = stack[: level - 1] + [title]
    while len(new_stack) < 5:  # 槽位扩到 5(L1-L4 + 哨兵)
        new_stack.append("")
    return new_stack


# ─────────────────────────────────────────────────────────────────────
# 本书 mineru 在 TOC 提取里出过的硬错(对照 PDF 截图人工核对,2026-05-05)
# 算法层面救不了的(漏识别整行 / OCR 错字混淆章号),走硬编码补丁
# ─────────────────────────────────────────────────────────────────────

# 修改 entries:把 (旧 title) → (新 title)。匹配时用 strict_key 比对
# 用于 OCR 字混淆等"字典内容错"的修
PATCH_REPLACE_TITLE: dict[str, str] = {
    "第二十三章糖尿病":   "第二十二章 糖尿病",       # OCR 二/三错(章号断序救)
    "第十章肺血栓栓寒症": "第十章 肺血栓栓塞症",     # OCR 寒/塞错
    "三、XX男性综合征•": "三、XX 男性综合征",        # 末尾 OCR 残留 •
}

# 注意:replace 后 strict_key 会变,下面的 entries 列表用旧 strict_key 查
# 但只对那些"重复章号"做 path 修(其他 replace 不影响 path)


# 新增 entries:mineru 完全漏识别的 entries(整行没出现在 content_list)
# 每项:(level, title, anchor_strict_key)
# 锚点 = "插在 anchor 这条 entry 之后(继承 anchor 的 stack 上下文)"
PATCH_INSERT_AFTER: list[tuple[int, str, str]] = [
    # 第六篇 第六章 第四节 血红蛋白病 下加 一、二、(mineru 把 3 行黏成 1 paragraph)
    (4, "一、珠蛋白生成障碍性贫血", "第四节血红蛋白病"),
    (4, "二、异常血红蛋白病",       "一、珠蛋白生成障碍性贫血"),

    # 第五篇 第六章 第一节 肾小管酸中毒 下加 三、混合性(mineru 漏行)
    (4, "三、混合性肾小管酸中毒", "二、近端肾小管酸中毒"),

    # 第三篇 第四章 第三节 慢性心肌缺血综合征 下加 三、缺血性心肌病(mineru 漏顿号)
    (4, "三、缺血性心肌病", "二、隐匿型冠心病"),

    # 第九篇 第二章 中毒 下加 第一节 概述(mineru TOC 漏识,正文 pg 906 已找到)
    (3, "第一节 概述", "第二章中毒"),

    # 第七篇 第二十六章 水、电解质代谢和酸碱平衡失常 下加 第一节 水、钠代谢失常
    # mineru 漏识 → 导致 一、失水/二/三/四 直接挂在 L2 章下,heading_path 缺中间节
    # 加上 L3 + 下面 _rebuild_paths 会自动把 4 个 L4 重新 stack 进来
    (3, "第一节 水、钠代谢失常", "第二十六章水、电解质代谢和酸碱平衡失常"),
]


def _rebuild_paths(entries: list[tuple[int, str, str, int]]) -> list[tuple[int, str, str, int]]:
    """全量 stack-walk 重算每条 entry 的 path。

    insert/replace 后 path 可能跟新的 stack 不一致(尤其新 insert L3 后下游 L4 还挂在
    L2 下),这里走一遍重建。逻辑跟 build_toc_dict 主循环里的 stack 处理一致。
    """
    stack = ["", "", "", "", ""]
    rebuilt: list[tuple[int, str, str, int]] = []
    for lvl, title, _old_path, pg in entries:
        # 更新 stack:第 lvl-1 槽放 title,后面的槽清空
        new_stack = stack[: lvl - 1] + [title]
        while len(new_stack) < 5:
            new_stack.append("")
        stack = new_stack
        # filter empty:mixed depth (e.g. L1 直接挂 L4) 时空 L2/L3 槽自然过滤
        path = " / ".join(x for x in stack[:lvl] if x)
        rebuilt.append((lvl, title, path, pg))
    return rebuilt


def _apply_patches(entries: list[tuple[int, str, str, int]]) -> list[tuple[int, str, str, int]]:
    """对 entries 应用本书硬编码补丁(replace + insert)。"""
    # Step A:replace title
    new_entries: list[tuple[int, str, str, int]] = []
    replaced_old_keys: set[str] = set()
    for lvl, title, path, pg in entries:
        k = strict_key(title)
        if k in PATCH_REPLACE_TITLE:
            new_title = PATCH_REPLACE_TITLE[k]
            # 同步修 path:把路径中含旧 title 的部分也替换
            new_path = path.replace(title, new_title) if title in path else path
            new_entries.append((lvl, new_title, new_path, pg))
            replaced_old_keys.add(k)
        else:
            new_entries.append((lvl, title, path, pg))

    # Step B:章号断序救 — 一篇内重复章号,更早的章号 -1
    # 只针对"第二十三章 糖尿病"这种(已 replace 成 第二十二章 糖尿病),
    # path 里的章号也要相应改
    # 实际 PATCH_REPLACE_TITLE 里 第二十三章糖尿病 → 第二十二章糖尿病 已经处理 title,
    # 但糖尿病下属的 L3 节 path 里"第二十三章 糖尿病"还在 — 一并修
    if "第二十三章糖尿病" in replaced_old_keys:
        fixed = []
        for lvl, title, path, pg in new_entries:
            new_path = path.replace("第二十三章 糖尿病", "第二十二章 糖尿病")
            fixed.append((lvl, title, new_path, pg))
        new_entries = fixed

    # Step C:insert_after — 找锚点,在其后插入新 entry(path 用占位,Step D 重算)
    for ins_lvl, ins_title, anchor_key in PATCH_INSERT_AFTER:
        for i, (lvl, title, path, pg) in enumerate(new_entries):
            if strict_key(title) == anchor_key:
                new_entries.insert(i + 1, (ins_lvl, ins_title, "", pg))
                break

    # Step D:全量 stack-walk 重算所有 entries 的 path
    # 这一步保证 insert L3 后下游 L4 自动挂到新 L3 下(修第七篇第二十六章漏第一节问题)
    new_entries = _rebuild_paths(new_entries)

    return new_entries


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

    stack = ["", "", "", "", ""]
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

    # 应用本书硬编码补丁(replace OCR 错字章号 + insert mineru 漏识 entries)
    entries = _apply_patches(entries)

    lookup: dict[str, list[tuple[int, str, str]]] = {}
    for level, title, path, _pg in entries:
        if level > 4:  # 收 L1-L4(本书 L4 = 一、/[附])
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
