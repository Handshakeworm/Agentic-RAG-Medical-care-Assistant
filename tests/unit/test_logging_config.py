"""tests/unit/test_logging_config.py — H4 结构化日志单元测试(DEV_SPEC §5.2.1.1)。

覆盖:
1. JSON 格式输出 — 字段集合 / level 大写 / timestamp ISO8601
2. ContextVar 注入 — trace_id / session_id / patient_id 自动写入 record
3. extra={"node": ...} 透传
4. exc_info 异常堆栈格式化
5. configure_logging 幂等
"""
from __future__ import annotations

import json
import logging

import pytest

from config import logging_config
from config.logging_config import (
    _ContextVarFilter,
    _MedicalJsonFormatter,
    bind_request_context,
    configure_logging,
    reset_request_context,
)


@pytest.fixture(autouse=True)
def _reset_configure_state():
    """每个 case 重置 configure_logging 单例 + 清 ContextVar,避免污染。"""
    logging_config._CONFIGURED = False
    yield
    logging_config._CONFIGURED = False
    logging.getLogger().handlers.clear()


def _make_record(
    msg: str = "hello",
    level: int = logging.INFO,
    extra: dict | None = None,
) -> logging.LogRecord:
    rec = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            setattr(rec, k, v)
    _ContextVarFilter().filter(rec)
    return rec


# ────────────────────────────────────────────────────────────────────────────
# Formatter 字段
# ────────────────────────────────────────────────────────────────────────────


def test_format_emits_required_fields():
    """spec §5.2.1.1 强制字段:trace_id / session_id / patient_id / node / level
    / message / exc_info / timestamp / logger。
    """
    rec = _make_record("hi")
    out = _MedicalJsonFormatter().format(rec)
    payload = json.loads(out)
    assert set(payload.keys()) >= {
        "timestamp", "level", "logger", "trace_id",
        "session_id", "patient_id", "node", "message",
    }
    assert payload["level"] == "INFO"
    assert payload["message"] == "hi"
    assert payload["logger"] == "test.logger"
    # exc_info 非异常时不输出
    assert "exc_info" not in payload


def test_format_level_is_uppercase():
    rec = _make_record(level=logging.WARNING)
    payload = json.loads(_MedicalJsonFormatter().format(rec))
    assert payload["level"] == "WARNING"


def test_format_timestamp_is_iso8601_utc():
    rec = _make_record()
    payload = json.loads(_MedicalJsonFormatter().format(rec))
    ts = payload["timestamp"]
    # ISO 8601 with millisecond precision, UTC suffix Z
    assert ts.endswith("Z")
    assert "T" in ts


# ────────────────────────────────────────────────────────────────────────────
# ContextVar 注入
# ────────────────────────────────────────────────────────────────────────────


def test_context_var_injects_into_record():
    tokens = bind_request_context(
        trace_id="t-123", session_id="s-456", patient_id="p-789"
    )
    try:
        rec = _make_record()
        payload = json.loads(_MedicalJsonFormatter().format(rec))
        assert payload["trace_id"] == "t-123"
        assert payload["session_id"] == "s-456"
        assert payload["patient_id"] == "p-789"
    finally:
        reset_request_context(tokens)


def test_context_var_defaults_to_none_outside_request():
    """没绑过 ContextVar(脚本 / 测试外路径)→ 字段为 None,不抛"""
    rec = _make_record()
    payload = json.loads(_MedicalJsonFormatter().format(rec))
    assert payload["trace_id"] is None
    assert payload["session_id"] is None
    assert payload["patient_id"] is None
    assert payload["node"] is None


def test_reset_request_context_clears_state():
    tokens = bind_request_context(trace_id="t-AAA")
    reset_request_context(tokens)
    rec = _make_record()
    payload = json.loads(_MedicalJsonFormatter().format(rec))
    assert payload["trace_id"] is None


# ────────────────────────────────────────────────────────────────────────────
# extra={...} 透传
# ────────────────────────────────────────────────────────────────────────────


def test_extra_node_field_passes_through():
    """业务代码 logger.info(..., extra={"node": "diagnose_step1"}) 应原样进 JSON"""
    rec = _make_record(extra={"node": "diagnose_step1"})
    payload = json.loads(_MedicalJsonFormatter().format(rec))
    assert payload["node"] == "diagnose_step1"


def test_extra_arbitrary_fields_pass_through():
    """非约定 extra 字段(如 latency_ms / chunk_count)也应拍平进 JSON"""
    rec = _make_record(extra={"latency_ms": 1234, "chunk_count": 7})
    payload = json.loads(_MedicalJsonFormatter().format(rec))
    assert payload["latency_ms"] == 1234
    assert payload["chunk_count"] == 7


# ────────────────────────────────────────────────────────────────────────────
# exc_info
# ────────────────────────────────────────────────────────────────────────────


def test_exc_info_serialized_as_string():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        rec = logging.LogRecord(
            name="t", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="failed", args=(), exc_info=sys.exc_info(),
        )
        _ContextVarFilter().filter(rec)
        payload = json.loads(_MedicalJsonFormatter().format(rec))
        assert "exc_info" in payload
        assert "ValueError: boom" in payload["exc_info"]
        assert "Traceback" in payload["exc_info"]


# ────────────────────────────────────────────────────────────────────────────
# configure_logging
# ────────────────────────────────────────────────────────────────────────────


def test_configure_logging_idempotent():
    """重复调不应叠加 handler"""
    configure_logging()
    h1 = list(logging.getLogger().handlers)
    configure_logging()
    h2 = list(logging.getLogger().handlers)
    assert len(h1) == len(h2) == 1
    assert h1[0] is h2[0]


def test_configure_logging_replaces_root_handler():
    """重置 root,挂自己的 handler;旧 handler 必须被清掉"""
    legacy = logging.StreamHandler()
    logging.getLogger().addHandler(legacy)
    configure_logging()
    handlers = logging.getLogger().handlers
    assert legacy not in handlers
    assert len(handlers) == 1


def test_logger_actually_emits_json(capsys):
    """端到端:configure_logging 后 logger.info(...) 应输出合法 JSON 一行"""
    configure_logging()
    logger = logging.getLogger("e2e.test")
    tokens = bind_request_context(trace_id="t-end-to-end")
    try:
        logger.info("ping", extra={"node": "build_query"})
    finally:
        reset_request_context(tokens)

    out = capsys.readouterr().out.strip().splitlines()
    # 至少一条 JSON 行,内容含 trace_id 与 node
    assert any(json.loads(line).get("trace_id") == "t-end-to-end" for line in out)
    assert any(json.loads(line).get("node") == "build_query" for line in out)
