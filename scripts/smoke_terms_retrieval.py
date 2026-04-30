"""scripts/smoke_terms_retrieval.py — terms_collection 向量召回冒烟测试。

用 Qwen3-Embedding-8B(8bit)对一组典型患者口语 query 做编码,
在 terms_collection 里检索 Top-5,人工肉眼验证是否命中预期标准术语。

运行:
    python -m scripts.smoke_terms_retrieval
"""

from __future__ import annotations

import os

from sentence_transformers import SentenceTransformer
from transformers import BitsAndBytesConfig

from src.db.milvus.terms_collection import count_aliases, search_aliases


# (query, 期望命中的 preferred_term 关键词或 ICD 段)— 人工对照用
QUERIES: list[tuple[str, str]] = [
    ("肚子疼",            "腹痛 R10"),
    ("肚子痛",            "腹痛 R10"),
    ("发烧",              "发热 R50"),
    ("咳嗽",              "咳嗽 R05"),
    ("胸闷",              "胸闷 / 胸痛 R07"),
    ("头晕",              "头晕 / 眩晕 R42"),
    ("呕吐",              "呕吐 R11"),
    ("腰疼",              "腰背痛 M54 / 肾区疼痛"),
    ("心慌",              "心悸 R00"),
    ("右下腹突然剧痛",     "急性阑尾炎 K35 / 右下腹痛"),
    ("尿频尿急",          "尿路感染 N39 / 排尿异常 R39"),
]


def load_model() -> SentenceTransformer:
    bnb = BitsAndBytesConfig(load_in_8bit=True)
    return SentenceTransformer(
        os.environ["EMBEDDING_MODEL_PATH"],
        model_kwargs={"quantization_config": bnb, "device_map": "auto"},
    )


def main() -> None:
    print(f"=== terms_collection 当前 entities: {count_aliases()} ===")

    print("\n=== 加载 Embedding(8bit) ===")
    model = load_model()
    print("✓ 模型加载完成")

    for q, expected in QUERIES:
        print(f"\n─── Query: 「{q}」(期望: {expected}) ───")
        vec = model.encode(q, convert_to_numpy=True).tolist()
        results = search_aliases(vec, top_k=5)
        for i, r in enumerate(results, 1):
            print(
                f"  {i}. score={r['score']:.4f}  "
                f"concept={r['concept_id']:12}  "
                f"preferred=「{r['preferred_term']}」  "
                f"alias=「{r['alias']}」"
            )


if __name__ == "__main__":
    main()
