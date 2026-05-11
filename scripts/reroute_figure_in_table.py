"""scripts/reroute_figure_in_table.py — 把 manifest 中"caption 写'图 N-X'但 chunk_kind=table"
的记录的 chunk_kind 从 'table' 改为 'figure',加 original_chunk_kind='table' 备查。

# 背景

mineru 把流程图 / 解剖示意图 / 分型示意图 / 评分系统等错切成了 html `<table>`,
caption 字段保留了真实的"图 N-X"。这类记录(实测全集 16 条):
- html 内容是 grid 残骸(大量空 td、单字母标签),text LLM 无法解读
- 但全部 image_exists=True,vision LLM 看截图能直接识别

重路由后,table_enrichment_generation.py 的 defensive guard(chunk_kind != 'table')
会自动跳过它们,figure_enrichment_generation.py 会自动捡起来跑 vision 路。

# 幂等

已带 original_chunk_kind 字段的记录不再修改,可重复运行。

# 用法

    python scripts/reroute_figure_in_table.py            # dry-run(默认)
    python scripts/reroute_figure_in_table.py --apply    # 实际写回
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

INPUT_DIR = Path(__file__).resolve().parent / "figure_extract_output"

# 中文"图"前不能紧跟其他汉字(避免命中"插图"、"图谱"等正文词)
CAP_FIG_RE = re.compile(r"(?<![一-龥])图\s*\d")
CAP_TBL_RE = re.compile(r"表\s*\d")


def cap_str(rec: dict) -> str:
    c = rec.get("caption") or []
    return "  ".join(c).strip() if isinstance(c, list) else str(c).strip()


def is_figure_misclassified(rec: dict) -> bool:
    if rec.get("chunk_kind") != "table":
        return False
    if rec.get("original_chunk_kind"):  # 已处理过,跳过
        return False
    cs = cap_str(rec)
    if not cs:
        return False
    return bool(CAP_FIG_RE.search(cs) and not CAP_TBL_RE.search(cs))


def process_book(jsonl_path: Path, apply: bool) -> tuple[int, list[str]]:
    records = []
    samples: list[str] = []
    n_flip = 0
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            r = json.loads(line)
            if is_figure_misclassified(r):
                samples.append(
                    f"  p{r.get('page_idx')}#{r.get('block_idx')}  "
                    f"sub_type={r.get('mineru_sub_type')}  "
                    f"img_exists={r.get('image_exists')}  "
                    f"caption: {cap_str(r)[:100]}"
                )
                if apply:
                    r["original_chunk_kind"] = "table"
                    r["chunk_kind"] = "figure"
                n_flip += 1
            records.append(r)

    if n_flip and apply:
        # in-place rewrite
        with jsonl_path.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    return n_flip, samples


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--apply", action="store_true", help="实际写回(默认 dry-run 只打印)")
    args = ap.parse_args()

    total = 0
    for p in sorted(INPUT_DIR.glob("poc_chunking_*.jsonl")):
        n, samples = process_book(p, args.apply)
        if n:
            tag = "已改" if args.apply else "dry-run"
            print(f"=== {p.stem} ({n} 条 {tag}) ===")
            for s in samples:
                print(s)
            total += n

    if total == 0:
        print("无需重路由(可能已全部处理过)")
        return

    if args.apply:
        print(f"\n汇总:{total} 条已写回 manifest(chunk_kind: table → figure,加 original_chunk_kind=table)")
    else:
        print(f"\n汇总:{total} 条候选,加 --apply 实际写入")


if __name__ == "__main__":
    main()
