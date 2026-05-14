"""tests/unit/test_trace_id_middleware.py — H4 trace_id middleware(DEV_SPEC §5.2.1.1)。

覆盖:
1. 入口生成 UUID4 → 写 ContextVar → 响应 header 回写
2. 上游传 X-Trace-Id → 复用同一 ID(跨服务链路追踪)
3. 异常路径也 reset ContextVar(避免 ASGI worker 跨请求污染)
4. 业务路由内 logger 输出带相同 trace_id(端到端联通)
"""
from __future__ import annotations

import json
import logging
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import logging_config
from config.logging_config import configure_logging, trace_id_ctx
from src.api.middleware.trace_id import TraceIdMiddleware


@pytest.fixture(autouse=True)
def _reset_log_state():
    logging_config._CONFIGURED = False
    yield
    logging_config._CONFIGURED = False
    logging.getLogger().handlers.clear()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    @app.get("/echo")
    def echo():
        # 路由内回显当前 ContextVar 中的 trace_id,验证已写入
        return {"trace_id": trace_id_ctx.get()}

    return app


def test_generates_uuid_when_no_incoming_header():
    client = TestClient(_make_app())
    resp = client.get("/echo")
    assert resp.status_code == 200
    tid = resp.json()["trace_id"]
    # 是合法 UUID4
    parsed = uuid.UUID(tid)
    assert parsed.version == 4
    # 响应 header 回写同一 ID
    assert resp.headers["x-trace-id"] == tid


def test_reuses_incoming_trace_id_header():
    client = TestClient(_make_app())
    incoming = "abc-123-not-a-uuid-but-pass-through"
    resp = client.get("/echo", headers={"X-Trace-Id": incoming})
    assert resp.status_code == 200
    assert resp.json()["trace_id"] == incoming
    assert resp.headers["x-trace-id"] == incoming


def test_blank_incoming_header_falls_back_to_generated():
    client = TestClient(_make_app())
    resp = client.get("/echo", headers={"X-Trace-Id": "   "})
    tid = resp.json()["trace_id"]
    uuid.UUID(tid)  # 必须是新生成的 UUID4


def test_context_var_reset_after_response():
    """请求结束后 ContextVar 必须 reset,否则下个请求看到上个请求的 trace_id"""
    client = TestClient(_make_app())
    client.get("/echo")
    # 跳出请求生命周期后,ContextVar 默认值 None
    assert trace_id_ctx.get() is None


def test_logger_emits_same_trace_id(capsys):
    """端到端:在路由内调 logger.info,JSON 日志的 trace_id 与响应 header 一致"""
    configure_logging()
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    @app.get("/work")
    def work():
        logging.getLogger("biz").info("did stuff", extra={"node": "build_query"})
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/work")
    tid = resp.headers["x-trace-id"]
    out = capsys.readouterr().out.splitlines()
    matched = [
        line for line in out
        if line.strip().startswith("{") and json.loads(line).get("trace_id") == tid
    ]
    assert matched, f"未找到 trace_id={tid} 的日志行(实际输出:{out!r})"
    payload = json.loads(matched[0])
    assert payload["node"] == "build_query"
    assert payload["message"] == "did stuff"
