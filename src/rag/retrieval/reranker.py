import os
from functools import lru_cache

from FlagEmbedding import LayerWiseFlagLLMReranker


_DEFAULT_CUTOFF_LAYER = 28


class Reranker:
    def __init__(
        self,
        model_path: str | None = None,
        device: str | None = None,
        use_fp16: bool = True,
        cutoff_layer: int | None = None,
    ):
        self.model_path = model_path or os.environ["RERANKER_MODEL_PATH"]
        self.device = device or os.getenv("RERANKER_DEVICE", "cuda")
        self.use_fp16 = use_fp16 and self.device == "cuda"
        self.cutoff_layer = cutoff_layer or int(
            os.getenv("RERANKER_CUTOFF_LAYER", _DEFAULT_CUTOFF_LAYER)
        )
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
        scores = self.model.compute_score(pairs, cutoff_layers=[self.cutoff_layer])

        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        if top_k is not None:
            ranked = ranked[:top_k]
        return ranked


@lru_cache(maxsize=1)
def get_reranker() -> Reranker:
    return Reranker()
