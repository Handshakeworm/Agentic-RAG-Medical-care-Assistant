"""权重下载完成后跑一次即可验证：模型可加载、layerwise 切层生效、分数符合预期。"""

import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.path.isdir(os.getenv("RERANKER_MODEL_PATH", "")),
    reason="RERANKER_MODEL_PATH 未指向已下载的权重目录",
)


def test_reranker_layerwise_ranking():
    from src.rag.retrieval.reranker import Reranker

    reranker = Reranker(cutoff_layer=28)
    query = "糖尿病患者的饮食注意事项"
    documents = [
        "糖尿病患者应控制碳水化合物摄入，避免高糖食物。",
        "高血压的治疗首选钙通道阻滞剂。",
        "急性阑尾炎常表现为转移性右下腹痛。",
    ]

    ranked = reranker.rerank(query, documents, top_k=2)

    assert len(ranked) == 2
    assert ranked[0][0] == 0, "最相关的文档应排第一"
    assert ranked[0][1] > ranked[1][1], "分数应递减"
