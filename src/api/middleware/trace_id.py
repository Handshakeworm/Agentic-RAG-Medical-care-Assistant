"""src/api/middleware/trace_id.py — trace_id 注入(DEV_SPEC §5.2.1.1 + §8.4 H4)。

每个 HTTP 请求入口生成一个 UUID(若 header `X-Trace-Id` 已带值则复用),
写入 ContextVar 并附到响应 header,日志 / 审计 / Loki 三路用同一个 ID 关联。

挂载顺序约定(对齐 §5.2.1.1 + G3 限流位置):
- 应在 RateLimitMiddleware **之外**(更早执行),让 429 响应也带 trace_id 可查
- 在 G4 endpoint 写 `rag_trace.trace_id` 时直接 `trace_id_ctx.get()` 即可

`/healthz` / `/readyz` / `/metrics` 仍然走这个 middleware(注入 trace_id 字段),
但日志可豁免(spec §5.2.1.1 末:无业务上下文)。这里不做特殊豁免,统一注入。
"""
from __future__ import annotations

import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from config.logging_config import bind_request_context, reset_request_context


_HEADER = "x-trace-id"


class TraceIdMiddleware(BaseHTTPMiddleware):
    """生成 / 复用 trace_id,绑 ContextVar,回写响应 header。"""

    def __init__(self, app: ASGIApp, *, header_name: str = _HEADER) -> None:
        super().__init__(app)
        self.header_name = header_name.lower()

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(self.header_name)
        # 复用上游传入的 trace_id(透过反向代理 / 网关)便于跨服务链路追踪;
        # 否则生成新的 UUID4。
        trace_id = incoming.strip() if incoming and incoming.strip() else str(uuid.uuid4())

        tokens = bind_request_context(trace_id=trace_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_context(tokens)

        response.headers[self.header_name] = trace_id
        return response
