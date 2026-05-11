"""scripts/enrichment.py — §3.1.3 chunk LLM 增强(disk-first 落 jsonl)。

按本(book_dir)从 PG `chunks` 表拉所有 chunk_type='child' 的子块,
为每条调 DeepSeek-V4-Pro 生成 ChunkEnrichmentOutput(title / summary /
hypothetical_questions),结果 append 写入 `scripts/enrichment_output/<book_dir>.jsonl`。

设计要点:
- **disk-first**:LLM 产物先落本地 jsonl,审核/重灌之间解耦;不直接写 PG
- **断点续传**:启动时读 jsonl 已有 chunk_id 集合(无论 ok/failed 都 skip),
  避免重复烧 token;`--retry-failed` 标志显式重跑失败条
- **失败跳过**(spec §9.3):2 次重试都失败 → 记 status="failed" 写 jsonl,继续下一条
- **并发**:asyncio 协程 + Semaphore 限流(默认 20),DeepSeek API 网络 IO 为主
- **配置就近**:DeepSeek base_url / model_name 写死本文件,不污染 settings.py;
  仅 API key 走 .env(`DEEPSEEK_API_KEY`)

用法:
    python scripts/enrichment.py poc_chunking_诊断学_第10版         # 单本跑全量
    python scripts/enrichment.py poc_chunking_诊断学_第10版 --limit 5  # 试跑前 5 条
    python scripts/enrichment.py --all                             # 全 12 本顺序跑
    python scripts/enrichment.py poc_chunking_诊断学_第10版 --retry-failed  # 重试失败条
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
from langchain_core.exceptions import OutputParserException
from langchain_openai import ChatOpenAI
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

load_dotenv(REPO_ROOT / ".env")

from load_chunks_to_pg import BOOK_TO_FILENAME  # noqa: E402
from src.agent.schemas.ingestion import ChunkEnrichmentOutput  # noqa: E402
from src.db.postgres.connection import session_scope  # noqa: E402
from src.prompts.ingestion import build_chunk_enrichment_prompt  # noqa: E402
from src.rag.ingestion.idempotency import compute_source_id  # noqa: E402

# ─────────────────────────────────────────────────────────────────────
# DeepSeek 配置(就近收敛,one-off 离线任务,不进 settings.LLMSettings)
# ─────────────────────────────────────────────────────────────────────

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL_NAME = "deepseek-v4-pro"  # 2.5 折活动至 2026-05-31;.env DEEPSEEK_MODEL_NAME 可覆盖
CONCURRENCY = 20  # 并发协程数,DeepSeek RPM 限额内
LLM_TEMPERATURE = 0.3
LLM_MAX_TOKENS = 800
LLM_TIMEOUT = 60.0  # 单次调用超时 (s)

OUTPUT_DIR = REPO_ROOT / "scripts" / "enrichment_output"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("enrichment")


# ─────────────────────────────────────────────────────────────────────
# jsonl 读写
# ─────────────────────────────────────────────────────────────────────


def _jsonl_path(book_dir: str) -> Path:
    return OUTPUT_DIR / f"{book_dir}.jsonl"


def _load_processed(book_dir: str, include_failed: bool = True) -> set[str]:
    """读 jsonl 已处理 chunk_id 集合(断点续传)。

    include_failed=True 时,failed 也算已处理(不重跑);False 时,failed 不计,
    会被重跑(配 --retry-failed 用)。损坏行(json 解析失败)跳过 + 警告。
    """
    p = _jsonl_path(book_dir)
    if not p.exists():
        return set()
    done: set[str] = set()
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
            done.add(rec["chunk_id"])
    if bad_lines:
        log.warning(f"  jsonl 中有 {bad_lines} 行损坏,已跳过")
    return done


# 多协程同时 append 写 jsonl,要互斥 — 文件 append 在 Linux 是原子的(<= PIPE_BUF)
# 但跨平台稳妥起见加 asyncio.Lock。
_jsonl_lock = asyncio.Lock()


async def _append_jsonl(book_dir: str, record: dict[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False) + "\n"
    async with _jsonl_lock:
        with _jsonl_path(book_dir).open("a", encoding="utf-8") as f:
            f.write(line)


# ─────────────────────────────────────────────────────────────────────
# PG 拉数据(按本)
# ─────────────────────────────────────────────────────────────────────


def _fetch_child_chunks(book_dir: str) -> list[dict[str, Any]]:
    """从 PG 按 book_dir 拉所有 chunk_type='child' 子块。

    返回 [{chunk_id, heading_path, chunk_raw_text}, ...] 按 chunk_id 排序(稳定顺序)。
    """
    file_name = BOOK_TO_FILENAME[book_dir]
    source_id = compute_source_id(file_name)
    sql = text(
        "SELECT chunk_id, heading_path, chunk_raw_text "
        "FROM chunks WHERE source_id=:sid AND chunk_type='child' "
        "ORDER BY chunk_id"
    )
    with session_scope() as s:
        rows = s.execute(sql, {"sid": source_id}).fetchall()
    return [{"chunk_id": r[0], "heading_path": r[1], "chunk_raw_text": r[2]} for r in rows]


# ─────────────────────────────────────────────────────────────────────
# LLM 调用 — 单 chunk(spec §9.1 try/except/finally,§9.3 失败跳过)
# ─────────────────────────────────────────────────────────────────────


def _build_llm() -> ChatOpenAI:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError(
            ".env 缺少 DEEPSEEK_API_KEY。请在 .env 添加:\nDEEPSEEK_API_KEY=sk-..."
        )
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


async def _enrich_one(
    chain: Any,
    chunk: dict[str, Any],
    book_dir: str,
    sem: asyncio.Semaphore,
) -> str:
    """处理单条 chunk:调 LLM → 落 jsonl。返回 'ok' / 'failed'。"""
    async with sem:
        chunk_id = chunk["chunk_id"]
        messages = build_chunk_enrichment_prompt(
            heading_path=chunk["heading_path"],
            chunk_text=chunk["chunk_raw_text"],
        )
        t0 = time.perf_counter()
        try:
            result: ChunkEnrichmentOutput = await chain.ainvoke(messages)
            elapsed = time.perf_counter() - t0
            await _append_jsonl(book_dir, {
                "chunk_id": chunk_id,
                "status": "ok",
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
                "chunk_id": chunk_id,
                "status": "failed",
                "error": f"{err_kind}: {err_msg}",
                "elapsed_s": round(elapsed, 2),
            })
            log.warning(f"  chunk {chunk_id[:12]}…  FAILED  ({err_kind})")
            return "failed"


# ─────────────────────────────────────────────────────────────────────
# 一本书的主流程
# ─────────────────────────────────────────────────────────────────────


async def enrich_book(
    book_dir: str,
    limit: int | None = None,
    retry_failed: bool = False,
) -> dict[str, int]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"=== {book_dir} ===")

    all_chunks = _fetch_child_chunks(book_dir)
    log.info(f"  PG 拉到 {len(all_chunks)} child chunks")

    done = _load_processed(book_dir, include_failed=not retry_failed)
    todo = [c for c in all_chunks if c["chunk_id"] not in done]
    if limit is not None:
        todo = todo[:limit]
    log.info(f"  jsonl 已有 {len(done)} 条,本次待处理 {len(todo)} 条")

    if not todo:
        log.info("  无待处理,跳过")
        return {"total": len(all_chunks), "done_before": len(done), "ok": 0, "failed": 0}

    llm = _build_llm()
    chain = llm.with_structured_output(
        ChunkEnrichmentOutput,
        method="json_mode",  # DeepSeek 不支持 json_schema(2026-05 实测 400);json_mode 是服务端 JSON 合法性校验,不约束解码,不影响生成质量
    ).with_retry(stop_after_attempt=2)  # spec §9.3:enrichment 低安全等级,2 次重试

    sem = asyncio.Semaphore(CONCURRENCY)
    t0 = time.perf_counter()
    results = await asyncio.gather(*(
        _enrich_one(chain, c, book_dir, sem) for c in todo
    ))
    elapsed = time.perf_counter() - t0

    n_ok = results.count("ok")
    n_fail = results.count("failed")
    rate = len(todo) / elapsed if elapsed > 0 else 0
    log.info(
        f"  本次完成 {len(todo)} 条:ok={n_ok}  failed={n_fail}  "
        f"耗时 {elapsed:.1f}s  ({rate:.1f} chunk/s)"
    )

    return {
        "total": len(all_chunks),
        "done_before": len(done),
        "ok": n_ok,
        "failed": n_fail,
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
        sys.exit("用法:python scripts/enrichment.py <book_dir> [--limit N] | --all")

    targets = list(BOOK_TO_FILENAME.keys()) if args.all else [args.book_dir]
    grand = {"ok": 0, "failed": 0}
    for book in targets:
        if book not in BOOK_TO_FILENAME:
            log.error(f"未知 book_dir: {book}(不在 BOOK_TO_FILENAME 映射)")
            continue
        s = await enrich_book(book, limit=args.limit, retry_failed=args.retry_failed)
        grand["ok"] += s["ok"]
        grand["failed"] += s["failed"]
    log.info(f"=== 汇总 ok={grand['ok']}  failed={grand['failed']} ===")


if __name__ == "__main__":
    asyncio.run(_amain())
