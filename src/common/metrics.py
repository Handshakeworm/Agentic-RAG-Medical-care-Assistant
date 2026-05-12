"""src/common/metrics.py — 结构化输出可观测性原料(DEV_SPEC §9.1)。

**只暴露**:
1. 6 个 Prometheus 指标对象(模块级单例)
2. `RetryObserver`(LangChain `BaseCallbackHandler` 子类)+ `retry_observer` 单例

**严禁**(spec §9.1 实现风格约定):
- 不封装装饰器、helper 函数、上下文管理器
- 不在本模块内做任何 LLM 调用包装

各 LLM 调用点按 §9.1 模板裸写 try/except/finally,主动 import 这里的指标做
`.labels(...).inc()` / `.observe()`,并把 `retry_observer` 通过 `config={"callbacks": [retry_observer]}`
传给 `chain.invoke(...)` —— `with_retry` 内部的重试事件由 callback 捕获,业务代码看不到。

20+ 调用点合计约 300 行重复样板,这是"零抽象"的明确代价,换取异常作用域清晰、
重试可观察、与 with_retry 内部行为不打架。
"""
from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler
from prometheus_client import Counter, Histogram


# ────────────────────────────────────────────────────────────────────────────
# 6 个指标(spec §9.1 表格 + §4.2.7 结构化输出健康度表)
# ────────────────────────────────────────────────────────────────────────────


_attempts = Counter(
    "structured_output_attempt_total",
    "LLM 结构化输出调用尝试总数(进入 try 前 .inc())",
    ["node", "schema"],
)

_retries = Counter(
    "structured_output_retry_total",
    "LLM 结构化输出 with_retry 内部重试次数(由 RetryObserver.on_retry 捕获)",
    ["node", "schema"],
)

_failures = Counter(
    "structured_output_failure_total",
    "LLM 结构化输出尝试耗尽后仍失败的总数(except 分支 .inc())",
    ["node", "schema", "exception_type"],
)

_fallbacks = Counter(
    "structured_output_fallback_triggered_total",
    "LLM 调用失败后走兜底路径的总数(执行兜底前 .inc())",
    ["node", "fallback_type"],
)

_latency = Histogram(
    "structured_output_latency_seconds",
    "LLM 结构化输出调用端到端耗时(finally 块用 time.perf_counter() 差值 .observe())",
    ["node", "schema"],
)

_diagnose_reason = Counter(
    "diagnose_failure_reason_total",
    "⑩ diagnose 节点失败原因分桶(按 failure_reason 写入时 .inc())",
    ["reason_kind"],
)


# ────────────────────────────────────────────────────────────────────────────
# RetryObserver — LangChain BaseCallbackHandler 子类,捕获 with_retry 重试事件
# ────────────────────────────────────────────────────────────────────────────


class RetryObserver(BaseCallbackHandler):
    """`with_retry` 内部的重试发生在 LangChain Runnable 内部,业务代码的
    try/except 看不到,所以用 callback 捕获(spec §9.1 注:这是 LangChain 框架
    原生扩展点,不是项目自建封装层)。

    业务侧用法(spec §9.1 模板):
        chain.invoke(
            prompt,
            config={"callbacks": [retry_observer], "metadata": {"node": ..., "schema": ...}},
        )
    """

    def on_retry(self, retry_state, *, metadata=None, **kwargs):
        md = metadata or {}
        _retries.labels(
            node=md.get("node", "unknown"),
            schema=md.get("schema", "unknown"),
        ).inc()


retry_observer = RetryObserver()
