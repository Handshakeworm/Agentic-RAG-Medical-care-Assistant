"""tests/unit/test_chunking_extractor.py — C2 step2 block extractor 锁定。

按 DEV_SPEC §3.1.2 白名单/黑名单 + §2.4.4.1 真实 block 嵌套结构断言。
所有测试纯函数,无 IO。
"""

from __future__ import annotations

from src.rag.ingestion.chunking import extract_chunkable_text


# ───────────────────────── 白名单 6 种 type ─────────────────────────


def test_title_extracts_concatenated_text() -> None:
    blk = {"type": "title",
           "content": {"title_content": [{"type": "text", "content": "第一章 发热"}],
                       "level": 2}}
    assert extract_chunkable_text(blk) == "第一章 发热"


def test_paragraph_extracts_concatenated_text() -> None:
    blk = {"type": "paragraph",
           "content": {"paragraph_content": [
               {"type": "text", "content": "发热是机体在"},
               {"type": "text", "content": "致热源作用下的状态。"},
           ]}}
    assert extract_chunkable_text(blk) == "发热是机体在致热源作用下的状态。"


def test_paragraph_skips_non_text_subitems() -> None:
    """混入 inline_equation 等非 text 子项应跳过(暂不抽公式)。"""
    blk = {"type": "paragraph",
           "content": {"paragraph_content": [
               {"type": "text", "content": "公式 "},
               {"type": "inline_equation", "content": "x^2"},
               {"type": "text", "content": " 推导"},
           ]}}
    assert extract_chunkable_text(blk) == "公式  推导"


def test_unordered_list_uses_dash_prefix() -> None:
    blk = {"type": "list",
           "content": {"list_type": "text_list",
                       "list_items": [
                           {"item_type": "text", "item_content": [{"type": "text", "content": "项目一"}]},
                           {"item_type": "text", "item_content": [{"type": "text", "content": "项目二"}]},
                       ]}}
    assert extract_chunkable_text(blk) == "- 项目一\n- 项目二"


def test_ordered_list_uses_number_prefix() -> None:
    blk = {"type": "list",
           "content": {"list_type": "ordered_list",
                       "list_items": [
                           {"item_type": "text", "item_content": [{"type": "text", "content": "第一步"}]},
                           {"item_type": "text", "item_content": [{"type": "text", "content": "第二步"}]},
                           {"item_type": "text", "item_content": [{"type": "text", "content": "第三步"}]},
                       ]}}
    assert extract_chunkable_text(blk) == "1. 第一步\n2. 第二步\n3. 第三步"


def test_list_skips_empty_items() -> None:
    """空 item 不产出 dangling prefix(- + 空)。"""
    blk = {"type": "list",
           "content": {"list_type": "text_list",
                       "list_items": [
                           {"item_type": "text", "item_content": [{"type": "text", "content": "有内容"}]},
                           {"item_type": "text", "item_content": []},
                           {"item_type": "text", "item_content": [{"type": "text", "content": "再有"}]},
                       ]}}
    assert extract_chunkable_text(blk) == "- 有内容\n- 再有"


def test_table_concatenates_caption_html_footnote() -> None:
    blk = {"type": "table",
           "content": {
               "image_source": {"path": "x.jpg"},
               "table_caption": [{"type": "text", "content": "表1-1 鉴别"}],
               "html": "<table><tr><td>a</td></tr></table>",
               "table_footnote": [{"type": "text", "content": "注:数据来源..."}],
           }}
    out = extract_chunkable_text(blk)
    assert out is not None
    assert "表1-1 鉴别" in out
    assert "<table>" in out  # html 原样保留(step3 双粒度才解析)
    assert "数据来源" in out


def test_chart_concatenates_caption_and_markdown_content() -> None:
    blk = {"type": "chart",
           "content": {
               "image_source": {"path": "y.jpg"},
               "chart_caption": [{"type": "text", "content": "图1-2 体温曲线"}],
               "content": "| 天 | 温度 |\n| --- | --- |\n| 1 | 38.2 |",
           }}
    out = extract_chunkable_text(blk)
    assert "图1-2 体温曲线" in out
    assert "| 天 | 温度 |" in out


def test_equation_interline_extracts_latex() -> None:
    blk = {"type": "equation_interline",
           "content": {"math_content": r"\frac{a}{b}", "math_type": "latex"}}
    assert extract_chunkable_text(blk) == r"\frac{a}{b}"


# ───────────────────────── 黑名单 4 种 type ─────────────────────────


def test_image_returns_none() -> None:
    """image content 已被 C1 loader 删除;即便残留也不抽(spec §3.1.2 黑名单)。"""
    blk = {"type": "image",
           "content": {"image_source": {"path": "x.jpg"},
                       "image_caption": [{"type": "text", "content": "图1-1"}]}}
    assert extract_chunkable_text(blk) is None


def test_page_header_returns_none() -> None:
    blk = {"type": "page_header",
           "content": {"page_header_content": [{"type": "text", "content": "+ "}]}}
    assert extract_chunkable_text(blk) is None


def test_page_footer_returns_none() -> None:
    blk = {"type": "page_footer", "content": {"page_footer_content": []}}
    assert extract_chunkable_text(blk) is None


def test_page_number_returns_none() -> None:
    blk = {"type": "page_number", "content": {"page_number_content": []}}
    assert extract_chunkable_text(blk) is None


# ───────────────────────── 防御性边界 ─────────────────────────


def test_unknown_type_returns_none() -> None:
    """未知 type 不该抛错,返 None 让主循环跳过。"""
    blk = {"type": "video", "content": {"foo": "bar"}}
    assert extract_chunkable_text(blk) is None


def test_empty_text_returns_none() -> None:
    """白名单 type 但抽出空文本应返 None,避免主循环把空字符串当有效 chunk 累积。"""
    blk = {"type": "paragraph", "content": {"paragraph_content": []}}
    assert extract_chunkable_text(blk) is None


def test_missing_content_field_returns_none() -> None:
    """彻底没 content 字段也不崩。"""
    assert extract_chunkable_text({"type": "paragraph"}) is None
    assert extract_chunkable_text({"type": "title"}) is None
