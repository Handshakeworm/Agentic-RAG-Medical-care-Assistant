"""C5 — PG chunks 行 → Milvus 多向量记录(DEV_SPEC §3.1.5)。

每行 PG chunk 产 1 `original` + 0-1 `summary` + 0-3 `question` 条 Milvus 记录:

| vector_type | id              | dense 输入            | text_for_bm25     |
|-------------|-----------------|-----------------------|-------------------|
| original    | {chunk_id}      | title + body          | chunk_raw_text    |
| summary     | {chunk_id}_summary | summary 字段          | ""                |
| question    | {chunk_id}_q{n} | hypothetical_questions[n] | ""            |

`body` 按 chunk_type 选取(§3.1.5):
- child         → chunk_raw_text
- table/figure  → medical_statement(图表的 dense `original` 来源,因 caption/html 作 dense 表达力不足)

`parent`(embedding_status='skip')不参与本步骤,调用方需在 SELECT 时过滤掉。

批处理策略:扁平化所有待 embed 文本 → 一次 GPU encode → 回填 dense_vector。
比"逐 chunk 调 encode"GPU 利用率高一个数量级。
"""

from __future__ import annotations

from typing import Any

from src.models.embedding_model import get_embedding_model


def _dense_input_for_original(chunk: dict[str, Any]) -> str:
    """组装 original 向量的输入文本:title 前缀 + body(按 chunk_type 分流)。"""
    chunk_type = chunk["chunk_type"]
    if chunk_type == "child":
        body = chunk["chunk_raw_text"]
    elif chunk_type in {"table", "figure"}:
        body = chunk.get("medical_statement")
        if not body:
            raise ValueError(
                f"{chunk_type} chunk {chunk['chunk_id']} 缺 medical_statement,"
                f"无法生成 dense `original` 向量(§3.1.5)"
            )
    else:
        raise ValueError(
            f"chunk_type {chunk_type!r} 不参与 embedding"
            f"(parent 应 skip,其他类型未在 §3.1.5 定义)"
        )
    title = (chunk.get("title") or "").strip()
    return f"{title}\n{body}" if title else body


def build_milvus_records(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量把 PG chunks 行扩展为 Milvus 上 upsert 记录。

    输入字段约定(对应 PG chunks 表列名):
        chunk_id, source_id, chunk_type, chunk_raw_text, medical_statement,
        title, summary, hypothetical_questions

    输出:每条 dict 包含 docs_collection 7 业务字段
    (`bm25_sparse` 由 Milvus Function 自动派生,不在这里写)。
    """
    if not chunks:
        return []

    model = get_embedding_model()

    # 第 1 步:扁平化所有待 embed 文本,与 record skeleton 配对
    payloads: list[tuple[str, dict[str, Any]]] = []
    for c in chunks:
        chunk_id = c["chunk_id"]
        source_id = c["source_id"]
        raw_text = c["chunk_raw_text"]

        # original 记录(每条 chunk 必产 1 条)
        payloads.append(
            (
                _dense_input_for_original(c),
                {
                    "id": chunk_id,
                    "source_chunk_id": chunk_id,
                    "vector_type": "original",
                    "text_for_bm25": raw_text,
                    "original_content": raw_text,
                    "source_id": source_id,
                },
            )
        )

        # summary 记录(若 LLM 生成了 summary 才产)
        summary = (c.get("summary") or "").strip()
        if summary:
            payloads.append(
                (
                    summary,
                    {
                        "id": f"{chunk_id}_summary",
                        "source_chunk_id": chunk_id,
                        "vector_type": "summary",
                        "text_for_bm25": "",
                        "original_content": raw_text,
                        "source_id": source_id,
                    },
                )
            )

        # question_0..n 记录(每条非空 hypothetical_question 产 1 条)
        for i, q in enumerate(c.get("hypothetical_questions") or []):
            q_clean = (q or "").strip()
            if not q_clean:
                continue
            payloads.append(
                (
                    q_clean,
                    {
                        "id": f"{chunk_id}_q{i}",
                        "source_chunk_id": chunk_id,
                        "vector_type": "question",
                        "text_for_bm25": "",
                        "original_content": raw_text,
                        "source_id": source_id,
                    },
                )
            )

    # 第 2 步:一次 GPU encode 全部文本
    texts = [t for t, _ in payloads]
    vectors = model.encode(texts, show_progress_bar=False)

    # 第 3 步:dense_vector 回填
    records: list[dict[str, Any]] = []
    for vec, (_, skeleton) in zip(vectors, payloads, strict=True):
        skeleton["dense_vector"] = vec
        records.append(skeleton)
    return records
