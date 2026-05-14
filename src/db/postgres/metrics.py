"""src/db/postgres/metrics.py — SQLAlchemy 依赖层指标(DEV_SPEC §5.2.1 ③ + H2)。

订阅 SQLAlchemy `before_cursor_execute` / `after_cursor_execute` 事件,在
`src/common/metrics.py` 已声明的 `db_query_latency_seconds` Histogram 上 .observe()。
连接池 Gauge `db_pool_checkedout` 由 `update_pool_gauge()` 拉取(可在 H8 / 定时任务
中调用,MVP 阶段不强制)。

`operation` label 从 SQL 首词(SELECT/INSERT/...)分桶,大小写不敏感;非常规语句
(SHOW / DDL / 多语句)归 `OTHER`。
"""
from __future__ import annotations

import time

from sqlalchemy import event
from sqlalchemy.engine import Engine

from src.common.metrics import db_pool_checkedout, db_query_latency_seconds


_KNOWN_OPS = ("SELECT", "INSERT", "UPDATE", "DELETE")
_QUERY_START_KEY = "__metrics_query_start__"


def _classify_operation(statement: str) -> str:
    head = statement.lstrip()[:7].upper()
    for op in _KNOWN_OPS:
        if head.startswith(op):
            return op
    return "OTHER"


def _on_before_execute(conn, cursor, statement, parameters, context, executemany):
    context._query_start_time = time.perf_counter()


def _on_after_execute(conn, cursor, statement, parameters, context, executemany):
    elapsed = time.perf_counter() - getattr(context, "_query_start_time", time.perf_counter())
    db_query_latency_seconds.labels(
        operation=_classify_operation(statement)
    ).observe(elapsed)


def install_engine_metrics(engine: Engine) -> None:
    """在 Engine 上注册事件监听器。幂等 — 重复注册同一 engine 不会叠加。

    业务代码:
        from src.db.postgres.connection import get_engine
        from src.db.postgres.metrics import install_engine_metrics
        install_engine_metrics(get_engine())
    """
    if not event.contains(engine, "before_cursor_execute", _on_before_execute):
        event.listen(engine, "before_cursor_execute", _on_before_execute)
    if not event.contains(engine, "after_cursor_execute", _on_after_execute):
        event.listen(engine, "after_cursor_execute", _on_after_execute)


def update_pool_gauge(engine: Engine) -> None:
    """读 engine.pool.checkedout() 写到 Gauge。

    可由 /readyz 路径或周期任务调用(MVP 阶段先不接周期任务,运维需要时手动触发)。
    """
    pool = engine.pool
    if hasattr(pool, "checkedout"):
        db_pool_checkedout.set(pool.checkedout())
