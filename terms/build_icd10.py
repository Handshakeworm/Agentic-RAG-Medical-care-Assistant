"""terms/build_icd10.py — Layer 2(ICD-10 北京临床版)灌 terms_collection。

读 4w 行 (ICD code, 诊断名) → 每行一条 alias 记录(alias = 诊断名自身,既是
preferred_term 也是 alias 文本)→ upsert 到 Milvus terms_collection。

设计:
- concept_id = ICD 编码(权威主键)
- 同 preferred_term 跨次或跨源灌库会归到同一 concept_id
- category 按 ICD 段粗分:R 段 → symptom,其余 → disease
  (Z/V/W/X/Y 等小众段先归 disease,后期可补细分)

幂等:
- record id = {icd_code}_{SHA256(alias)[:16]}
- upsert 同 id 自动覆盖,重跑不重复
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

import pandas as pd
from sentence_transformers import SentenceTransformer
from transformers import BitsAndBytesConfig

from src.db.milvus.terms_collection import (
    count_aliases,
    ensure_terms_collection,
    upsert_aliases,
)


def _make_record_id(concept_id: str, alias: str) -> str:
    alias_hash = hashlib.sha256(alias.encode("utf-8")).hexdigest()[:16]
    return f"{concept_id}_{alias_hash}"


def load_icd10(xlsx_path: Path) -> pd.DataFrame:
    """读 Excel + 清洗。返回去重后的 (icd10, name) DataFrame。"""
    df = pd.read_excel(xlsx_path, header=None, names=["icd10", "name"])
    df = df.dropna(subset=["icd10", "name"])
    df["icd10"] = df["icd10"].astype(str).str.strip()
    df["name"] = df["name"].astype(str).str.strip()
    df = df[(df["icd10"] != "") & (df["name"] != "")]
    return df.drop_duplicates(subset=["icd10"]).reset_index(drop=True)


def categorize(icd_code: str) -> str:
    """ICD 段 → schema.category 枚举。R 段为症状码,其他先归 disease。"""
    if not icd_code:
        return "disease"
    return "symptom" if icd_code[0].upper() == "R" else "disease"


def build_records(df: pd.DataFrame) -> list[dict]:
    records = []
    for icd, name in zip(df["icd10"], df["name"], strict=True):
        records.append({
            "id": _make_record_id(icd, name),
            "concept_id": icd,
            "preferred_term": name,
            "alias": name,
            "source_vocab": "ICD10CN",
            "icd10": icd,
            "category": categorize(icd),
        })
    return records


def load_embedding_model() -> SentenceTransformer:
    bnb = BitsAndBytesConfig(load_in_8bit=True)
    return SentenceTransformer(
        os.environ["EMBEDDING_MODEL_PATH"],
        model_kwargs={"quantization_config": bnb, "device_map": "auto"},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="灌 ICD-10 北京临床版到 Milvus terms_collection")
    parser.add_argument("--icd-xlsx", type=Path, required=True, help="北京临床版 .xlsx 路径")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 行(冒烟测试用)")
    args = parser.parse_args()

    print("=== 1. 确保 collection ===")
    ensure_terms_collection()
    print(f"灌库前 entities: {count_aliases()}")

    print(f"\n=== 2. 读 ICD-10: {args.icd_xlsx} ===")
    df = load_icd10(args.icd_xlsx)
    if args.limit:
        df = df.head(args.limit)
    print(f"清洗后 ICD 行数: {len(df)}")

    print("\n=== 3. 构造 alias 记录 ===")
    records = build_records(df)
    print(f"记录数: {len(records)}")

    # 类目分布(看一眼 R 段症状码占比)
    from collections import Counter
    print("category 分布:", dict(Counter(r["category"] for r in records)))

    print("\n=== 4. 加载 Embedding(8bit) ===")
    model = load_embedding_model()

    print("\n=== 5. 批量 Embedding ===")
    aliases = [r["alias"] for r in records]
    vectors = model.encode(
        aliases,
        batch_size=args.batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
    ).tolist()
    for r, v in zip(records, vectors, strict=True):
        r["dense_vector"] = v
    print(f"已编码 {len(vectors)} 条向量,维度 {len(vectors[0])}")

    print("\n=== 6. Upsert 到 Milvus ===")
    total = 0
    for i in range(0, len(records), args.batch_size):
        batch = records[i : i + args.batch_size]
        n = upsert_aliases(batch)
        total += n
        print(f"  batch {i // args.batch_size + 1}: +{n}(累计 {total})")

    print(f"\n=== 7. 完成 ===")
    print(f"灌库后 entities: {count_aliases()}")


if __name__ == "__main__":
    main()
