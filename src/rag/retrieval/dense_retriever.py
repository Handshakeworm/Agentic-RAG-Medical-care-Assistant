"""src/rag/retrieval/dense_retriever.py — Dense Route 单次 ANN 召回(DEV_SPEC §3.2.2 Dense Route)。

Agent ③ retrieve 节点 Dense 路入口:接收 ② build_query 产出的 `dense_query`
(LLM 整合改写后的语义连贯自然语言句子) → Qwen3-Embedding-8B 编码 → Milvus
ANN(COSINE) → 返回 Top-K 候选(单路结果),交由 ④ RRF 融合。

覆盖全 vector_type:original / summary / question 三类记录均参与 ANN 召回
(spec §3.2.2 行 1785),不做 vector_type 过滤。

底层 `docs_collection.search_dense` 已封装单次 ANN 检索;`get_embedding_model`
是进程内 singleton,首次调用加载 9.3GB 模型,后续复用。

**spec gap 备案**(同 E2):§8.3 E3 验收说"返回 Top-N",未明示 N 的具体值;
默认 `top_k = settings.agent_limits.RETRIEVE_TOP_N`(=200),与 RRF 融合后截断
名额一致。
"""
from __future__ import annotations

from config.settings import settings
from src.db.milvus.docs_collection import search_dense
from src.models.embedding_model import get_embedding_model


def search_dense_route(
    dense_query: str,
    top_k: int | None = None,
    source_id_filter: str | None = None,
) -> list[dict]:
    """对 `dense_query` 单次编码 + ANN 检索,返回 Top-K 候选。

    Args:
        dense_query: ② build_query Step 4 产出的语义查询句(spec §3.2.1 Step 3 例
            "进食后加重的上腹胀痛伴反酸,白细胞升高,既往糖尿病史")
        top_k: 召回 Top-K 数。None 时取 `settings.agent_limits.RETRIEVE_TOP_N`
        source_id_filter: 可选 pre-filter,按来源文档过滤(对接 E6)

    Returns:
        list[dict]:Top-K hit,每项含 id / source_chunk_id / vector_type /
        original_content / source_id / score(COSINE 相似度,见
        `docs_collection.search_dense` 形态)。
    """
    if top_k is None:
        top_k = settings.agent_limits.RETRIEVE_TOP_N
    model = get_embedding_model()
    query_vector = model.encode_one(dense_query)
    return search_dense(
        query_vector=query_vector,
        top_k=top_k,
        source_id_filter=source_id_filter,
    )
