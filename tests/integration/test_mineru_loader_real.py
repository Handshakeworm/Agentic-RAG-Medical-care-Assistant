"""tests/integration/test_mineru_loader_real.py — C1 真起 PG + 真诊断学产物冒烟。

需要:
- PG 真服务在跑(`docker compose up -d postgres`)
- 已跑 0001 + 0002 迁移
- 诊断学已 mineru 解析(`/data/medical-resources/mineru-output/诊断学 第10版/hybrid_auto/`)

验证:
- 跑一次真灌库 → PG 有该行
- markdown 中"站体教学"被清干净(实测 v2/md 各 98 处)
- 灌库幂等(重跑不报错,行数仍 1)
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest
from sqlalchemy import text

DIAGNOSIS_MINERU_DIR = Path("/data/medical-resources/mineru-output/诊断学 第10版/hybrid_auto")
PDF_NAME = "诊断学 第10版.pdf"


def _pg_alive() -> bool:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    try:
        socket.create_connection((host, port), timeout=2).close()
        return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(not _pg_alive(), reason="PG 不可达,启动 docker compose 后再跑"),
    pytest.mark.skipif(
        not DIAGNOSIS_MINERU_DIR.is_dir(),
        reason=f"诊断学 mineru 产物未就位: {DIAGNOSIS_MINERU_DIR}",
    ),
]


@pytest.fixture
def cleanup_diagnosis():
    """灌库前后清理诊断学这条记录,避免污染其他测试。"""
    from src.db.postgres.connection import session_scope
    from src.rag.ingestion.idempotency import compute_source_id

    sid = compute_source_id(PDF_NAME)

    def _wipe():
        with session_scope() as s:
            s.execute(text("DELETE FROM raw_documents WHERE source_id = :sid"), {"sid": sid})
            s.execute(text("DELETE FROM sources WHERE source_id = :sid"), {"sid": sid})

    _wipe()
    yield sid
    _wipe()


def test_load_real_diagnosis_book(cleanup_diagnosis) -> None:
    """灌真诊断学 626 页 → 验证 PG 有数据 + 幻觉清干净。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models import RawDocument
    from src.rag.ingestion.mineru_loader import load_mineru_output

    stats = load_mineru_output(DIAGNOSIS_MINERU_DIR, PDF_NAME)

    # 1. 基本 stats 合理性
    assert stats["source_id"] == cleanup_diagnosis
    assert stats["content_list_pages"] == 626
    assert stats["image_blocks_content_dropped"] >= 100  # 实测约 532,至少几百
    assert stats["markdown_size_after_clean_bytes"] < stats["markdown_size_bytes"]
    assert stats["markdown_unclean_snippets"] == [], \
        f"有 {len(stats['markdown_unclean_snippets'])} 个指纹未清干净"

    # 2. PG 有该行,字段对得上
    with session_scope() as s:
        row = s.get(RawDocument, cleanup_diagnosis)
        assert row is not None
        assert row.file_name == PDF_NAME
        assert isinstance(row.content_list, list)
        assert len(row.content_list) == 626
        # markdown 中"站体教学"应等于 0(原本 98 处)
        assert "站体教学" not in row.markdown_content
        # 但占位符保留(Q2 决策)
        assert "![](images/" in row.markdown_content


def test_idempotent_relat(cleanup_diagnosis) -> None:
    """重跑一次,raw_documents 仍只 1 行(MEMORY: 幂等是核心准则)。"""
    from src.db.postgres.connection import session_scope
    from src.rag.ingestion.mineru_loader import load_mineru_output

    load_mineru_output(DIAGNOSIS_MINERU_DIR, PDF_NAME)
    load_mineru_output(DIAGNOSIS_MINERU_DIR, PDF_NAME)

    with session_scope() as s:
        n = s.execute(
            text("SELECT COUNT(*) FROM raw_documents WHERE source_id = :sid"),
            {"sid": cleanup_diagnosis},
        ).scalar()
        assert n == 1


def test_v2_image_content_field_completely_dropped(cleanup_diagnosis) -> None:
    """灌完后,PG content_list 中所有 image 块都不应再有 content 字段。"""
    from src.db.postgres.connection import session_scope
    from src.db.postgres.models import RawDocument
    from src.rag.ingestion.mineru_loader import load_mineru_output

    load_mineru_output(DIAGNOSIS_MINERU_DIR, PDF_NAME)

    with session_scope() as s:
        row = s.get(RawDocument, cleanup_diagnosis)
        leak_count = 0
        for page in row.content_list:
            for blk in page:
                if blk.get("type") == "image" and "content" in blk.get("content", {}):
                    leak_count += 1
        assert leak_count == 0, f"{leak_count} 个 image 块仍残留 content 字段"
