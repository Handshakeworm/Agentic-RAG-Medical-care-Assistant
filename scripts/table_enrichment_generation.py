"""scripts/table_enrichment_generation.py — 给 table 源块单步产出 table_summary chunk 入库 4 字段(disk-first 落 jsonl)。

按本(book_dir)读 `scripts/figure_extract_output/<book_dir>.jsonl` 的 manifest,
**只处理 chunk_kind="table" 的块**(chart/figure 走独立的 figure_enrichment_generation.py,
LLM 选型 + prompt 完全不同,放一起会让两条路径相互拖累):

- text LLM(deepseek-v4-pro)看 html → 4 字段单步产出

产出落 `scripts/table_enrichment_output/<book_dir>.jsonl`,每条 record:
    {book_dir, page_idx, block_idx, chunk_kind, status,
     medical_statement, title, summary, hypothetical_questions,
     elapsed_s}

medical_statement 直接对应下游 table_summary chunk 的 chunk_raw_text;title/summary/
hypothetical_questions 对应 ChunkEnrichmentOutput 的同名字段——一次产出可直接灌库。

下游 cosine 去重 + chunks 表灌库由独立任务接力,本脚本不负责。

# 设计要点(对齐 figure_enrichment_generation.py)

- **disk-first**:LLM 产物先落本地 jsonl,审核/重灌之间解耦;不直接写 PG
- **断点续传**:启动时读 jsonl 已有 (page_idx, block_idx) 集合(无论 ok/failed 都 skip),
  避免重复烧 token;`--retry-failed` 标志显式重跑失败条
- **失败跳过**(spec §9.3 低安全等级):2 次重试都失败 → 记 status="failed" 写 jsonl
- **并发**:asyncio + Semaphore;deepseek-v4-pro 文本调用比 vision 稳,16 并发已被
  enrichment.py(22287 条 child enrichment)验证
- **配置就近**:复用 enrichment.py 的 DEEPSEEK_BASE_URL / DEEPSEEK_API_KEY / DEEPSEEK_MODEL_NAME
- **孤儿丢弃**:manifest 中 heading_path=None 的(table 中 ~129 条)直接 skip,不入库
- **跨书并发**:全局共享 LLM + Semaphore + asyncio.gather 跨书 → 12+ 本同时跑共占 16 并发槽

# 跨页 sibling 处理(merge_role 字段由 scripts/merge_crosspage_tables.py 注入)

- **duplicate**:mineru 跨页冗余转录(sibling 内容已在 anchor 里),跳过 LLM,
  写 status=duplicate_of_anchor 留痕
- **standalone + merged_html_extension**:真分页同表 anchor — 拿 content + extension
  拼接的 html 喂给 LLM(extension 已经过去重,只含 sibling 中 anchor 没有的新行)
- **standalone**(无 extension)/ 缺 merge_role:走单表原逻辑

用法:
    python scripts/table_enrichment_generation.py poc_chunking_诊断学_第10版            # 单本
    python scripts/table_enrichment_generation.py poc_chunking_诊断学_第10版 --limit 5  # 试跑前 5
    python scripts/table_enrichment_generation.py --all                                  # 全 12 本
    python scripts/table_enrichment_generation.py --all --retry-failed                  # 重跑 failed
"""

from __future__ import annotations

import argparse
import asyncio
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
from src.prompts.ingestion import build_table_summary_messages  # noqa: E402

# ─────────────────────────────────────────────────────────────────────
# 路径 / LLM 配置(就近收敛,one-off 离线任务,不进 settings.LLMSettings)
# ─────────────────────────────────────────────────────────────────────

INPUT_DIR = REPO_ROOT / "scripts" / "figure_extract_output"
OUTPUT_DIR = REPO_ROOT / "scripts" / "table_enrichment_output"

# Text LLM(table → html → 4 字段)— 复用 enrichment.py 同款 deepseek
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL_NAME = "deepseek-v4-pro"

