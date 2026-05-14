"""src/api/routes/health.py — 健康检查端点(DEV_SPEC §5.2.4 + §8.4 H8)。

两个端点职责严格分离:
- `GET /healthz` (Liveness):零依赖,固定 200,K8s 进程级存活探针
- `GET /readyz`  (Readiness):并发探测 PG `SELECT 1` + Milvus `has_connection` + ping;
                              2s 超时,任一失败 503;Redis 不可用仍算 ready
                              (§5.1 降级模式,Redis 不影响功能正确性)

**两个端点不经过** JWT 鉴权 / 限流 / 审计(否则健康检查会污染 rag_trace 与 HTTP 指标 —
G3 / G1 / Instrumentator 已在 excluded_paths 列表中包含 `/healthz` `/readyz`)。

代码量 ≤ 40 行(不计 docstring)— spec §5.2.4 末"代码位置"约定。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.db.postgres.connection import get_engine


_logger = logging.getLogger(__name__)
_PROBE_TIMEOUT_SECONDS = 2.0

router = APIRouter()


# ────────────────────────────────────────────────────────────────────────────
# /healthz — Liveness(零依赖)
# ────────────────────────────────────────────────────────────────────────────


@router.get("/healthz", include_in_schema=False)
async def liveness() -> dict:
    """进程级 liveness:返回固定 200,**不**探测任何外部依赖。

    Spec §5.2.4 末:DB 挂了也返 200 — K8s 否则会错误重启进程反而加剧故障
    (PG/Milvus 重启窗口期内进程不该被一并杀)。
    """
    return {"status": "ok"}


# ────────────────────────────────────────────────────────────────────────────
# /readyz — Readiness(PG + Milvus 并发探测,2s 超时)
# ────────────────────────────────────────────────────────────────────────────


def _probe_postgres() -> tuple[str, bool, str | None]:
    """同步 SELECT 1。返回 (name, ok, error_str)。"""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return ("postgres", True, None)
    except Exception as e:
        return ("postgres", False, type(e).__name__)


def _probe_milvus() -> tuple[str, bool, str | None]:
    """has_connection 兜底建连 + 拉 server_version。"""
    try:
        # 局部 import,健康检查模块自身不应在 import 期就拉 pymilvus 全部 schema
        from pymilvus import connections, utility

        from src.db.milvus.docs_collection import _ensure_connection

        _ensure_connection()
        if not connections.has_connection("default"):
            return ("milvus", False, "no_connection")
        utility.get_server_version()
        return ("milvus", True, None)
    except Exception as e:
        return ("milvus", False, type(e).__name__)


@router.get("/readyz", include_in_schema=False)
async def readiness() -> JSONResponse:
    """流量准入:并发探 PG + Milvus,任一失败 503 + 列出 failing。

    Redis 不可用**仍算 ready**(§5.1 降级模式:配置回源 PG,功能不受影响)。
    """
    loop = asyncio.get_event_loop()
    pg_task = loop.run_in_executor(None, _probe_postgres)
    mv_task = loop.run_in_executor(None, _probe_milvus)

    try:
        results = await asyncio.wait_for(
            asyncio.gather(pg_task, mv_task), timeout=_PROBE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        # 超时 — 哪个慢就标 timeout,无法逐条区分时全标 timeout(运维看 metrics 进一步细分)
        _logger.warning("readiness probe timed out after %ss", _PROBE_TIMEOUT_SECONDS)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "failing": ["timeout"]},
        )

    deps = {name: ("ok" if ok else err or "fail") for name, ok, err in results}
    failing = [name for name, ok, _ in results if not ok]

    if failing:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "failing": failing, "deps": deps},
        )
    return JSONResponse(status_code=200, content={"status": "ready", "deps": deps})
