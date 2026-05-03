"""tests/unit/test_mineru_loader.py — C1 loader 不依赖真 PG 的纯逻辑测试。

构造假 mineru 输出目录,mock B5 upsert,验证:
- 文件定位 / 读取 / JSON 校验
- image content 双清洗(v2 + markdown,同时验证短指纹门槛 + 验证后报告)
- 其他 type(table/chart/image_caption)未被波及
- stats dict 结构与 §3.1.4 source_id 计算
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_mineru_fixture(
    mineru_dir: Path,
    stem: str,
    markdown: str,
    content_list: list,
    middle: dict | list | None = None,
    model: dict | list | None = None,
) -> None:
    mineru_dir.mkdir(parents=True, exist_ok=True)
    (mineru_dir / f"{stem}.md").write_text(markdown, encoding="utf-8")
    (mineru_dir / f"{stem}_content_list_v2.json").write_text(
        json.dumps(content_list, ensure_ascii=False), encoding="utf-8"
    )
    (mineru_dir / f"{stem}_middle.json").write_text(
        json.dumps(middle or {}, ensure_ascii=False), encoding="utf-8"
    )
    (mineru_dir / f"{stem}_model.json").write_text(
        json.dumps(model or {}, ensure_ascii=False), encoding="utf-8"
    )


# ───────────────────────── 文件定位与校验 ─────────────────────────


def test_missing_md_raises(tmp_path: Path) -> None:
    """空目录应 raise FileNotFoundError(B5 4 字段全 NOT NULL,不允许传 None)。"""
    from src.rag.ingestion.mineru_loader import load_mineru_output

    with pytest.raises(FileNotFoundError, match="未找到 .md"):
        load_mineru_output(tmp_path, "诊断学.pdf")


def test_missing_one_json_raises(tmp_path: Path) -> None:
    (tmp_path / "x.md").write_text("# t")
    (tmp_path / "x_content_list_v2.json").write_text("[]")
    # 缺 middle 和 model

    from src.rag.ingestion.mineru_loader import load_mineru_output

    with pytest.raises(FileNotFoundError, match="mineru 产物缺失"):
        load_mineru_output(tmp_path, "诊断学.pdf")


def test_corrupted_json_raises_with_path(tmp_path: Path) -> None:
    (tmp_path / "x.md").write_text("# t")
    (tmp_path / "x_content_list_v2.json").write_text("not a valid json{{")
    (tmp_path / "x_middle.json").write_text("[]")
    (tmp_path / "x_model.json").write_text("[]")

    from src.rag.ingestion.mineru_loader import load_mineru_output

    with pytest.raises(ValueError, match="JSON 解析失败"):
        load_mineru_output(tmp_path, "诊断学.pdf")


def test_v1_flat_structure_rejected(tmp_path: Path) -> None:
    """v1 是扁平 list[dict],应 raise(我们只消费 v2 list[list[dict]])。"""
    _write_mineru_fixture(
        tmp_path, "x", "# t",
        content_list=[{"type": "paragraph"}],  # v1 扁平
    )
    from src.rag.ingestion.mineru_loader import load_mineru_output

    with pytest.raises(ValueError, match="顶层 list\\[0\\] 不是 list"):
        load_mineru_output(tmp_path, "诊断学.pdf")


# ───────────────────────── 双清洗逻辑 ─────────────────────────


def test_image_content_dropped_from_v2_but_other_types_intact(tmp_path: Path) -> None:
    """image.content 被删,table/chart/paragraph 等 content 字段必须保留。"""
    long_hallucination = "1. 体验智能学习\n2. 站体教学\n3. 站体教学\n4. 站体教学"
    content_list = [[
        # image — content 必须删
        {"type": "image",
         "content": {"image_source": {"path": "images/x.jpg"},
                     "image_caption": [{"type": "text", "content": "图1-1 心脏解剖"}],
                     "image_footnote": [],
                     "content": long_hallucination}},
        # table — content 必须保留
        {"type": "table",
         "content": {"image_source": {"path": "images/y.jpg"},
                     "table_caption": [{"type": "text", "content": "表1-1"}],
                     "html": "<table><tr><td>a</td></tr></table>",
                     "table_footnote": []}},
        # chart — content 必须保留
        {"type": "chart",
         "content": {"image_source": {"path": "images/z.jpg"},
                     "chart_caption": [{"type": "text", "content": "图1-2"}],
                     "content": "| 列1 | 列2 |\n| --- | --- |\n| a | b |"}},
        # paragraph — 完全不动
        {"type": "paragraph",
         "content": {"paragraph_content": [{"type": "text", "content": "正文段落"}]}},
    ]]
    _write_mineru_fixture(tmp_path, "x", f"前\n{long_hallucination}\n后", content_list)

    from src.rag.ingestion.mineru_loader import load_mineru_output

    with patch("src.rag.ingestion.mineru_loader.upsert_source"), \
         patch("src.rag.ingestion.mineru_loader.upsert_raw_document") as mock_raw:
        load_mineru_output(tmp_path, "诊断学.pdf")
        kwargs = mock_raw.call_args.kwargs

    cl = kwargs["content_list"]
    # image.content 被删
    assert "content" not in cl[0][0]["content"]
    # image_caption / image_footnote / image_source 保留
    assert cl[0][0]["content"]["image_source"]["path"] == "images/x.jpg"
    assert cl[0][0]["content"]["image_caption"][0]["content"] == "图1-1 心脏解剖"
    # table.html 保留(关键!)
    assert cl[0][1]["content"]["html"] == "<table><tr><td>a</td></tr></table>"
    # chart.content 保留(markdown 数据表)
    assert cl[0][2]["content"]["content"].startswith("| 列1")
    # paragraph 完全不动
    assert cl[0][3]["content"]["paragraph_content"][0]["content"] == "正文段落"


def test_markdown_hallucination_text_deleted(tmp_path: Path) -> None:
    """markdown 里出现的 image content 应通过 substring replace 被精确删除。"""
    snippet = "1. 体验智能学习\n2. 站体教学\n3. 站体教学\n4. 站体教学"
    markdown = f"# 教材\n\n![](images/x.jpg)\n{snippet}\n\n第一章 正文"
    content_list = [[
        {"type": "image",
         "content": {"image_source": {"path": "images/x.jpg"}, "content": snippet}},
    ]]
    _write_mineru_fixture(tmp_path, "x", markdown, content_list)

    from src.rag.ingestion.mineru_loader import load_mineru_output

    with patch("src.rag.ingestion.mineru_loader.upsert_source"), \
         patch("src.rag.ingestion.mineru_loader.upsert_raw_document") as mock_raw:
        stats = load_mineru_output(tmp_path, "诊断学.pdf")
        clean_md = mock_raw.call_args.kwargs["markdown_content"]

    assert "站体教学" not in clean_md
    assert "体验智能学习" not in clean_md
    # 占位符保留(不顺手删,Q2 决策)
    assert "![](images/x.jpg)" in clean_md
    # 正文保留
    assert "第一章 正文" in clean_md
    # stats 报告无遗漏
    assert stats["markdown_unclean_snippets"] == []
    assert stats["image_blocks_content_dropped"] == 1


def test_short_snippet_skipped_in_markdown_clean(tmp_path: Path) -> None:
    """短指纹(< 20 字符)应跳过 markdown 清洗,防止误删合法短文本。"""
    short = "图1-1"  # 4 字符,合法图名也长这样
    markdown = f"# 教材\n\n图1-1 这是出版社的合法图名,不该被删\n"
    content_list = [[
        {"type": "image",
         "content": {"image_source": {"path": "images/x.jpg"}, "content": short}},
    ]]
    _write_mineru_fixture(tmp_path, "x", markdown, content_list)

    from src.rag.ingestion.mineru_loader import load_mineru_output

    with patch("src.rag.ingestion.mineru_loader.upsert_source"), \
         patch("src.rag.ingestion.mineru_loader.upsert_raw_document") as mock_raw:
        load_mineru_output(tmp_path, "诊断学.pdf")
        clean_md = mock_raw.call_args.kwargs["markdown_content"]

    # 短指纹未触发 replace,合法图名保留
    assert "图1-1 这是出版社的合法图名" in clean_md


def test_image_with_empty_content_field_safe(tmp_path: Path) -> None:
    """image 块 content 字段是空串或缺失,不应崩。"""
    content_list = [[
        {"type": "image", "content": {"image_source": {"path": "x.jpg"}, "content": ""}},
        {"type": "image", "content": {"image_source": {"path": "y.jpg"}}},  # 无 content
    ]]
    _write_mineru_fixture(tmp_path, "x", "# t", content_list)

    from src.rag.ingestion.mineru_loader import load_mineru_output

    with patch("src.rag.ingestion.mineru_loader.upsert_source"), \
         patch("src.rag.ingestion.mineru_loader.upsert_raw_document"):
        stats = load_mineru_output(tmp_path, "诊断学.pdf")

    assert stats["image_blocks_content_dropped"] == 0  # 空 / 缺失都不算


# ───────────────────────── source_id 与 stats ─────────────────────────


def test_source_id_uses_original_pdf_name_not_md_stem(tmp_path: Path) -> None:
    """source_id 必须基于调用方传入的 original_pdf_name(Q1-A),不是 mineru 生成的 stem。"""
    _write_mineru_fixture(tmp_path, "诊断学 第10版", "# t", [])

    from src.rag.ingestion.idempotency import compute_source_id
    from src.rag.ingestion.mineru_loader import load_mineru_output

    with patch("src.rag.ingestion.mineru_loader.upsert_source"), \
         patch("src.rag.ingestion.mineru_loader.upsert_raw_document"):
        stats = load_mineru_output(tmp_path, "诊断学 第10版.pdf")

    # source_id 应 = compute_source_id("诊断学 第10版.pdf")
    assert stats["source_id"] == compute_source_id("诊断学 第10版.pdf")
    assert stats["file_name"] == "诊断学 第10版.pdf"


def test_stats_dict_has_all_required_keys(tmp_path: Path) -> None:
    """stats 字段齐全,供未来 Prometheus 埋点 / kb_change_log 直接取用。"""
    content_list = [[{"type": "paragraph",
                      "content": {"paragraph_content": [{"type": "text", "content": "x"}]}}]]
    _write_mineru_fixture(tmp_path, "x", "# t", content_list)

    from src.rag.ingestion.mineru_loader import load_mineru_output

    with patch("src.rag.ingestion.mineru_loader.upsert_source"), \
         patch("src.rag.ingestion.mineru_loader.upsert_raw_document"):
        stats = load_mineru_output(tmp_path, "诊断学.pdf")

    assert set(stats) == {
        "source_id", "file_name",
        "markdown_size_bytes", "markdown_size_after_clean_bytes",
        "content_list_pages", "content_list_blocks_total",
        "image_blocks_content_dropped", "markdown_unclean_snippets",
        "duration_seconds",
    }
    assert stats["content_list_pages"] == 1
    assert stats["content_list_blocks_total"] == 1


def test_upsert_source_called_before_raw_document(tmp_path: Path) -> None:
    """sources 必须先 upsert(FK 依赖),否则 raw_documents 会报 FK violation。"""
    _write_mineru_fixture(tmp_path, "x", "# t", [])

    from src.rag.ingestion.mineru_loader import load_mineru_output

    with patch("src.rag.ingestion.mineru_loader.upsert_source") as mock_src, \
         patch("src.rag.ingestion.mineru_loader.upsert_raw_document") as mock_raw:
        # 用 Mock 的 mock_calls 追踪调用顺序
        manager_calls: list[str] = []
        mock_src.side_effect = lambda **_: manager_calls.append("source")
        mock_raw.side_effect = lambda **_: manager_calls.append("raw")
        load_mineru_output(tmp_path, "诊断学.pdf")

    assert manager_calls == ["source", "raw"]