LLM_TEMPERATURE = 0.3
# 单步 4 字段 JSON + 可能的 thinking 推理预算(参 figure 脚本):4096 token 给足
LLM_MAX_TOKENS = 4096
LLM_TIMEOUT = 90.0

TEXT_CONCURRENCY = 16  # enrichment.py 22287 条已验证

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("table_enrichment")


# ─────────────────────────────────────────────────────────────────────
# jsonl 读写
# ─────────────────────────────────────────────────────────────────────


def _input_path(book_dir: str) -> Path:
    return INPUT_DIR / f"{book_dir}.jsonl"


def _output_path(book_dir: str) -> Path:
    return OUTPUT_DIR / f"{book_dir}.jsonl"


def _record_key(rec: dict[str, Any]) -> tuple[int, int]:
    """resume key:用 (page_idx, block_idx) 在书内唯一标识 table 块。"""
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
# LLM client
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


# ─────────────────────────────────────────────────────────────────────
# 输出后置校验(同 figure 脚本)
# ─────────────────────────────────────────────────────────────────────


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
    if len(title) > 60:
        raise RuntimeError(f"title too long ({len(title)} chars)")
    summary = (out.summary or "").strip()
    if not summary:
        raise RuntimeError("summary is empty")
    if not out.hypothetical_questions:
        raise RuntimeError("hypothetical_questions is empty")


# ─────────────────────────────────────────────────────────────────────
# 单条处理
# ─────────────────────────────────────────────────────────────────────


async def _summarize_one(
    text_chain: Any,
    rec: dict[str, Any],
    book_dir: str,
    text_sem: asyncio.Semaphore,
) -> str:
    """处理单条 table manifest record。详细去重语义见模块 docstring。

    返回 'ok' / 'failed' / 'skip_orphan' / 'skip_non_table' / 'skip_duplicate'。
    """
    page_idx = rec["page_idx"]
    block_idx = rec["block_idx"]
    chunk_kind = rec["chunk_kind"]
    merge_role = rec.get("merge_role")  # 未跑 merge 模块的 manifest 此字段缺失

    # 防御性:本脚本只处理 table
    if chunk_kind != "table":
        return "skip_non_table"

    # duplicate 跳过 LLM(anchor 已包含完整表 / 拿 extension 合并跑,duplicate 不重复跑),只留痕
    if merge_role == "duplicate":
        await _append_jsonl(book_dir, {
            "book_dir": book_dir,
            "page_idx": page_idx,
            "block_idx": block_idx,
            "chunk_kind": chunk_kind,
            "status": "duplicate_of_anchor",
            "merge_group_id": rec.get("merge_group_id"),
        })
        return "skip_duplicate"

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

    caption = " ".join(rec.get("caption") or [])
    footnote = " ".join(rec.get("footnote") or [])
    sub_type = rec.get("mineru_sub_type") or ""
    heading_path = rec["heading_path"]
    # anchor 有 merged_html_extension 时(真分页同表),把新增行拼到 content 后给 LLM
    content = rec.get("content") or ""
    ext = rec.get("merged_html_extension")
    if ext:
        content = content + "\n" + ext

    t0 = time.perf_counter()
    try:
        messages = build_table_summary_messages(
            heading_path=heading_path,
            caption=caption,
            footnote=footnote,
            mineru_sub_type=sub_type,
            content=content,
        )
        async with text_sem:
            result: FigureSummaryEnrichmentOutput = await text_chain.ainvoke(messages)

        _validate_output(result)

        elapsed = time.perf_counter() - t0
        await _append_jsonl(book_dir, {
            "book_dir": book_dir,
            "page_idx": page_idx,
            "block_idx": block_idx,
            "chunk_kind": chunk_kind,
            "status": "ok",
            # 4 字段 — 直接对应 table_summary chunk 入 PG 的内容字段
            "medical_statement": result.medical_statement,
            "title": result.title,
            "summary": result.summary,
            "hypothetical_questions": result.hypothetical_questions,
            "elapsed_s": round(elapsed, 2),
        })
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
        log.warning(f"  p{page_idx}#{block_idx} table  FAILED  ({err_kind})")
        return "failed"


