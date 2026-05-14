"""config/logging_config.py — 结构化日志(DEV_SPEC §5.2.1.1 + §8.4 H4)。

强制 JSON 一行一条,字段(spec §5.2.1.1 表):
  `trace_id` / `session_id` / `patient_id` / `node` / `level` / `message`
  / `exc_info` / `timestamp` / `logger`

`trace_id` 等上下文字段从 `contextvars.ContextVar` 取,FastAPI middleware 在请求
入口注入,Agent 节点 LangGraph `RunnableConfig` 透传,Promtail 采到 Loki 后可按
`trace_id` label 直接关联到 PG `rag_trace` 详情(spec §5.2.1.1 接 G4)。

**实现风格**:本模块只声明 Formatter / Filter / setup 函数;**不**封装 Logger 类、
不写 wrapper。业务侧标准 `logger = logging.getLogger(__name__)` + `logger.info(...)`
+ 可选 `extra={"node": "diagnose_step1"}` 即可。

**spec 偏差备注**:spec §5.2.1.1 给的实现提示是 `python-json-logger 的 JsonFormatter`,
本实现改用 stdlib `json` 自写 ~30 行 Formatter,等价输出,字段约定完全对齐 spec 表。
理由:不引入第三方依赖与项目 cu128 索引策略冲突。
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone


# ────────────────────────────────────────────────────────────────────────────
# Context Vars(spec §5.2.1.1 表)
# ────────────────────────────────────────────────────────────────────────────


# 默认值 None — 非 HTTP 请求路径(ingestion 脚本 / 后台任务)调 logger 也合法,
# 字段写 None 即可。
trace_id_ctx: ContextVar[str | None] = ContextVar("trace_id", default=None)
session_id_ctx: ContextVar[str | None] = ContextVar("session_id", default=None)
patient_id_ctx: ContextVar[str | None] = ContextVar("patient_id", default=None)


# ────────────────────────────────────────────────────────────────────────────
# Filter — 把 ContextVar 写到 LogRecord
# ────────────────────────────────────────────────────────────────────────────


class _ContextVarFilter(logging.Filter):
    """把 trace_id / session_id / patient_id 从 ContextVar 注入 LogRecord。

    `extra={"node": ...}` 走 LogRecord.__dict__ 标准路径,Filter 兜底默认 None,
    保证 JSON 输出里 `node` 字段恒在(便于 Loki label 提取)。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_ctx.get()
        record.session_id = session_id_ctx.get()
        record.patient_id = patient_id_ctx.get()
        if not hasattr(record, "node"):
            record.node = None
        return True


# ────────────────────────────────────────────────────────────────────────────
# Formatter
# ────────────────────────────────────────────────────────────────────────────


# stdlib `logging.LogRecord` 自带的属性集合(用来识别 `extra={...}` 传入的额外字段)
_STD_LOG_RECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime",
    # 本模块 Filter 注入的几个,统一序列化时单独处理
    "trace_id", "session_id", "patient_id", "node",
})


class _MedicalJsonFormatter(logging.Formatter):
    """JSON 行 Formatter。

    输出字段顺序固定(便于 grep):
        timestamp, level, logger, trace_id, session_id, patient_id, node,
        message, exc_info, [其他 extra=... 透传字段]
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
            "level": record.levelname.upper(),
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", None),
            "session_id": getattr(record, "session_id", None),
            "patient_id": getattr(record, "patient_id", None),
            "node": getattr(record, "node", None),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        # extra={...} 用户透传字段一并拍平进 JSON(LogRecord.__dict__ 中
        # 不在标准字段表里的都视作 extra)
        for key, value in record.__dict__.items():
            if key not in _STD_LOG_RECORD_ATTRS and key not in payload:
                payload[key] = value

        return json.dumps(payload, ensure_ascii=False, default=str)


# ────────────────────────────────────────────────────────────────────────────
# setup
# ────────────────────────────────────────────────────────────────────────────


_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """初始化全局 logging。

    - 加 `_ContextVarFilter` 让所有 record 带 `trace_id/session_id/patient_id/node`
    - StreamHandler → stdout(Promtail 抓 docker json-file driver 的 stdout)
    - 替换 root handler,清掉 uvicorn 默认的纯文本 handler

    幂等:重复 import 不会叠加 handler。
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    formatter = _MedicalJsonFormatter()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(_ContextVarFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn 自己的几个 logger 也走同一格式(否则 access log 还会是文本)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True  # 由 root handler 接管,统一格式

    _CONFIGURED = True


def bind_request_context(
    *,
    trace_id: str,
    session_id: str | None = None,
    patient_id: str | None = None,
) -> tuple:
    """在请求入口设置 ContextVar,返回 token 元组供退出时 reset。

    用法(典型 FastAPI middleware):
        tokens = bind_request_context(trace_id=tid, ...)
        try:
            return await call_next(request)
        finally:
            reset_request_context(tokens)
    """
    return (
        trace_id_ctx.set(trace_id),
        session_id_ctx.set(session_id),
        patient_id_ctx.set(patient_id),
    )


def reset_request_context(tokens: tuple) -> None:
    """ContextVar reset(避免 ASGI worker 跨请求复用 task 时上下文残留)。"""
    trace_id_ctx.reset(tokens[0])
    session_id_ctx.reset(tokens[1])
    patient_id_ctx.reset(tokens[2])
