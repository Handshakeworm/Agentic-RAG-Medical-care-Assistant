"""src/rag/retrieval/reranker.py — BGE-Reranker-v2-minicpm-layerwise 客户端 + fallback 包装(DEV_SPEC §3.2.3)。

两层 API:
  ① `Reranker` / `get_reranker()` — B7 实现的 lazy load 单例(LayerWiseFlagLLMReranker
     封装,cutoff_layers 走 §9.7 `settings.agent_limits.RERANKER_CUTOFF_LAYERS`,
     None=全 40 层)
  ② `rerank_with_fallback()` — E5 高阶函数:对 documents 跑精排,异常 / 超时 / 模型
     不可用时 **必须回退至原序**(spec §3.2.3 表格 + "默认策略"段),供 ⑩ diagnose
     节点 Step 0 调用

设计要点(spec §3.2.3):
- Reranker 不在 ③ retrieve 中调用,仅在 ⑩ diagnose 前置 Step 0
- 必须可关闭(关闭 = 调用方传 enabled=False 或不调本模块,直接用 candidate_chunks 原序)
- 失败时返回原序 idx 列表,**不抛异常**,保证 ⑩ diagnose 仍能执行

返回形态:list[int] 是相对 documents 的原序索引 idx;调用方按 idx 重排
candidate_chunks。这样 reranker 模块不耦合 candidate_chunks 形态。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from functools import lru_cache

from FlagEmbedding import LayerWiseFlagLLMReranker

from config.settings import settings


_logger = logging.getLogger(__name__)


class Reranker:
    """LayerWiseFlagLLMReranker 薄封装:lazy load + 单进程 GPU 资源复用。

    cutoff_layer 走 `settings.agent_limits.RERANKER_CUTOFF_LAYERS`(§9.7),
    None=全 40 层(模型自身 layerwise 完整深度);非 None 时 layerwise early-exit
    省 ~30% 推理时间,质量损失可控。
    """

    def __init__(
        self,
        model_path: str | None = None,
        device: str | None = None,
        use_fp16: bool = True,
    ):
        self.model_path = model_path or settings.reranker.MODEL_PATH
        self.device = device or settings.reranker.DEVICE
        self.use_fp16 = use_fp16 and self.device == "cuda"
        # spec §9.7:None 表示全层不截断;非 None 走 layerwise early-exit
        self.cutoff_layer: int | None = settings.agent_limits.RERANKER_CUTOFF_LAYERS
        self._model: LayerWiseFlagLLMReranker | None = None

    @property
    def model(self) -> LayerWiseFlagLLMReranker:
        if self._model is None:
            self._model = LayerWiseFlagLLMReranker(
                self.model_path,
                use_fp16=self.use_fp16,
                devices=self.device,
            )
        return self._model

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        if not documents:
            return []

        pairs = [[query, doc] for doc in documents]
        # cutoff_layer=None → 不传 cutoff_layers 参数,模型用全 40 层
        score_kwargs = {} if self.cutoff_layer is None else {"cutoff_layers": [self.cutoff_layer]}
        scores = self.model.compute_score(pairs, **score_kwargs)

        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        if top_k is not None:
            ranked = ranked[:top_k]
        return ranked


@lru_cache(maxsize=1)
def get_reranker() -> Reranker:
    return Reranker()


def rerank_with_fallback(
    query: str,
    documents: list[str],
    top_k: int | None = None,
    timeout_sec: float | None = 10.0,
    enabled: bool = True,
    reranker: Reranker | None = None,
) -> list[int]:
    """对 documents 跑 cross-encoder 精排,返回相对 documents 的原序 idx 列表(降序排)。

    Args:
        query: 查询文本(诊断时通常是已收敛 chief_complaint + confirmed_symptoms)
        documents: 候选文本列表(调用方按 candidate_chunks 中合适字段拼出,如父块全文)
        top_k: 截断,None 时不截断
        timeout_sec: 单次调用超时秒数;None = 无超时
        enabled: False 时直接走 fallback 原序(spec §3.2.3 "None(关闭)"模式)
        reranker: 可注入测试 mock;None 时用 get_reranker() singleton

    Returns:
        list[int]:精排后的原序索引,长度 ≤ min(top_k, len(documents))。
        documents 为空 → [];异常/超时/disabled → 原序 idx [0, 1, 2, ...] 截断。

    spec §3.2.3 强约束:**不抛异常**,失败必须 fallback 至 candidate_chunks 原序,
    保证 ⑩ diagnose 仍能正常推理。
    """
    if not documents:
        return []

    n = len(documents)
    fallback_top = top_k if top_k is not None else n
    fallback = list(range(min(fallback_top, n)))

    if not enabled:
        return fallback

    rk = reranker if reranker is not None else get_reranker()

    def _do_rerank() -> list[int]:
        ranked = rk.rerank(query=query, documents=documents, top_k=top_k)
        return [idx for idx, _score in ranked]

    try:
        if timeout_sec is None:
            return _do_rerank()
        # 用线程池实现 best-effort 超时(GPU 任务无法真中断,但主线程能及时回退)
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_do_rerank).result(timeout=timeout_sec)
    except FuturesTimeoutError:
        _logger.warning(
            "reranker timed out after %.1fs, falling back to original order (n=%d)",
            timeout_sec, n,
        )
        return fallback
    except Exception as exc:  # noqa: BLE001 — spec 强制不抛异常
        _logger.warning(
            "reranker failed (%s: %s), falling back to original order (n=%d)",
            type(exc).__name__, exc, n,
        )
        return fallback
