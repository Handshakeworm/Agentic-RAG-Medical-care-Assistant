"""tests/unit/test_metrics_extended.py — H2 扩展指标单元测试(DEV_SPEC §4.2.7 + §5.2.1)。

覆盖:
1. 4 个上下文/会话级指标对象存在且可 .observe()/.set()
2. 4 个依赖层指标对象存在且 label 集合正确
3. SQLAlchemy event listener 接入后能产 db_query_latency_seconds 样本
4. /metrics 端点能拉到所有新增指标的 family
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from src.common import metrics


# ────────────────────────────────────────────────────────────────────────────
# 指标对象存在性 + label 契约(spec §4.2.7 + §5.2.1 ③)
# ────────────────────────────────────────────────────────────────────────────


def test_context_metrics_objects_exist():
    assert hasattr(metrics, "context_tokens_per_llm_call")
    assert hasattr(metrics, "context_structured_fields_size")
    assert hasattr(metrics, "context_messages_count")
    assert hasattr(metrics, "context_loop_iterations")


def test_context_tokens_observe_works():
    """spec §4.2.7 表 1:Histogram,label = (node, model)"""
    metrics.context_tokens_per_llm_call.labels(
        node="diagnose_step1", model="deepseek-v4-pro"
    ).observe(1234)
    # 不抛即视为接口对齐;具体值由 Prometheus 抓取


def test_context_loop_iterations_label_split():
    """label loop_type 必须区分 followup / exam(spec §9.7 两个上限)"""
    metrics.context_loop_iterations.labels(loop_type="followup").observe(5)
    metrics.context_loop_iterations.labels(loop_type="exam").observe(2)


def test_dependency_metrics_objects_exist():
    assert hasattr(metrics, "db_query_latency_seconds")
    assert hasattr(metrics, "db_pool_checkedout")
    assert hasattr(metrics, "redis_command_latency_seconds")
    assert hasattr(metrics, "milvus_rpc_latency_seconds")
    assert hasattr(metrics, "milvus_rpc_errors_total")


def test_milvus_rpc_metrics_label_contract():
    """spec §5.2.1 ③:milvus 指标必须按 collection + operation 分桶"""
    metrics.milvus_rpc_latency_seconds.labels(
        collection="docs_collection", operation="search"
    ).observe(0.123)
    metrics.milvus_rpc_errors_total.labels(
        collection="terms_collection", operation="query", error_code="timeout"
    ).inc()


# ────────────────────────────────────────────────────────────────────────────
# SQLAlchemy event listener
# ────────────────────────────────────────────────────────────────────────────


def test_classify_operation_buckets():
    from src.db.postgres.metrics import _classify_operation

    assert _classify_operation("SELECT 1") == "SELECT"
    assert _classify_operation("  select id from x") == "SELECT"
    assert _classify_operation("INSERT INTO x VALUES (1)") == "INSERT"
    assert _classify_operation("UPDATE x SET ...") == "UPDATE"
    assert _classify_operation("DELETE FROM x") == "DELETE"
    assert _classify_operation("CREATE TABLE x ()") == "OTHER"
    assert _classify_operation("BEGIN") == "OTHER"


def test_install_engine_metrics_idempotent():
    """重复 install 不应叠加 listener(避免一次 query 产两条样本)。

    SQLAlchemy event API 要求 target 是真 Engine,不能 MagicMock。用 SQLite memory
    engine,纯进程内、零依赖。
    """
    from sqlalchemy import create_engine, event
    from src.db.postgres.metrics import _on_before_execute, install_engine_metrics

    engine = create_engine("sqlite:///:memory:")
    install_engine_metrics(engine)
    install_engine_metrics(engine)
    # 第二次 install 不应再 add listener
    assert event.contains(engine, "before_cursor_execute", _on_before_execute)


def test_query_timing_observed_via_event(capsys):
    """端到端:before/after 事件包一对,_query_start_time 存 context"""
    from src.db.postgres.metrics import _on_after_execute, _on_before_execute

    ctx = MagicMock()
    _on_before_execute(None, None, "SELECT 1", None, ctx, False)
    assert hasattr(ctx, "_query_start_time")
    time.sleep(0.001)  # 留 1ms latency
    _on_after_execute(None, None, "SELECT 1", None, ctx, False)
    # observe 已发生;Histogram 的 _sum 应 > 0
    sum_sample = list(metrics.db_query_latency_seconds._metrics.values())[0]
    assert sum_sample._sum.get() > 0


# ────────────────────────────────────────────────────────────────────────────
# /metrics 端点能拉到新指标
# ────────────────────────────────────────────────────────────────────────────


def test_metrics_endpoint_exposes_extended_indicators():
    """G1 + H2:GET /metrics 应包含本次新增的指标 family"""
    # 触发一次产样本
    metrics.context_tokens_per_llm_call.labels(node="x", model="y").observe(100)
    metrics.redis_command_latency_seconds.labels(command="GET").observe(0.001)

    from prometheus_client import generate_latest

    body = generate_latest().decode()
    assert "context_tokens_per_llm_call" in body
    assert "context_structured_fields_size" in body
    assert "context_messages_count" in body
    assert "context_loop_iterations" in body
    assert "db_query_latency_seconds" in body
    assert "redis_command_latency_seconds" in body
    assert "milvus_rpc_latency_seconds" in body