# ─────────────────────────────────────────────────────────────────────
# 一本书的主流程
# ─────────────────────────────────────────────────────────────────────


async def summarize_book(
    book_dir: str,
    text_chain: Any,
    text_sem: asyncio.Semaphore,
    limit: int | None = None,
    retry_failed: bool = False,
) -> dict[str, int]:
    """注:text_chain / text_sem 由 _amain 全局构建,跨书共享。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"=== {book_dir} ===")

    all_records = _load_manifest(book_dir)
    log.info(f"  manifest 共 {len(all_records)} 条")

    # 只处理 table
    all_records = [r for r in all_records if r["chunk_kind"] == "table"]
    log.info(f"  table 过滤后 {len(all_records)} 条")

    # 孤儿(heading_path=None)前置过滤
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
        return {"total": len(all_records), "done_before": len(done), "ok": 0, "failed": 0}

    t0 = time.perf_counter()
    results = await asyncio.gather(*(
        _summarize_one(text_chain, r, book_dir, text_sem) for r in todo
    ))
    elapsed = time.perf_counter() - t0

    n_ok = results.count("ok")
    n_fail = results.count("failed")
    n_orphan = results.count("skip_orphan")
    n_dup = results.count("skip_duplicate")
    rate = len(todo) / elapsed if elapsed > 0 else 0
    log.info(
        f"  本次完成 {len(todo)} 条:ok={n_ok}  failed={n_fail}  "
        f"skip_orphan={n_orphan}  skip_duplicate={n_dup}  "
        f"耗时 {elapsed:.1f}s  ({rate:.1f} chunk/s)"
    )

    return {
        "total": len(all_records),
        "done_before": len(done),
        "ok": n_ok,
        "failed": n_fail,
        "skip_orphan": n_orphan,
        "skip_duplicate": n_dup,
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
    return p.parse_args()


async def _amain():
    args = _parse_args()
    if args.all and args.book_dir:
        sys.exit("--all 与 book_dir 不能同时指定")
    if not args.all and not args.book_dir:
        sys.exit("用法:python scripts/table_enrichment_generation.py <book_dir> [--limit N] | --all")

    targets = list(BOOK_TO_FILENAME.keys()) if args.all else [args.book_dir]
    valid_targets = [b for b in targets if b in BOOK_TO_FILENAME]
    for b in targets:
        if b not in BOOK_TO_FILENAME:
            log.error(f"未知 book_dir: {b}(不在 BOOK_TO_FILENAME 映射,跳过)")

    text_llm = _build_text_llm()
    # method="json_mode":DeepSeek 不支持 json_schema(2026-05 实测 400 BadRequest
    # "This response_format type is unavailable now"),enrichment.py 也走 json_mode。
    # 跟 figure 脚本(qwen)默认 function_calling 不同,deepseek 端只能用 json_mode。
    text_chain = text_llm.with_structured_output(
        FigureSummaryEnrichmentOutput,
        method="json_mode",
    ).with_retry(stop_after_attempt=2)
    text_sem = asyncio.Semaphore(TEXT_CONCURRENCY)

    grand_t0 = time.perf_counter()
    book_results = await asyncio.gather(*(
        summarize_book(
            book,
            text_chain=text_chain,
            text_sem=text_sem,
            limit=args.limit,
            retry_failed=args.retry_failed,
        ) for book in valid_targets
    ))
    grand_elapsed = time.perf_counter() - grand_t0

    grand = {"ok": 0, "failed": 0, "skip_orphan": 0, "skip_duplicate": 0}
    for s in book_results:
        for k in grand:
            grand[k] += s.get(k, 0)
    log.info(
        f"=== 汇总 ok={grand['ok']}  failed={grand['failed']}  "
        f"skip_orphan={grand['skip_orphan']}  skip_duplicate={grand['skip_duplicate']}  "
        f"全程 {grand_elapsed:.1f}s ==="
    )


if __name__ == "__main__":
    asyncio.run(_amain())
