"""scripts/figure_enrichment_generation.py — 给 table/chart/figure 源块单步产出 *_summary chunk 入库 4 字段(disk-first 落 jsonl)。

按本(book_dir)读 `scripts/figure_extract_output/<book_dir>.jsonl` 的 figure manifest,
按 chunk_kind 分流调用不同 LLM,**单步产出 4 字段**(2026-05-08 决策从两阶段合一):

- **table**:文本 LLM(deepseek-v4-pro)看 html → 4 字段
- **chart / figure**:多模态 LLM(qwen3.5-plus)看截图(anchor 多图、standalone 单图)→ 4 字段

产出落 `scripts/figure_enrichment_output/<book_dir>.jsonl`,每条 record:
    {book_dir, page_idx, block_idx, chunk_kind, status,
     medical_statement, title, summary, hypothetical_questions,
     elapsed_s, [merge_group_id, n_images]}

medical_statement 直接对应下游 *_summary chunk 的 chunk_raw_text;title/summary/
hypothetical_questions 对应 ChunkEnrichmentOutput 的同名字段——一次产出可直接灌库。

下游 cosine 去重 + chunks 表灌库由独立任务接力,本脚本不负责。

# 设计要点(对齐 enrichment.py)

- **disk-first**:LLM 产物先落本地 jsonl,审核/重灌之间解耦;不直接写 PG
- **断点续传**:启动时读 jsonl 已有 (page_idx, block_idx) 集合(无论 ok/failed 都 skip),
  避免重复烧 token;`--retry-failed` 标志显式重跑失败条
- **失败跳过**(spec §9.3 低安全等级):2 次重试都失败 → 记 status="failed" 写 jsonl
- **并发**:asyncio + Semaphore(text/vision 各自一组,vision 更稳但慢,text 高并发)
- **配置就近**:
  - text(deepseek):复用 enrichment.py 的 DEEPSEEK_BASE_URL/DEEPSEEK_API_KEY
  - vision(qwen3.5-plus):走 LLM_BASE_URL(DashScope 兼容口) + LLM_API_KEY
- **孤儿丢弃**:manifest 中 heading_path=None 的(135 条 preface/ref-zone/视频资源页)直接 skip,
  不生成 summary,后续也不入库

用法:
    python scripts/figure_enrichment_generation.py poc_chunking_诊断学_第10版            # 单本
    python scripts/figure_enrichment_generation.py poc_chunking_诊断学_第10版 --limit 5  # 试跑前 5
    python scripts/figure_enrichment_generation.py --all                                  # 全 12 本
    python scripts/figure_enrichment_generation.py poc_chunking_诊断学_第10版 --retry-failed
    python scripts/figure_enrichment_generation.py poc_chunking_诊断学_第10版 --kind table  # 只跑 table
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

load_dotenv(REPO_ROOT / ".env")

from load_chunks_to_pg import BOOK_TO_FILENAME  # noqa: E402
from src.agent.schemas.ingestion import FigureSummaryEnrichmentOutput  # noqa: E402
from src.prompts.ingestion import (  # noqa: E402
    build_figure_summary_multimodal_messages,
    build_figure_summary_text_prompt,
)

# ─────────────────────────────────────────────────────────────────────
# 路径 / LLM 配置(就近收敛,one-off 离线任务,不进 settings.LLMSettings)
# ─────────────────────────────────────────────────────────────────────

INPUT_DIR = REPO_ROOT / "scripts" / "figure_extract_output"
OUTPUT_DIR = REPO_ROOT / "scripts" / "figure_enrichment_output"

# Text LLM(table → html → summary)— 复用 enrichment.py 同款 deepseek
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL_NAME = "deepseek-v4-pro"

# Vision LLM(chart/figure → 截图 → summary)— qwen3.5-plus 原生多模态
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_VISION_MODEL = "qwen3.5-plus"

LLM_TEMPERATURE = 0.3
# 单步 4 字段 JSON 输出 + 保留 thinking mode 的预算:
#   - 4 字段 JSON 纯输出:medical_statement(100-300 字)+ title(≤30)+ summary(≤250)
#     + 2-3 questions(各 30-80 字)+ JSON syntax 损耗 = ~700-1100 token
#   - thinking mode 推理:hard case 可能 500-2000 token(见 #22 骨科 figure 历史失败)
#   - 给足 4096 让模型不会被截断;实际平均消耗远低于此(浪费可忽略)
LLM_MAX_TOKENS = 4096
LLM_TIMEOUT = 120.0   # 视觉 + thinking 比纯输出长,timeout 同步抬

# 并发配置(2026-05-08 调:vision 6→12 提速一倍,实测 ~50s/call ≈ 14 RPM,
# 远低于 DashScope 视觉模型默认 60-120 RPM 限流;429 时 with_retry 自动兜底)
TEXT_CONCURRENCY = 16        # deepseek 动态并发,enrichment.py 已验证
VISION_CONCURRENCY = 12      # qwen3.5-plus 多模态

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("figure_summary")


# ─────────────────────────────────────────────────────────────────────
# jsonl 读写
# ─────────────────────────────────────────────────────────────────────


def _input_path(book_dir: str) -> Path:
    return INPUT_DIR / f"{book_dir}.jsonl"


def _output_path(book_dir: str) -> Path:
    return OUTPUT_DIR / f"{book_dir}.jsonl"


def _record_key(rec: dict[str, Any]) -> tuple[int, int]:
    """resume key:用 (page_idx, block_idx) 在书内唯一标识图块。"""
    return (rec["page_idx"], rec["block_idx"])


def _load_processed(book_dir: str, include_failed: bool = True) -> set[tuple[int, int]]:
    """读 output jsonl 已处理 (page_idx, block_idx) 集合(断点续传)。"""
    p = _output_path(book_dir)
    if not p.exists():
        return set()
    done: set[tuple[int, int]] = set()
    bad_lines = 0
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            if not include_failed and rec.get("status") != "ok":
                continue
            done.add(_record_key(rec))
    if bad_lines:
        log.warning(f"  output jsonl 中有 {bad_lines} 行损坏,已跳过")
    return done


_jsonl_lock = asyncio.Lock()


async def _append_jsonl(book_dir: str, record: dict[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False) + "\n"
    async with _jsonl_lock:
        with _output_path(book_dir).open("a", encoding="utf-8") as f:
            f.write(line)


# ─────────────────────────────────────────────────────────────────────
# manifest 读取
# ─────────────────────────────────────────────────────────────────────


def _load_manifest(book_dir: str) -> list[dict[str, Any]]:
    p = _input_path(book_dir)
    if not p.exists():
        raise FileNotFoundError(f"manifest 不存在:{p}")
    records = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


# ─────────────────────────────────────────────────────────────────────
# LLM client 构造
# ─────────────────────────────────────────────────────────────────────


def _build_text_llm() -> ChatOpenAI:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError(".env 缺少 DEEPSEEK_API_KEY(用于 table summary)")
    model_name = os.environ.get("DEEPSEEK_MODEL_NAME", DEEPSEEK_MODEL_NAME)
    base_url = os.environ.get("DEEPSEEK_BASE_URL", DEEPSEEK_BASE_URL)
    return ChatOpenAI(
        base_url=base_url,
        api_key=api_key,
        model=model_name,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
        timeout=LLM_TIMEOUT,
    )


def _build_vision_llm() -> ChatOpenAI:
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        raise RuntimeError(".env 缺少 LLM_API_KEY(DashScope key,用于 chart/figure summary)")
    model_name = os.environ.get("QWEN_VISION_MODEL", QWEN_VISION_MODEL)
    base_url = os.environ.get("LLM_BASE_URL", DASHSCOPE_BASE_URL)
    return ChatOpenAI(
        base_url=base_url,
        api_key=api_key,
        model=model_name,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
        timeout=LLM_TIMEOUT,
    )


# ─────────────────────────────────────────────────────────────────────
# 单条处理
# ─────────────────────────────────────────────────────────────────────


def _read_image_b64(img_abs_path: str) -> tuple[str, str]:
    """读图返回 (base64_str, mime)。jpg/png 各自识别。"""
    p = Path(img_abs_path)
    raw = p.read_bytes()
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return base64.b64encode(raw).decode("ascii"), mime


def _read_images_b64(paths: list[str]) -> list[tuple[str, str]]:
    """读多图,顺序保留(已由 merge 模块按 bbox y→x 排好)。"""
    return [_read_image_b64(p) for p in paths]


def _validate_output(out: FigureSummaryEnrichmentOutput) -> None:
    """轻量后置校验:命中任一异常情形抛 RuntimeError → with_retry 触发重试。"""
    ms = (out.medical_statement or "").strip()
    if not ms:
        raise RuntimeError("medical_statement is empty")
    if len(ms) < 30:
        raise RuntimeError(f"medical_statement too short ({len(ms)} chars)")
    title = (out.title or "").strip()
    if not title:
        raise RuntimeError("title is empty")
    if len(title) > 60:  # 30 字硬上限放到 60 是安全余量
        raise RuntimeError(f"title too long ({len(title)} chars)")
    summary = (out.summary or "").strip()
    if not summary:
        raise RuntimeError("summary is empty")
    if not out.hypothetical_questions:
        raise RuntimeError("hypothetical_questions is empty")


async def _summarize_one(
    text_chain: Any,
    vision_chain: Any,
    rec: dict[str, Any],
    book_dir: str,
    text_sem: asyncio.Semaphore,
    vision_sem: asyncio.Semaphore,
) -> str:
    """处理单条 manifest record。返回 'ok' / 'failed' / 'skip_orphan' / 'skip_sibling'。

    合并语义(merge_role 由 merge_multipanel_figures.py 注入):
      - "anchor":vision LLM 看 merged_image_abs_paths 全组截图
      - "sibling":跳过 LLM,记 status=merged_into_anchor 落 jsonl 留痕
      - "standalone" / 缺失:走单图原逻辑(向后兼容未跑过 merge 的 manifest)
    """
    page_idx = rec["page_idx"]
    block_idx = rec["block_idx"]
    chunk_kind = rec["chunk_kind"]
    merge_role = rec.get("merge_role")  # 未跑 merge 模块的 manifest 此字段缺失

    # 孤儿(heading_path=None)不生成 summary
    if rec.get("heading_path") is None:
        await _append_jsonl(book_dir, {
            "book_dir": book_dir,
            "page_idx": page_idx,
            "block_idx": block_idx,
            "chunk_kind": chunk_kind,
            "status": "skip_orphan",
            "reason": "heading_path is None (preface/ref-zone)",
        })
        return "skip_orphan"

    # sibling 跳过 LLM 调用(summary 由 anchor 统一产出),只留痕
    if merge_role == "sibling":
        await _append_jsonl(book_dir, {
            "book_dir": book_dir,
            "page_idx": page_idx,
            "block_idx": block_idx,
            "chunk_kind": chunk_kind,
            "status": "merged_into_anchor",
            "merge_group_id": rec.get("merge_group_id"),
        })
        return "skip_sibling"

    caption = " ".join(rec.get("caption") or [])
    content = rec.get("content") or ""
    sub_type = rec.get("mineru_sub_type") or ""
    heading_path = rec["heading_path"]

    t0 = time.perf_counter()
    try:
        if chunk_kind == "table":
            messages = build_figure_summary_text_prompt(
                heading_path=heading_path,
                caption=caption,
                mineru_sub_type=sub_type,
                content=content,
            )
            async with text_sem:
                result: FigureSummaryEnrichmentOutput = await text_chain.ainvoke(messages)
        else:  # chart / figure → vision LLM
            # anchor 用 merged_image_abs_paths;standalone / 缺 merge_role 用单图
            if merge_role == "anchor" and rec.get("merged_image_abs_paths"):
                img_paths = rec["merged_image_abs_paths"]
            else:
                single_path = rec.get("image_abs_path")
                if not single_path or not rec.get("image_exists"):
                    raise RuntimeError(f"image missing: {single_path}")
                img_paths = [single_path]

            images_b64 = _read_images_b64(img_paths)
            messages = build_figure_summary_multimodal_messages(
                heading_path=heading_path,
                caption=caption,
                chunk_kind=chunk_kind,
                mineru_sub_type=sub_type,
                images_b64=images_b64,
            )
            async with vision_sem:
                result = await vision_chain.ainvoke(messages)

        _validate_output(result)

        elapsed = time.perf_counter() - t0
        out_rec: dict[str, Any] = {
            "book_dir": book_dir,
            "page_idx": page_idx,
            "block_idx": block_idx,
            "chunk_kind": chunk_kind,
            "status": "ok",
            # 4 字段 — 直接对应 *_summary chunk 入 PG 的内容字段
            "medical_statement": result.medical_statement,
            "title": result.title,
            "summary": result.summary,
            "hypothetical_questions": result.hypothetical_questions,
            "elapsed_s": round(elapsed, 2),
        }
        if merge_role == "anchor":
            out_rec["merge_group_id"] = rec.get("merge_group_id")
            out_rec["n_images"] = len(rec.get("merged_image_abs_paths") or [])
        await _append_jsonl(book_dir, out_rec)
        return "ok"

    except Exception as e:
        elapsed = time.perf_counter() - t0
        err_kind = type(e).__name__
        err_msg = str(e)[:200]
        await _append_jsonl(book_dir, {
            "book_dir": book_dir,
            "page_idx": page_idx,
            "block_idx": block_idx,
            "chunk_kind": chunk_kind,
            "status": "failed",
            "error": f"{err_kind}: {err_msg}",
            "elapsed_s": round(elapsed, 2),
        })
        log.warning(f"  p{page_idx}#{block_idx} {chunk_kind}  FAILED  ({err_kind})")
        return "failed"


# ─────────────────────────────────────────────────────────────────────
# 一本书的主流程
# ─────────────────────────────────────────────────────────────────────


async def summarize_book(
    book_dir: str,
    text_chain: Any,
    vision_chain: Any,
    text_sem: asyncio.Semaphore,
    vision_sem: asyncio.Semaphore,
    limit: int | None = None,
    retry_failed: bool = False,
    only_kinds: set[str] | None = None,
    anchors_only: bool = False,
) -> dict[str, int]:
    """注:text_chain / vision_chain / sem 由 _amain 全局构建,跨书共享。

    早先版本是每本书内部各自 build_llm + Semaphore,在 books 顺序 await 时变成
    "12 本 × 各自 N 并发"的串行,实际吞吐严重退化(--limit 2 时每本只用 2 个槽)。
    现改成全局共享 + 跨书 asyncio.gather,~24 条一次性吃满 12 并发。
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"=== {book_dir} ===")

    all_records = _load_manifest(book_dir)
    log.info(f"  manifest 共 {len(all_records)} 条")

    if only_kinds:
        all_records = [r for r in all_records if r["chunk_kind"] in only_kinds]
        log.info(f"  --kind {','.join(sorted(only_kinds))} 过滤后 {len(all_records)} 条")

    if anchors_only:
        all_records = [r for r in all_records if r.get("merge_role") == "anchor"]
        log.info(f"  --anchors-only 过滤后 {len(all_records)} 条")

    # 孤儿(heading_path=None,~3.4% 全集)前置过滤,避免占 --limit 配额
    n_orphan_excluded = sum(1 for r in all_records if r.get("heading_path") is None)
    all_records = [r for r in all_records if r.get("heading_path") is not None]
    if n_orphan_excluded:
        log.info(f"  孤儿前置过滤:跳过 {n_orphan_excluded} 条(不入库,不调 LLM)")

    done = _load_processed(book_dir, include_failed=not retry_failed)
    todo = [r for r in all_records if _record_key(r) not in done]
    if limit is not None:
        todo = todo[:limit]
    log.info(f"  output jsonl 已有 {len(done)} 条,本次待处理 {len(todo)} 条")

    if not todo:
        log.info("  无待处理,跳过")
        return {"total": len(all_records), "done_before": len(done),
                "ok": 0, "failed": 0, "skip_orphan": 0, "skip_sibling": 0}

    t0 = time.perf_counter()
    results = await asyncio.gather(*(
        _summarize_one(text_chain, vision_chain, r, book_dir, text_sem, vision_sem)
        for r in todo
    ))
    elapsed = time.perf_counter() - t0

    n_ok = results.count("ok")
    n_fail = results.count("failed")
    n_orphan = results.count("skip_orphan")
    n_sibling = results.count("skip_sibling")
    rate = len(todo) / elapsed if elapsed > 0 else 0
    log.info(
        f"  本次完成 {len(todo)} 条:ok={n_ok}  failed={n_fail}  "
        f"skip_orphan={n_orphan}  skip_sibling={n_sibling}  "
        f"耗时 {elapsed:.1f}s  ({rate:.1f} chunk/s)"
    )

    return {
        "total": len(all_records),
        "done_before": len(done),
        "ok": n_ok,
        "failed": n_fail,
        "skip_orphan": n_orphan,
        "skip_sibling": n_sibling,
    }


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("book_dir", nargs="?", help="POC 目录名,如 poc_chunking_诊断学_第10版")
    p.add_argument("--all", action="store_true", help="顺序跑 BOOK_TO_FILENAME 全 12 本")
    p.add_argument("--limit", type=int, default=None, help="限制本次最多处理 N 条(试跑用)")
    p.add_argument("--retry-failed", action="store_true", help="重试 jsonl 中 status=failed 的条")
    p.add_argument(
        "--kind",
        default=None,
        help="只处理指定类型,支持逗号多选,如 --kind chart 或 --kind chart,figure",
    )
    p.add_argument(
        "--anchors-only",
        action="store_true",
        help="只处理 merge_role=anchor 的多面板组(便于审多图合并产出);未跑过 merge 模块的 manifest 此 flag 无效",
    )
    return p.parse_args()


