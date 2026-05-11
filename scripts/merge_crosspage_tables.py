"""scripts/merge_crosspage_tables.py — table 跨页 chunk 去重 + 选择性合并新内容。

# 问题模式

mineru 把跨多页的医学表按页切成多个 chunk_kind=table 块。后续 chunk 之间的关系
有两种(2026-05 实测):

A. **冗余转录**(占比 ~97%):mineru 把整张跨页表识别到第 1 个 chunk(anchor),
   第 2/3 个 chunk(sibling)是它把第 2/3 页那部分内容**又切了一次**输出的副本,
   行内容跟 anchor 末尾大段重复,无新信息。

B. **真分页延续**(占比 ~3%):mineru 把跨页表正确分两段,sibling 包含 anchor
   没有的新行(典型:学_第9版 p910 中毒物速查表 = 有机溶剂/刺激性气体,
   p911 续 = 杀鼠剂/除草剂/中西药物)。

# 处理策略

识别条件(共用):
- 同 heading_path
- 紧邻页(page_diff ≤ 2)
- sibling caption 必须为空(有 caption 的多半是独立表)
- sibling 第一行 cells == anchor 第一行 cells(空白归一化后)

按 sibling 数据行(跳过表头)能否在 anchor 中找到分流(用 strong norm:HTML entity
+ LaTeX wrapping + 全部空白归一化):

| sibling 新增有效行 | 处理 |
|---|---|
| 0 (完全冗余) | sibling 标 merge_role=duplicate skip。anchor 不动。 |
| ≥1 (真分页延续) | sibling 标 merge_role=duplicate skip。anchor 加 merged_html_extension 字段(只含新增行),table 脚本跑 LLM 时拼到 anchor.content 后。|

无论分流到哪条,sibling 都跳过 LLM,信息保真由 anchor + (可选) extension 承担。

# 字段约定

- 命中的 duplicate:`merge_role="duplicate"`,`merge_group_id=f"p{anchor_page}-b{anchor_block}"`
- anchor:保持 `merge_role="standalone"`;有新增行时新增 `merged_html_extension` 字段(纯 HTML,
  仅含新增数据行,不带表头不带 wrapper `<table>`)。
- 其他 standalone 表:不动。

# 跨脚本边界

严格只动 chunk_kind=table 的记录。chart/figure 的 anchor/sibling 是
merge_multipanel_figures.py 维护的"同页多面板合并",语义独立,绝对不碰。

# 幂等

每次运行先 reset(把 prior 写入的 anchor/sibling 标记清回 standalone + 删
merged_html_extension 等冗余字段),然后重新识别 + 标 duplicate + 算 extension。
可放心反复跑。

# 用法

    python scripts/merge_crosspage_tables.py            # dry-run(默认)
    python scripts/merge_crosspage_tables.py --apply    # 写回 manifest
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any

INPUT_DIR = Path(__file__).resolve().parent / "figure_extract_output"

WS_RE = re.compile(r"\s+")
TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
LATEX_DOLLAR_RE = re.compile(r"\$([^$]*)\$")


# ─────────────────────────────────────────────────────────────────────
# 文本归一化:轻 norm 用于表头匹配,strong norm 用于内容重叠判断
# ─────────────────────────────────────────────────────────────────────


def cap_str(rec: dict) -> str:
    c = rec.get("caption") or []
    return "  ".join(c).strip() if isinstance(c, list) else str(c).strip()


def norm_light(text: str) -> str:
    """轻归一化:去 html 标签 + 折叠空白。用于表头字面匹配(保留 LaTeX 区分)。"""
    s = TAG_RE.sub("", text)
    return WS_RE.sub(" ", s).strip()


def norm_strong(text: str) -> str:
    """强归一化:去 html 标签 + 解 HTML entity + 去 LaTeX `$` 包裹 + 去全部空白。

    解决 mineru 在两个 chunk 中给同一 cell 不同写法导致的假阳性"新增行":
    - HTML entity:`Ewing&#x27;肉瘤` → `Ewing'肉瘤`
    - LaTeX 包裹:`$\\times 10^{9}$` → `\\times 10^{9}`
    - 空格差异:`甲状腺 摄^{131}I率` → `甲状腺摄^{131}I率`
    """
    s = TAG_RE.sub("", text)
    s = html.unescape(s)
    s = LATEX_DOLLAR_RE.sub(r"\1", s)
    s = re.sub(r"\s+", "", s)
    return s


def first_row_light(html_text: str) -> tuple[str, ...]:
    """提取 html 第一个 <tr> 的 light-normalized cell 列表(用于表头匹配)。"""
    m = TR_RE.search(html_text or "")
    if not m:
        return ()
    return tuple(norm_light(c) for c in CELL_RE.findall(m.group(1)))


def all_rows_strong(html_text: str) -> list[tuple[str, ...]]:
    """所有 <tr> 的 strong-normalized cell 元组(用于内容重叠判断)。"""
    return [tuple(norm_strong(c) for c in CELL_RE.findall(r))
            for r in TR_RE.findall(html_text or "")]


# ─────────────────────────────────────────────────────────────────────
# 阶段 1:reset 旧标记
# ─────────────────────────────────────────────────────────────────────


def reset_prior_marks(records: list[dict]) -> tuple[int, int]:
    """把 prior 误写在 chunk_kind=table 上的 anchor/sibling/duplicate 标记清回 standalone
    + 删 merged_html_extension 等冗余字段。

    严格限定 chunk_kind=table:chart/figure 的 anchor/sibling 是 merge_multipanel_figures.py
    维护的多面板合并,绝对不能动。

    返回 (n_reset_role, n_reset_extension)。
    """
    n_role = n_ext = 0
    for r in records:
        if r.get("chunk_kind") != "table":
            continue
        if r.get("merge_role") in ("anchor", "sibling", "duplicate"):
            n_role += 1
            r["merge_role"] = "standalone"
            r["merge_group_id"] = None
        # 清掉历史遗留字段(几轮策略迭代留下的)
        for stale in ("merged_html_concatenated", "merged_footnote_concatenated"):
            r.pop(stale, None)
        if "merged_block_idxs" in r and r["merged_block_idxs"] is not None:
            r["merged_block_idxs"] = None
        if "merged_html_extension" in r:
            n_ext += 1
            r.pop("merged_html_extension", None)
    return n_role, n_ext


# ─────────────────────────────────────────────────────────────────────
# 阶段 2:detect — 找候选 sibling 对(按表头一致 + 紧邻页 + 空 cap)
# ─────────────────────────────────────────────────────────────────────


def detect_candidates(records: list[dict]) -> list[tuple[int, list[int]]]:
    """扫 manifest 找候选 (anchor_idx, [sibling_idx, ...])。

    返回的 sibling 顺序按 (page,block) 升序。anchor 是首次扫到的 candidate,
    可能不是 sibling 的"最近"anchor,后续在 resolve_anchor_for_dup 里修正。
    """
    table_indexes = sorted(
        (i for i, r in enumerate(records) if r.get("chunk_kind") == "table"),
        key=lambda i: (records[i]["page_idx"], records[i]["block_idx"])
    )

    used = set()
    candidates: list[tuple[int, list[int]]] = []

    for pos, idx in enumerate(table_indexes):
        if idx in used:
            continue
        anchor = records[idx]
        if not anchor.get("heading_path"):
            continue
        anchor_first = first_row_light(anchor.get("content", ""))
        if not anchor_first:
            continue

        anchor_p = anchor["page_idx"]
        anchor_head = anchor["heading_path"]

        sibs: list[int] = []
        for next_pos in range(pos + 1, len(table_indexes)):
            cand_idx = table_indexes[next_pos]
            if cand_idx in used:
                continue
            cand = records[cand_idx]
            cand_p = cand["page_idx"]
            # 距离限制:首个 sibling 距 anchor ≤ 2 页;后续 sibling 距上一 sibling ≤ 2 页
            if cand_p - anchor_p > 2:
                if not sibs or cand_p - records[sibs[-1]]["page_idx"] > 2:
                    break
            if cand.get("heading_path") != anchor_head:
                continue
            if cap_str(cand):
                continue  # sibling 必须空 cap
            if first_row_light(cand.get("content", "")) != anchor_first:
                continue
            sibs.append(cand_idx)
            used.add(cand_idx)

        if sibs:
            used.add(idx)
            candidates.append((idx, sibs))

    return candidates


# ─────────────────────────────────────────────────────────────────────
# 阶段 3:per-sibling 算真 anchor + 算新增行
# ─────────────────────────────────────────────────────────────────────


def resolve_anchor_for_dup(records: list[dict], dup: dict,
                            table_indexes: list[int], fallback_idx: int,
                            dup_idx_set: set[int]) -> int:
    """每个 duplicate 单独算它的真 anchor:同 (head, first_row) cluster 里
    "在 duplicate 之前 (page,block) 最大的 *非 dup* 记录"(也就是紧挨上一张同表头的真 anchor)。

    **关键**:cluster 必须排除已被 detect 标为 sibling 的 idx,否则会出现"穿透失败" —
    例:学_第9版 p909#6 anchor + p910#0 sib + p911#0 sib 三人 cluster 同 first_row,
    p911#0 不能挂 p910#0(自己也是 dup),应该穿透到 p909#6。

    解决场景:
    - 同节多张表头一致(神经内科 p410#6 GCS + p410#7 Pittsburgh,都 ['检查项目/临床症状/评分']):
      sib p411#0 应挂 p410#7
    - 同节多次跨页续接 + 中间页有新独立表(内分泌 p257 → p258 → p259#3 → p260):
      p260 应挂 p259#3,而不是 p257#7
    - 单 anchor 多 sib(学_第9版 p909→p910→p911):p911 应穿透到 p909#6

    cluster 为空时回退到 fallback_idx(理论不会触发,因为 fallback 自己就在 cluster 里)。
    """
    head = dup.get("heading_path")
    first_row = first_row_light(dup.get("content", ""))
    dup_pos = (dup["page_idx"], dup["block_idx"])
    cluster = [
        i for i in table_indexes
        if i not in dup_idx_set                                        # 排除其他 sib
        and records[i].get("heading_path") == head
        and first_row_light(records[i].get("content", "")) == first_row
        and (records[i]["page_idx"], records[i]["block_idx"]) < dup_pos
    ]
    if not cluster:
        return fallback_idx
    return max(cluster, key=lambda i: (records[i]["page_idx"], records[i]["block_idx"]))


def compute_extension_html(anchor: dict, dup: dict) -> str | None:
    """算 sibling 中 anchor 没有的"新增数据行"对应的原始 html 片段。

    只用 strong-normalized 行做集合比较找新增,但拼接时用原始 html 保留数据保真度
    (LaTeX 公式、HTML entity 等都不破坏)。

    返回 None 表示 sibling 完全冗余(0 新增行);返回拼接 html 字符串表示有新增。
    """
    a_rows_set = set(all_rows_strong(anchor.get("content", "")))
    raw_rows = TR_RE.findall(dup.get("content", "") or "")
    if not raw_rows:
        return None
    new_html_chunks: list[str] = []
    for raw_tr in raw_rows[1:]:  # skip 表头(已知与 anchor 一致)
        cells = CELL_RE.findall(raw_tr)
        norm_row = tuple(norm_strong(c) for c in cells)
        # 跳过纯空行(全 cell strip 后无内容)
        if not any(norm_row):
            continue
        if norm_row in a_rows_set:
            continue  # 冗余行,跳过
        # 新增行:保留原始 <tr>...</tr>(从 dup html 中提取完整片段)
        new_html_chunks.append(f"<tr>{raw_tr}</tr>")
    if not new_html_chunks:
        return None
    return "\n".join(new_html_chunks)


# ─────────────────────────────────────────────────────────────────────
# 一本书的处理(reset → detect → per-dup resolve+extension → apply)
# ─────────────────────────────────────────────────────────────────────


def process_book(jsonl_path: Path, apply: bool) -> dict[str, Any]:
    records: list[dict] = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            records.append(json.loads(line))

    n_reset_role, n_reset_ext = reset_prior_marks(records)
    candidates = detect_candidates(records)
    table_indexes = [i for i, r in enumerate(records) if r.get("chunk_kind") == "table"]

    # 先把所有会被标 dup 的 idx 收集起来 — resolve 时 cluster 要排除它们
    # 否则 sib 会挂到另一个 sib 上而不是穿透到真 anchor
    all_dup_idxs = {sib_idx for _, sib_idxs in candidates for sib_idx in sib_idxs}

    # 每个 (anchor_idx, [sib_idx]) → 解析每个 sib 的真 anchor + 算 extension
    # 输出结构:[{anchor_idx, anchor, dup_idx, dup, extension_html}]
    plan: list[dict] = []
    for fallback_anchor_idx, sib_idxs in candidates:
        for sib_idx in sib_idxs:
            sib = records[sib_idx]
            true_anchor_idx = resolve_anchor_for_dup(
                records, sib, table_indexes, fallback_anchor_idx, all_dup_idxs
            )
            true_anchor = records[true_anchor_idx]
            ext_html = compute_extension_html(true_anchor, sib)
            plan.append({
                "anchor_idx": true_anchor_idx, "anchor": true_anchor,
                "dup_idx": sib_idx, "dup": sib,
                "extension_html": ext_html,
            })

    if apply:
        # 标 sibling 为 duplicate
        for entry in plan:
            dup = entry["dup"]
            anchor = entry["anchor"]
            dup["merge_role"] = "duplicate"
            dup["merge_group_id"] = f"p{anchor['page_idx']}-b{anchor['block_idx']}"
        # 给 anchor 累积 extension(同一 anchor 可能被多个 sib 续接)
        ext_by_anchor_idx: dict[int, list[str]] = {}
        for entry in plan:
            if entry["extension_html"]:
                ext_by_anchor_idx.setdefault(entry["anchor_idx"], []).append(entry["extension_html"])
        for ai, exts in ext_by_anchor_idx.items():
            records[ai]["merged_html_extension"] = "\n".join(exts)

        with jsonl_path.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    return {
        "plan": plan,
        "n_reset_role": n_reset_role,
        "n_reset_ext": n_reset_ext,
        "n_dups": len(plan),
        "n_anchors_with_ext": len({e["anchor_idx"] for e in plan if e["extension_html"]}),
        "n_dups_with_new": sum(1 for e in plan if e["extension_html"]),
    }


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--apply", action="store_true", help="实际写回(默认 dry-run 只打印)")
    args = ap.parse_args()

    grand = {"books": 0, "dups": 0, "dups_with_new": 0,
             "anchors_with_ext": 0, "reset_role": 0, "reset_ext": 0}

    for p in sorted(INPUT_DIR.glob("poc_chunking_*.jsonl")):
        result = process_book(p, args.apply)
        if not result["plan"] and result["n_reset_role"] == 0 and result["n_reset_ext"] == 0:
            continue
        tag = "已写回" if args.apply else "dry-run"
        print(f"=== {p.stem} ({result['n_dups']} dup / {result['n_dups_with_new']} 带新内容 {tag}"
              f"  reset: {result['n_reset_role']} role + {result['n_reset_ext']} ext) ===")
        for entry in result["plan"]:
            d = entry["dup"]; a = entry["anchor"]
            ext = entry["extension_html"]
            ext_tag = f"+ext({len(ext)} char)" if ext else ""
            print(f"  dup p{d['page_idx']}#{d['block_idx']} → anchor p{a['page_idx']}#{a['block_idx']}  "
                  f"cap={cap_str(a)[:50]}  {ext_tag}")
        grand["books"] += 1
        grand["dups"] += result["n_dups"]
        grand["dups_with_new"] += result["n_dups_with_new"]
        grand["anchors_with_ext"] += result["n_anchors_with_ext"]
        grand["reset_role"] += result["n_reset_role"]
        grand["reset_ext"] += result["n_reset_ext"]

    if grand["dups"] == 0 and grand["reset_role"] == 0 and grand["reset_ext"] == 0:
        print("无变更")
        return

    title = "已写回" if args.apply else "dry-run,加 --apply 实际写入"
    print(f"\n汇总({title}):")
    print(f"  reset 旧标记:{grand['reset_role']} role + {grand['reset_ext']} extension")
    print(f"  标 duplicate:{grand['dups']} 条 sibling(skip LLM)")
    print(f"  其中 anchor 加 extension:{grand['dups_with_new']} 条 sibling 有新内容,"
          f"覆盖 {grand['anchors_with_ext']} 个 anchor 拿合并 html")


if __name__ == "__main__":
    main()
