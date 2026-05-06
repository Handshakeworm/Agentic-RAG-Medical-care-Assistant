"""chunking — 父子分块 + 表格双粒度(DEV_SPEC §3.1.2)。

当前完成:
- block extractor 适配器(白名单 6 种 type 抽正文,黑名单返 None)
- POC 验证完整切分主流程(《内分泌代谢病学第4版上册》),
  位于 scripts/poc_chunking_内分泌代谢病学_第4版上册/poc_chunk_book.py。

待 port 到 production(本文件):
- Step 1: 目录权威清单提取(POC: poc_build_toc_dict_*.py)
- Step 2: 正文节边界匹配 + REAL_START 选取(POC: poc_match_body_titles_*.py)
- Step 3: 书末截断(中文名词索引/英文缩略语索引/彩色插图)+ 节内参考文献丢弃
- Step 4: 父块构建(节本身/节内三遍切【】+(一)+1./严格层级合并)
- Step 5: 子块构建(size 驱动,目标 600 字,父块 ≤ 1200 字直接当 child)
- 表格双粒度(整表 chunk + 逐行 chunk),与父子分块独立路径
- 调 C3 算 chunk_id + 调 B1 bulk_upsert_chunks 写 PG
- 12 本书逐本适配 anchor pattern(每本书目录格式可能差异大)

**已弃案**:
- 基于正则的 title.level 重建(`rebuild_title_levels`)— 因每本书章节约定差异大,
  正则归级不可靠;且节内子节(【】/(一)/1.) mineru type 标记不一致,正则补救不全。
  改用"目录权威清单"思路:从目录页提取本书目录字典,作为章节层级唯一真值。
- LangChain `RecursiveCharacterTextSplitter` 子块切分 — POC 验证发现"父块和子块共用
  同一组标题边界"会让小父块 degenerate 成"父=子"。改用 size 驱动的 mineru block
  累积算法(算法详见 POC METHODOLOGY §5.7)。
"""

from __future__ import annotations


def _extract_text_from_items(items: list) -> str:
    """通用工具:从 mineru block.content.*_content 列表中拼出纯文本。

    items 是 list[{"type": "text"|"inline_equation"|..., "content": "..."}],
    本函数只取 type=text 子项拼接,跳过其他类型(如 inline_equation 暂不抽)。
    """
    if not isinstance(items, list):
        return ""
    return "".join(
        sub.get("content", "")
        for sub in items
        if isinstance(sub, dict) and sub.get("type") == "text"
    )


def _extract_title_text(block: dict) -> str:
    """从 title block 抽标题原文。"""
    return _extract_text_from_items(block.get("content", {}).get("title_content", []))


# ────────────────────────────────────────────────────────────────────────────
# C2 step2: block extractor 适配器(DEV_SPEC §3.1.2 白名单/黑名单)
#
# extract_chunkable_text(block) -> str | None
#   white-list 6 种 type 抽出可入库文本;black-list 4 种返回 None。
#   chunking 主循环只对返回非 None 的 block 做后续 splitter / 父子索引处理。
# ────────────────────────────────────────────────────────────────────────────


# 白名单 6 种 type(spec §3.1.2 白名单表)
_WHITELIST_TYPES = frozenset({
    "title", "paragraph", "list", "table", "chart", "equation_interline",
})

# 黑名单 type:页眉/页脚/页码/图像 + 12 本实数据扫出的额外 page_* 系列
# spec §3.1.2 黑名单表列了 4 种,但实测 mineru hybrid 还会吐出 `page_aside_text` /
# `page_footnote`(2026-05-02 12 本教材扫出各 8 个);本项目对所有 page_* 系列均视为噪音。
_BLACKLIST_TYPES = frozenset({
    "image",
    "page_header", "page_footer", "page_number",
    "page_aside_text", "page_footnote",
})


def _extract_list_text(block: dict) -> str:
    """list 块:递归抽 list_items[].item_content[].content,按 list_type 加序号前缀。

    list_type:
    - "ordered_list" → 项前加 "1. "、"2. " 序号
    - "unordered_list" / 缺省 → 项前加 "- "
    """
    content = block.get("content", {})
    list_type = content.get("list_type", "")
    items = content.get("list_items", [])
    if not isinstance(items, list):
        return ""

    is_ordered = "ordered" in list_type  # 容忍各种命名风格
    out: list[str] = []
    for idx, it in enumerate(items, 1):
        if not isinstance(it, dict):
            continue
        text = _extract_text_from_items(it.get("item_content", []))
        if not text:
            continue
        prefix = f"{idx}. " if is_ordered else "- "
        out.append(prefix + text)
    return "\n".join(out)


def _extract_table_text(block: dict) -> str:
    """table 块:caption + html(扁平形式,留给 step3 双粒度做精细拆)。

    本 step 只是给主流程一个"text 表征"用于父块累积;真正的 table chunk
    生成(整表 + 逐行)在 step3 单独函数处理。
    """
    content = block.get("content", {})
    caption = _extract_text_from_items(content.get("table_caption", []))
    html = content.get("html", "") or ""
    footnote = _extract_text_from_items(content.get("table_footnote", []))
    parts = [p for p in (caption, html, footnote) if p]
    return "\n".join(parts)


def _extract_chart_text(block: dict) -> str:
    """chart 块:caption + content(content 已是 markdown 数据表)。"""
    content = block.get("content", {})
    caption = _extract_text_from_items(content.get("chart_caption", []))
    body = content.get("content", "") or ""  # markdown 数据表
    parts = [p for p in (caption, body) if p]
    return "\n".join(parts)


def _extract_equation_text(block: dict) -> str:
    """equation_interline 块:抽 latex 公式文本。"""
    return block.get("content", {}).get("math_content", "") or ""


def extract_chunkable_text(block: dict) -> str | None:
    """C2 主入口:把 block 抽成可入 chunks 表的纯文本(spec §3.1.2 白名单适配器)。

    返回 None 表示该 block 不进 chunking pipeline(黑名单 / 抽不到正文 / 未知 type)。
    chunking 主循环对 None 的 block 直接跳过。

    注意:table / chart 这里返回的扁平文本是"主流程父块累积"用的占位,真正的
    双粒度 chunk(整表 + 逐行)由 step3 单独函数从 block 直接生成,不复用本函数。
    """
    btype = block.get("type")
    if btype not in _WHITELIST_TYPES:
        # 黑名单 + 未知 type 一律 None
        return None

    if btype == "title":
        return _extract_title_text(block) or None
    if btype == "paragraph":
        text = _extract_text_from_items(block.get("content", {}).get("paragraph_content", []))
        return text or None
    if btype == "list":
        return _extract_list_text(block) or None
    if btype == "table":
        return _extract_table_text(block) or None
    if btype == "chart":
        return _extract_chart_text(block) or None
    if btype == "equation_interline":
        return _extract_equation_text(block) or None
    return None  # defensive fallback