def _parse_kinds(kind_arg: str | None) -> set[str] | None:
    if not kind_arg:
        return None
    valid = {"table", "chart", "figure"}
    kinds = {k.strip() for k in kind_arg.split(",") if k.strip()}
    bad = kinds - valid
    if bad:
        sys.exit(f"--kind 含未知类型 {bad};合法值:{valid}")
    return kinds


async def _amain():
    args = _parse_args()
    if args.all and args.book_dir:
        sys.exit("--all 与 book_dir 不能同时指定")
    if not args.all and not args.book_dir:
        sys.exit("用法:python scripts/figure_enrichment_generation.py <book_dir> [--limit N] [--kind table|chart|figure] | --all")

    only_kinds = _parse_kinds(args.kind)
    targets = list(BOOK_TO_FILENAME.keys()) if args.all else [args.book_dir]
    valid_targets = [b for b in targets if b in BOOK_TO_FILENAME]
    for b in targets:
        if b not in BOOK_TO_FILENAME:
            log.error(f"未知 book_dir: {b}(不在 BOOK_TO_FILENAME 映射,跳过)")

    # 全局共享 LLM client + chain + Semaphore(跨书共用,这样跨书 gather 也按 12 限流)
    text_llm = _build_text_llm()
    vision_llm = _build_vision_llm()
    # with_structured_output 不指定 method:langchain 默认对 ChatOpenAI 走 function_calling
    # (思考链走 content 字段,JSON 走 tool_calls 字段,天然解耦),避开 json_mode 服务端
    # 约束解码导致 thinking 与 JSON 互踩的失败模式(实测 1 条 神经内科 p152#0 figure 因
    # json_mode + thinking 冲突返回空 content)
    text_chain = text_llm.with_structured_output(
        FigureSummaryEnrichmentOutput
    ).with_retry(stop_after_attempt=2)
    vision_chain = vision_llm.with_structured_output(
        FigureSummaryEnrichmentOutput
    ).with_retry(stop_after_attempt=2)
    text_sem = asyncio.Semaphore(TEXT_CONCURRENCY)
    vision_sem = asyncio.Semaphore(VISION_CONCURRENCY)

    # 跨书并发跑(每本书内 asyncio.gather 也并发,共享 sem 总限流)
    grand_t0 = time.perf_counter()
    book_results = await asyncio.gather(*(
        summarize_book(
            book,
            text_chain=text_chain,
            vision_chain=vision_chain,
            text_sem=text_sem,
            vision_sem=vision_sem,
            limit=args.limit,
            retry_failed=args.retry_failed,
            only_kinds=only_kinds,
            anchors_only=args.anchors_only,
        ) for book in valid_targets
    ))
    grand_elapsed = time.perf_counter() - grand_t0

    grand = {"ok": 0, "failed": 0, "skip_orphan": 0, "skip_sibling": 0}
    for s in book_results:
        for k in grand:
            grand[k] += s.get(k, 0)
    log.info(
        f"=== 汇总 ok={grand['ok']}  failed={grand['failed']}  "
        f"skip_orphan={grand['skip_orphan']}  skip_sibling={grand['skip_sibling']}  "
        f"全程 {grand_elapsed:.1f}s ==="
    )


if __name__ == "__main__":
    asyncio.run(_amain())
