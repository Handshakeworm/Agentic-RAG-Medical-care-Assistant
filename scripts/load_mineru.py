"""scripts/load_mineru.py — 批量调用 C1 mineru_loader 灌库。

用法:
  # 灌单本(目录下唯一 backend 子目录)
  python -m scripts.load_mineru "诊断学 第10版"

  # 扫指定根目录下所有书,逐本灌(默认 /data/medical-resources/mineru-output/)
  python -m scripts.load_mineru --all
  python -m scripts.load_mineru --all --root /custom/mineru-output/

C8 完整 pipeline 入口(scripts/ingest.py)未来会包含 chunking + embedding +
storage 全链路,本脚本只做 loader 这一步,供 C2 之前批量灌原文用。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from src.rag.ingestion.mineru_loader import load_mineru_output

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("load_mineru")

DEFAULT_ROOT = Path("/data/medical-resources/mineru-output")


def _resolve_backend_dir(book_dir: Path) -> Path:
    """单本书目录下找 backend 子目录(hybrid_auto / vlm_auto / pipeline_auto)。"""
    candidates = [d for d in book_dir.iterdir() if d.is_dir() and d.name.endswith("_auto")]
    if not candidates:
        raise FileNotFoundError(f"{book_dir} 下没有 *_auto/ backend 子目录")
    # 优先 hybrid_auto,其次随便挑一个
    for d in candidates:
        if d.name == "hybrid_auto":
            return d
    return candidates[0]


def load_one(book_name: str, root: Path) -> dict:
    """灌单本书(book_name 不含 .pdf 后缀,跟 mineru 输出目录名一致)。"""
    book_dir = root / book_name
    if not book_dir.is_dir():
        raise FileNotFoundError(f"未找到 mineru 输出目录: {book_dir}")
    backend_dir = _resolve_backend_dir(book_dir)
    return load_mineru_output(backend_dir, original_pdf_name=f"{book_name}.pdf")


def load_all(root: Path) -> list[dict]:
    """扫 root 下所有书目录,逐本灌(单本失败不阻塞其他)。"""
    book_names = sorted(d.name for d in root.iterdir() if d.is_dir())
    logger.info(f"发现 {len(book_names)} 本书,开始批量灌库")

    results: list[dict] = []
    for i, name in enumerate(book_names, 1):
        logger.info(f"[{i}/{len(book_names)}] {name}")
        t0 = time.time()
        try:
            stats = load_one(name, root)
            stats["_status"] = "ok"
            logger.info(
                f"  ✓ source_id={stats['source_id']} "
                f"pages={stats['content_list_pages']} "
                f"blocks={stats['content_list_blocks_total']} "
                f"image_dropped={stats['image_blocks_content_dropped']} "
                f"unclean={len(stats['markdown_unclean_snippets'])} "
                f"in {stats['duration_seconds']}s"
            )
        except Exception as e:
            stats = {"_status": "failed", "_error": repr(e), "book_name": name,
                     "duration_seconds": round(time.time() - t0, 3)}
            logger.error(f"  ✗ {name}: {e}", exc_info=True)
        results.append(stats)
    return results


def _summary(results: list[dict]) -> None:
    ok = [r for r in results if r.get("_status") == "ok"]
    failed = [r for r in results if r.get("_status") == "failed"]
    total_pages = sum(r.get("content_list_pages", 0) for r in ok)
    total_blocks = sum(r.get("content_list_blocks_total", 0) for r in ok)
    total_image_dropped = sum(r.get("image_blocks_content_dropped", 0) for r in ok)
    total_unclean = sum(len(r.get("markdown_unclean_snippets", [])) for r in ok)
    total_dur = sum(r.get("duration_seconds", 0) for r in results)

    logger.info("=" * 60)
    logger.info(f"灌库汇总: 成功 {len(ok)} / 失败 {len(failed)}")
    logger.info(f"  总页数: {total_pages}")
    logger.info(f"  总 block: {total_blocks}")
    logger.info(f"  删 image content: {total_image_dropped}")
    logger.info(f"  清洗遗漏指纹: {total_unclean} (>0 需查 warning 日志)")
    logger.info(f"  总耗时: {total_dur:.1f}s")
    if failed:
        logger.info("失败列表:")
        for f in failed:
            logger.info(f"  - {f['book_name']}: {f['_error'][:120]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="批量调用 C1 mineru_loader 灌库")
    parser.add_argument("book_name", nargs="?", help="单本书目录名(不含 .pdf 后缀)")
    parser.add_argument("--all", action="store_true", help="扫 --root 下所有书逐本灌")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help=f"mineru 输出根目录(默认 {DEFAULT_ROOT})")
    args = parser.parse_args()

    if not args.root.is_dir():
        logger.error(f"--root 目录不存在: {args.root}")
        return 1

    if args.all:
        results = load_all(args.root)
        _summary(results)
        return 0 if all(r.get("_status") == "ok" for r in results) else 1
    if args.book_name:
        stats = load_one(args.book_name, args.root)
        logger.info(f"灌库完成: {stats}")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
