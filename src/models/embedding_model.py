"""Qwen3-Embedding-8B 客户端(DEV_SPEC §2.1 / §8.4 B6)。

INT8(bitsandbytes 8bit)量化加载到 GPU,显存 ~9.3GB,与 Reranker 共享 RTX 5070 Ti 16GB。
进程内 singleton:8B 权重 9GB,重复加载会 OOM。

接口:
    >>> model = get_embedding_model()
    >>> vec = model.encode_one("腹痛")              # → list[float] 长度 4096
    >>> vecs = model.encode(["腹痛", "头晕"])       # → list[list[float]]

性能记录(2026-05 D2 灌 4 万 ICD-10 实测):
- batch=256 短文本(<20 字):全量 4 万条约 3 分钟,显存峰值 9.3GB
- batch=16 长文本(200-800 字):预估 13 万条约 1-2 小时
"""

from __future__ import annotations

import os
import threading

from sentence_transformers import SentenceTransformer
from transformers import BitsAndBytesConfig


EMBEDDING_DIM = 4096


class EmbeddingModel:
    """Qwen3-Embedding-8B 单条/批量编码客户端。

    封装 sentence-transformers + bitsandbytes 8bit 加载链路,
    业务代码不直接接触 SentenceTransformer / BitsAndBytesConfig。
    """

    DEFAULT_BATCH = 8  # 8B INT8 + 长文本 4096 维输出在 16GB 卡的稳态值
    # 2026-05-11 实测:batch=16 时显存 15559 MiB,~7/85 batch 触发 CUDA OOM
    # (碎片化 + 偶遇超长 chunk → 试图申请 1 GiB 但只剩 ~840 MiB)。
    # 改 8 后 peak activation 减半,显存降到 ~12-13 GiB,杜绝 OOM。

    def __init__(self, model_path: str | None = None) -> None:
        path = model_path or os.environ["EMBEDDING_MODEL_PATH"]
        bnb = BitsAndBytesConfig(load_in_8bit=True)
        self._model = SentenceTransformer(
            path,
            model_kwargs={"quantization_config": bnb, "device_map": "auto"},
        )

    def encode(
        self,
        texts: list[str],
        batch_size: int | None = None,
        show_progress_bar: bool = False,
    ) -> list[list[float]]:
        """批量编码。空 list 返回空 list,不发起 GPU 调用。"""
        if not texts:
            return []
        vectors = self._model.encode(
            texts,
            batch_size=batch_size or self.DEFAULT_BATCH,
            show_progress_bar=show_progress_bar,
            convert_to_numpy=True,
        )
        return vectors.tolist()

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]


_singleton: EmbeddingModel | None = None
_lock = threading.Lock()


def get_embedding_model() -> EmbeddingModel:
    """进程内单例。双检锁避免并发场景下重复加载 8B 权重导致 OOM。"""
    global _singleton
    if _singleton is None:
        with _lock:
            if _singleton is None:
                _singleton = EmbeddingModel()
    return _singleton
