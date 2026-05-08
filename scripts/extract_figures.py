"""scripts/extract_figures.py — 从 mineru 产物拎出图表 block 落 manifest jsonl。

按"图表等处理方式"决策(2026-05-03):只保留 3 类作 chunk
- `table`(全部:simple_table + complex_table)
- `chart`(全部:line / bar / scatter / ...)
- `image` 且 `sub_type == 'flowchart'`

其它一律丢:image 的 chemical / natural_image / text_image / None,以及
equation_interline。mineru 原始产物文件不动,只在 chunking/extract 内存过滤。

每本书一个 jsonl 落到 `scripts/figure_extract_output/<book_dir>.jsonl`,
后续 summary 生成 / 入库步骤直接读这份 manifest。

# 数据源策略(2026-05-08 验证后修订)

mineru 在 `*_content_list_v2.json`(v2)里**会漏写**部分 table/chart/figure 的结构化字段
(html / markdown / mermaid)——但实际识别成功,完整数据在 `*_middle.json` 里。

实测全 12 本:
- table:139 / 2996 v2 缺 html → 100% 可从 middle.json 救回
- chart:19 / 320 v2 缺 markdown → 47% 可从 middle.json 救回
- figure:1 / 726 v2 缺 mermaid → 0% 可从 middle.json 救回(但截图都在,vision LLM 兜底)

匹配方法:逐页保留类(table/chart/figure)的顺序在 v2 与 middle.preproc_blocks 里 100%
一致(12130 页验证 0 不一致),所以用 `(page_idx, chunk_kind, 该页同 kind 第几个)` 三元组
精确对应,**比 bbox 严格匹配更稳**(v2/middle 的 bbox 是不同坐标系,数值对不上)。

# 封面/版权页错判

mineru 偶尔把封面/版权/目录页的整页版面错判成一张"大 table"(整页 bbox + 同页无文字
paragraph)。这种 block 即便从 middle 救回 html,内容也是版面噪声,不入 manifest。
全 12 本扫出 16 条(都是 table 类),提取阶段直接丢。

# 每行 manifest schema

    {
      "book_dir":         "poc_chunking_内分泌代谢病学_第4版上册",
      "page_idx":         5,
      "block_idx":        0,                   # 块在该页内的位置序号
      "chunk_kind":       "table" | "chart" | "figure",
      "kind_ordinal":     0,                   # 该页同 kind 第几个(从 0)
      "mineru_type":      "table" | "chart" | "image",
      "mineru_sub_type":  "simple_table" | "complex_table" | "flowchart" | "line" | ...,
      "bbox":             [x0, y0, x1, y1],    # v2 坐标系
      "content":          "<table>...</table>" | "| col | ... |" | "```mermaid\\n...\\n```",
      "content_source":   "v2" | "middle.json",   # 结构化文本来自哪
      "content_empty":    true | false,            # 两个源都没救出 → 下游需走 vision LLM
      "caption":          ["..."],                 # list[str],可空
      "footnote":         ["..."],                 # list[str],可空
      "image_path":       "images/<sha>.jpg",      # mineru 相对路径(若有)
      "image_abs_path":   "/data/medical-resources/.../images/<sha>.jpg",
      "image_exists":     true | false,            # 用 is_file() 判断,目录不算
    }

注意:`chunk_kind == "figure"` 是我们之后落 PG 时 `chunk_type='figure' / 'figure_summary'` 用,
不要跟 mineru 的 `image` 混。table 的 `sub_type` 在 mineru 顶层是 None,实际写在
`content.table_type` 里,这里统一搬到顶层 `mineru_sub_type`。

用法:
    python scripts/extract_figures.py poc_chunking_内分泌代谢病学_第4版上册
    python scripts/extract_figures.py --all
    python scripts/extract_figures.py --all --report   # 只统计,不落盘
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from load_chunks_to_pg import BOOK_TO_FILENAME  # noqa: E402

MINERU_ROOT = Path("/data/medical-resources/mineru-output")
OUTPUT_DIR = REPO_ROOT / "scripts" / "figure_extract_output"


# ─────────────────────────────────────────────────────────────────────
# middle.json fallback 索引
# ─────────────────────────────────────────────────────────────────────


def _kind_of(blk: dict[str, Any]) -> str | None:
    """v2 / middle 共用的保留类判定。"""
    t = blk.get("type")
    sub = blk.get("sub_type")
    if t == "table":
        return "table"
    if t == "chart":
        return "chart"
    if t == "image" and sub == "flowchart":
        return "figure"
    return None


def _build_middle_index(book_dir: str) -> dict[tuple[int, str, int], dict[str, Any]]:
    """{(page_idx, kind, kind_ordinal): middle_block} 索引。

    middle.json 顶层 `pdf_info[i].preproc_blocks` 第一层 block 的保留类顺序与 v2 完全一致
    (12130 页校验 0 不一致),所以按 (page, kind, 序号) 三元组就能精确对应。
    """
    fname = BOOK_TO_FILENAME[book_dir]
    book_name = fname[:-4] if fname.endswith(".pdf") else fname
    p = MINERU_ROOT / book_name / "hybrid_auto" / f"{book_name}_middle.json"
    if not p.exists():
        return {}
    middle = json.loads(p.read_text(encoding="utf-8"))["pdf_info"]
    idx: dict[tuple[int, str, int], dict[str, Any]] = {}
    for pi, page in enumerate(middle):
        ord_ct = {"table": 0, "chart": 0, "figure": 0}
        for blk in page.get("preproc_blocks", []):
            kind = _kind_of(blk)
            if kind is None:
                continue
            idx[(pi, kind, ord_ct[kind])] = blk
            ord_ct[kind] += 1
    return idx


def _recover_content_from_middle(middle_blk: dict[str, Any], kind: str) -> str:
    """递归扫 middle block 内任意嵌套的 string,按 kind 匹配结构化文本特征。

    table:看 `<table` / `<td>`(html);
    chart:看 markdown 数据表(含 `|` 分隔且换行,且至少 4 个 `|`);
    figure:看 mermaid(`mermaid` 关键字 / `graph TD` / `graph LR`)。

    找不到匹配 → 返回空字符串。
    """
    cands: list[str] = []

    def _walk(o: Any) -> None:
        if isinstance(o, dict):
            for v in o.values():
                _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)
        elif isinstance(o, str) and o.strip():
            cands.append(o)

    _walk(middle_blk)

    if kind == "table":
        for s in cands:
            if "<table" in s or "<td>" in s:
                return s
    elif kind == "chart":
        for s in cands:
            if "|" in s and s.count("|") >= 4 and "\n" in s:
                return s
    elif kind == "figure":
        for s in cands:
            if "mermaid" in s.lower() or "graph TD" in s or "graph LR" in s:
                return s
    return ""


# ─────────────────────────────────────────────────────────────────────
# 单 block 抽取
# ─────────────────────────────────────────────────────────────────────


def _captions(items: list[dict[str, Any]] | None) -> list[str]:
    if not items:
        return []
    out: list[str] = []
    for it in items:
        c = it.get("content") if isinstance(it, dict) else None
        if isinstance(c, str) and c.strip():
            out.append(c.strip())
    return out


def _is_cover_misjudge(blk: dict[str, Any], page_blocks: list[dict[str, Any]]) -> bool:
    """mineru 把封面/版权/目录页错判成大 table 的特征(必须三条都满足):
    1. v2 的 html 是空(mineru 没真解析出表格结构)
    2. 整页 bbox(w>800, h>700)
    3. 同页一个 paragraph block 都没有

    缺第 1 条会误丢"整页 bbox 但 html 完整"的有效大表(药品索引表、检验参考值大表等)。
    只对 table 类做判断,chart/figure 不会出现这种情况(实测 cover 错判全是 table)。
    """
    if blk.get("type") != "table":
        return False
    html = ((blk.get("content") or {}).get("html")) or ""
    if html.strip():
        return False
    bbox = blk.get("bbox") or [0, 0, 0, 0]
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if not (w > 800 and h > 700):
        return False
    has_para = any(
        b.get("type") == "paragraph"
        and ((b.get("content") or {}).get("paragraph_content"))
        for b in page_blocks
    )
    return not has_para


def _extract_one(
    blk: dict[str, Any],
    page_blocks: list[dict[str, Any]],
    page_idx: int,
    block_idx: int,
    kind_ordinal: int,
    book_dir: str,
    images_dir: Path,
    middle_idx: dict[tuple[int, str, int], dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    """命中保留规则 → 返回 (manifest_record, status_tag)。
    status_tag 用于汇总报告归类(kept / dropped_cover / recovered / empty)。"""
    kind = _kind_of(blk)
    if kind is None:
        return None, "skip"
    if _is_cover_misjudge(blk, page_blocks):
        return None, "dropped_cover"

    content_obj = blk.get("content") or {}

    if kind == "table":
        mineru_sub = content_obj.get("table_type")
        body = content_obj.get("html") or ""
        cap = _captions(content_obj.get("table_caption"))
        ft = _captions(content_obj.get("table_footnote"))
    elif kind == "chart":
        mineru_sub = blk.get("sub_type")
        body = content_obj.get("content") or ""
        cap = _captions(content_obj.get("chart_caption"))
        ft = _captions(content_obj.get("chart_footnote"))
    else:  # figure
        mineru_sub = "flowchart"
        body = content_obj.get("content") or ""
        cap = _captions(content_obj.get("image_caption"))
        ft = _captions(content_obj.get("image_footnote"))

    content_source = "v2"
    content_empty = False
    if not body.strip():
        mb = middle_idx.get((page_idx, kind, kind_ordinal))
        if mb is not None:
            recovered = _recover_content_from_middle(mb, kind)
            if recovered.strip():
                body = recovered
                content_source = "middle.json"
        if not body.strip():
            content_empty = True

    img_rel = (content_obj.get("image_source") or {}).get("path") or ""
    # mineru 解析失败时会写 'images/'(空文件名只剩斜杠),这种不视作有效路径
    img_abs = images_dir.parent / img_rel if img_rel else None
    img_exists = bool(img_abs and img_abs.is_file())
    if not img_exists:
        img_rel = ""
        img_abs = None

    rec = {
        "book_dir": book_dir,
        "page_idx": page_idx,
        "block_idx": block_idx,
        "chunk_kind": kind,
        "kind_ordinal": kind_ordinal,
        "mineru_type": blk.get("type"),
        "mineru_sub_type": mineru_sub,
        "bbox": blk.get("bbox"),
        "content": body,
        "content_source": content_source,
        "content_empty": content_empty,
        "caption": cap,
        "footnote": ft,
        "image_path": img_rel,
        "image_abs_path": str(img_abs) if img_abs else "",
        "image_exists": img_exists,
    }
    if content_empty:
        return rec, "empty"
    if content_source == "middle.json":
        return rec, "recovered"
    return rec, "kept"


# ─────────────────────────────────────────────────────────────────────
# 单本书提取
# ─────────────────────────────────────────────────────────────────────


def _v2_path(book_dir: str) -> tuple[Path, Path]:
    """返回 (content_list_v2.json, images_dir)。"""
    file_name = BOOK_TO_FILENAME[book_dir]
    book_name = file_name[:-4] if file_name.endswith(".pdf") else file_name
    base = MINERU_ROOT / book_name / "hybrid_auto"
    return base / f"{book_name}_content_list_v2.json", base / "images"


def extract_book(book_dir: str, report_only: bool = False) -> dict[str, int]:
    cl_path, images_dir = _v2_path(book_dir)
    if not cl_path.exists():
        print(f"[SKIP] {book_dir}: 找不到 {cl_path}")
        return {"book": book_dir, "table": 0, "chart": 0, "figure": 0,
                "recovered": 0, "empty": 0, "dropped_cover": 0, "missing_img": 0}

    pages: list[list[dict[str, Any]]] = json.loads(cl_path.read_text(encoding="utf-8"))
    middle_idx = _build_middle_index(book_dir)

    records: list[dict[str, Any]] = []
    counts = {
        "table": 0, "chart": 0, "figure": 0,
        "recovered": 0, "empty": 0, "dropped_cover": 0, "missing_img": 0,
    }
    for pi, page in enumerate(pages):
        ord_ct = {"table": 0, "chart": 0, "figure": 0}
        for bi, blk in enumerate(page):
            kind = _kind_of(blk)
            if kind is None:
                continue
            kind_ordinal = ord_ct[kind]
            ord_ct[kind] += 1
            rec, tag = _extract_one(
                blk, page, pi, bi, kind_ordinal,
                book_dir, images_dir, middle_idx,
            )
            if tag == "dropped_cover":
                counts["dropped_cover"] += 1
                continue
            if rec is None:
                continue
            counts[rec["chunk_kind"]] += 1
            if tag == "recovered":
                counts["recovered"] += 1
            elif tag == "empty":
                counts["empty"] += 1
            if not rec["image_exists"]:
                counts["missing_img"] += 1
            records.append(rec)

    total = counts["table"] + counts["chart"] + counts["figure"]
    print(
        f"[{book_dir}] table={counts['table']}  chart={counts['chart']}  "
        f"figure={counts['figure']}  total={total}  "
        f"recovered={counts['recovered']}  empty={counts['empty']}  "
        f"dropped_cover={counts['dropped_cover']}  missing_img={counts['missing_img']}"
    )

    if report_only:
        return {"book": book_dir, **counts, "total": total}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{book_dir}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  → {out_path}  ({total} 行)")
    return {"book": book_dir, **counts, "total": total}


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("book_dir", nargs="?", help="POC 目录名;省略需配 --all")
    p.add_argument("--all", action="store_true", help="顺序跑 BOOK_TO_FILENAME 全 12 本")
    p.add_argument("--report", action="store_true", help="只统计计数,不落盘")
    return p.parse_args()


def main():
    args = _parse_args()
    if args.all and args.book_dir:
        sys.exit("--all 与 book_dir 不能同时指定")
    if not args.all and not args.book_dir:
        sys.exit("用法:python scripts/extract_figures.py <book_dir> | --all  [--report]")

    targets = list(BOOK_TO_FILENAME.keys()) if args.all else [args.book_dir]
    grand: dict[str, int] = {
        "table": 0, "chart": 0, "figure": 0,
        "recovered": 0, "empty": 0, "dropped_cover": 0,
        "missing_img": 0, "total": 0,
    }
    for book in targets:
        if book not in BOOK_TO_FILENAME:
            print(f"[ERROR] 未知 book_dir: {book}")
            continue
        s = extract_book(book, report_only=args.report)
        for k in grand:
            grand[k] += s.get(k, 0)
    print(
        f"\n=== 汇总 books={len(targets)}  table={grand['table']}  "
        f"chart={grand['chart']}  figure={grand['figure']}  total={grand['total']}  "
        f"recovered={grand['recovered']}  empty={grand['empty']}  "
        f"dropped_cover={grand['dropped_cover']}  missing_img={grand['missing_img']} ==="
    )


if __name__ == "__main__":
    main()
