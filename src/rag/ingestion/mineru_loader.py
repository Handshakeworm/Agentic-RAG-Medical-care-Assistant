"""mineru_loader — MinerU 解析产物加载器(DEV_SPEC §3.1.1 / §3.1.4 / C1)。

读取 mineru 输出目录下的 4 个产物文件,清洗 image 块的 VLM 幻觉文本,
upsert sources + raw_documents,返回灌入统计。

清洗范围(只 2 件事,详见 §3.1.1 末"限制 1"):
- 删 `content_list_v2` 里 image 块的 `content` 字段(VLM 50% 幻觉)
- 用上一步收集的指纹,从 markdown 同样删掉对应文本

不清洗 / 不动:
- middle_data / model_data:不进 chunking 主链路,即使含残余 VLM 文本无害
- image_caption / image_footnote / image_source.path / bbox:出版社权威元数据,保留
- markdown 里的 `![](images/xxx.jpg)` 占位符:保留位置信号
- page_header / page_footer / page_number / list / chart / table:全保留,过滤是 C2 的事
- title.level 全 1:C2 chunking **完全不读此字段**(改用目录权威清单,见 §3.1.2),loader 不动
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from src.db.postgres.models import upsert_raw_document, upsert_source
from src.rag.ingestion.idempotency import compute_source_id

logger = logging.getLogger(__name__)

# 短指纹门槛:< 该长度的 image content 跳过 markdown 清洗,防止误删合法短文本
# (例:出版社写的"图1-1"这种短字符串可能在多处合法出现)
_MIN_SNIPPET_LEN = 20


def _locate_files(mineru_dir: Path) -> dict[str, Path]:
    """定位 mineru 输出目录下的 4 个必需文件。

    mineru 命名约定:`{stem}.md` / `{stem}_content_list_v2.json` /
    `{stem}_middle.json` / `{stem}_model.json`。所有 4 个文件 stem 相同。
    """
    if not mineru_dir.is_dir():
        raise FileNotFoundError(f"mineru 目录不存在: {mineru_dir}")

    md_files = sorted(mineru_dir.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"{mineru_dir} 下未找到 .md 文件")
    md_path = md_files[0]
    stem = md_path.stem

    paths = {
        "markdown": md_path,
        "content_list": mineru_dir / f"{stem}_content_list_v2.json",
        "middle": mineru_dir / f"{stem}_middle.json",
        "model": mineru_dir / f"{stem}_model.json",
    }
    for label, p in paths.items():
        if not p.is_file():
            raise FileNotFoundError(f"mineru 产物缺失 [{label}]: {p}")
    return paths


def _read_json(path: Path) -> Any:
    """读 JSON 文件,失败时附带文件路径以便定位。"""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败 [{path}]: {e}") from e


def _validate_content_list_shape(content_list: Any, path: Path) -> None:
    """spec §2.4.4.1:content_list_v2 顶层必须是 list[页 list[block dict]]。"""
    if not isinstance(content_list, list):
        raise ValueError(f"{path} 顶层不是 list,实际 {type(content_list).__name__}")
    if content_list and not isinstance(content_list[0], list):
        # 允许空文档(0 页),但首页必须是 list
        raise ValueError(f"{path} 顶层 list[0] 不是 list(可能是 v1 扁平格式),"
                         f"实际 {type(content_list[0]).__name__}")


def _strip_image_hallucinations(content_list: list) -> list[str]:
    """边遍历 content_list,边删 image 块 content 字段,边收集幻觉文本作指纹。

    spec §3.1.1 末限制 1:image 块约 50% 含 VLM 幻觉,必丢。
    table / chart / equation_interline 等其他 type 的 content 字段必须保留。

    返回值:被删除的 image content 文本列表(供 markdown 清洗用作指纹)。
    """
    snippets: list[str] = []
    for page in content_list:
        for blk in page:
            if blk.get("type") != "image":
                continue
            content_dict = blk.get("content", {})
            if not isinstance(content_dict, dict):
                continue
            text = content_dict.pop("content", None)
            if isinstance(text, str) and text:
                snippets.append(text)
    return snippets


def _clean_markdown(markdown: str, hallucination_snippets: list[str]) -> tuple[str, list[str]]:
    """用 image content 指纹从 markdown 中精确 substring 删除。

    返回 (cleaned_markdown, unclean_snippets)
    unclean_snippets:清洗后仍能在 markdown 中找到的指纹片段(用于 warning)。
    短指纹(< _MIN_SNIPPET_LEN)跳过 markdown 清洗以避免误删合法短文本,
    但 v2 中已删,无 BM25 污染风险。
    """
    cleaned = markdown
    for snippet in hallucination_snippets:
        if len(snippet) < _MIN_SNIPPET_LEN:
            continue
        cleaned = cleaned.replace(snippet, "")

    unclean = [
        s[:50] for s in hallucination_snippets
        if len(s) >= _MIN_SNIPPET_LEN and s in cleaned
    ]
    return cleaned, unclean


def load_mineru_output(mineru_dir: Path | str, original_pdf_name: str) -> dict:
    """C1 主入口:读 mineru 输出 → 清洗 → upsert sources + raw_documents → 返回 stats。

    参数:
    - mineru_dir:mineru 输出目录(`mineru_output/{name}/{backend}_auto/`)
    - original_pdf_name:**原始 PDF 文件名**(如 "诊断学 第10版.pdf"),
      用于 source_id 计算(spec §3.1.4.1)。调用方负责传对(Q1-A 方案)。

    返回 stats dict:
    {
        "source_id": "...",
        "file_name": "诊断学 第10版.pdf",
        "markdown_size_bytes": 988_487,
        "markdown_size_after_clean_bytes": 935_120,
        "content_list_pages": 626,
        "content_list_blocks_total": 10833,
        "image_blocks_content_dropped": 532,
        "markdown_unclean_snippets": [],   # 应为空;非空表示有指纹未在 md 中清干净
        "duration_seconds": 1.234,
    }
    """
    t0 = time.time()
    mineru_dir = Path(mineru_dir)

    # 1. 定位 + 读 4 文件
    paths = _locate_files(mineru_dir)
    markdown = paths["markdown"].read_text(encoding="utf-8")
    content_list = _read_json(paths["content_list"])
    middle_data = _read_json(paths["middle"])
    model_data = _read_json(paths["model"])
    markdown_size_orig = len(markdown.encode("utf-8"))

    # 2. 校验 content_list 形状
    _validate_content_list_shape(content_list, paths["content_list"])

    # 3. 双清洗:v2 image content 删除 + markdown 同步清洗
    snippets = _strip_image_hallucinations(content_list)
    markdown_clean, unclean = _clean_markdown(markdown, snippets)
    if unclean:
        logger.warning(
            f"[mineru_loader] {len(unclean)} 个 image content 指纹未在 markdown 清干净 "
            f"(原 PDF={original_pdf_name});前 3: {unclean[:3]}"
        )

    # 4. 算 source_id(spec §3.1.4.1)
    source_id = compute_source_id(original_pdf_name)

    # 5. 先 upsert sources(raw_documents 有 FK 依赖)
    upsert_source(
        source_id=source_id,
        file_name=original_pdf_name,
        file_path=str(mineru_dir),
        doc_type="textbook",
    )

    # 6. upsert raw_documents
    upsert_raw_document(
        source_id=source_id,
        file_name=original_pdf_name,
        markdown_content=markdown_clean,
        content_list=content_list,
        middle_data=middle_data,
        model_data=model_data,
        pdf_path=str(mineru_dir),
    )

    return {
        "source_id": source_id,
        "file_name": original_pdf_name,
        "markdown_size_bytes": markdown_size_orig,
        "markdown_size_after_clean_bytes": len(markdown_clean.encode("utf-8")),
        "content_list_pages": len(content_list),
        "content_list_blocks_total": sum(len(p) for p in content_list),
        "image_blocks_content_dropped": len(snippets),
        "markdown_unclean_snippets": unclean,
        "duration_seconds": round(time.time() - t0, 3),
    }
