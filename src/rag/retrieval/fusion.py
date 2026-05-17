"""src/rag/retrieval/fusion.py — 单阶段多路 RRF 融合 + 多向量聚合(DEV_SPEC §3.2.2)。

Agent ③ retrieve 节点融合层:
    输入: dense 1 路 + sparse N 路(每路 list[hit],hit 由 search_dense_route /
          search_sparse_routes 产出,字段含 id / source_chunk_id / vector_type /
          original_content / source_id / score)

    输出: list[candidate_chunk] 按 spec §3.2.2 形态:
        {
          "source_chunk_id": str,
          "rrf_score":       float,         # 该 chunk 下所有命中 record 的 1/(k+rank) 之和
          "vector_hits": [                  # 每命中一种 vector_type record 加一条
            {"vector_type": "original" | "summary" | "question",
             "rank":         int,           # 在原召回路中的排名(1-indexed,首路命中 rank)
             "matched_text": str},
            ...
          ],
        }

公式(加权多路 RRF + 多向量 sum-aggregate,spec §3.2.2 + §9.7):
    dense_weight    = max(1, N_sparse_actual / RRF_DENSE_WEIGHT_FACTOR)  # 默认 factor=5
    record_score(r) = dense_weight · 1/(k + rank_dense(r))
                    + Σ_sparse_route 1/(k + rank_sparse(r)),    k=60
    chunk_score(c)  = Σ_record_in_chunk record_score(record)
    截断按 chunk_score 降序取 Top-N(settings.agent_limits.RETRIEVE_TOP_N,默认 200)

RETRIEVAL_EVAL §4 评测发现:sparse 多字段直采后 N_sparse 涨到 12~30(均 21.8),
等权 RRF 下 dense 单路被 sparse 路集体挤兑(Top-20 内 dense exclusive chunks
均 0.45 个/case),加权 N/5 后保留量 ×6.5 至 ~3 个/case。

matched_text 取值规则(spec §3.2.2 行 1822-1825):
    - vector_type='original' → 直接用 hit['original_content'](Milvus 冗余字段)
    - vector_type='summary'  → 回查 PG chunks.summary
    - vector_type='question' → 解析 vector id 后缀 N(`{chunk_id}_q{n}`,见
       config/milvus_schema.py + src/rag/ingestion/embedding.py:112)→
       chunks.hypothetical_questions[N]

PG 回查抽象成 callable 注入(`pg_chunk_lookup`),保持 fusion 纯逻辑可单测。
生产时 F4 retrieve 节点传入真实 PG 查询函数;单测时 mock。
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from config.settings import settings


RRF_K = 60  # 传统 RRF 默认常量(spec §3.2.2 行 1794)


PgChunkLookup = Callable[[Iterable[str]], dict[str, dict]]
"""(chunk_ids) → {chunk_id: {'summary': str|None, 'hypothetical_questions': list[str]|None}}"""


def _parse_question_index(vector_id: str) -> int | None:
    """从 `{chunk_id}_q{n}` 解出 n(0-indexed)。格式不匹配返回 None。"""
    sep = "_q"
    idx = vector_id.rfind(sep)
    if idx < 0:
        return None
    suffix = vector_id[idx + len(sep) :]
    try:
        return int(suffix)
    except ValueError:
        return None


def _matched_text_for_hit(hit: dict, pg_data: dict | None) -> str:
    """按 vector_type 取 matched_text(spec §3.2.2)。

    PG 数据缺失或 question 索引越界 → 返回空字符串(把缺失暴露给上层日志,
    不抛错阻塞融合主流程)。
    """
    vt = hit["vector_type"]
    if vt == "original":
        return hit.get("original_content") or ""
    if vt == "summary":
        return ((pg_data or {}).get("summary")) or ""
    if vt == "question":
        n = _parse_question_index(hit["id"])
        if n is None or pg_data is None:
            return ""
        questions = pg_data.get("hypothetical_questions") or []
        return questions[n] if 0 <= n < len(questions) else ""
    return ""


def fuse_routes(
    dense_route: list[dict],
    sparse_routes: list[list[dict]],
    top_n: int | None = None,
    rrf_k: int = RRF_K,
    pg_chunk_lookup: PgChunkLookup | None = None,
) -> list[dict]:
    """主入口:多路 RRF + Top-N 截断 + 多向量聚合 + matched_text 填充。

    Args:
        dense_route: search_dense_route 输出(可空 list)
        sparse_routes: search_sparse_routes 输出(N 路,空路径会被跳过)
        top_n: 截断阈值,None 时取 settings.agent_limits.RETRIEVE_TOP_N
        rrf_k: RRF 公式常量,默认 60
        pg_chunk_lookup: PG 批量回查 callable;None 时 summary/question 的
                         matched_text 留空字符串(单测专用)

    Returns:
        list[candidate_chunk],按 rrf_score 降序;长度 ≤ top_n;
        每项形态见模块 docstring。
    """
    if top_n is None:
        top_n = settings.agent_limits.RETRIEVE_TOP_N

    # ─── Step 1: 加权多路 RRF — dense 加权,sparse 每路等权(spec §3.2.2)───
    # 同一 record_id 在多路出现 → 分数累加;rank 取首路命中(确定性,可重现)
    record_score: dict[str, float] = {}
    record_hit: dict[str, dict] = {}
    record_first_rank: dict[str, int] = {}

    n_sparse_actual = sum(1 for r in sparse_routes if r)
    factor = settings.agent_limits.RRF_DENSE_WEIGHT_FACTOR
    dense_weight = max(1.0, n_sparse_actual / factor) if factor > 0 else 1.0

    def _accumulate(route: list[dict], weight: float) -> None:
        for rank, hit in enumerate(route, start=1):
            rid = hit["id"]
            record_score[rid] = record_score.get(rid, 0.0) + weight / (rrf_k + rank)
            if rid not in record_hit:
                record_hit[rid] = hit
                record_first_rank[rid] = rank

    if dense_route:
        _accumulate(dense_route, dense_weight)
    for route in sparse_routes:
        if route:
            _accumulate(route, 1.0)

    if not record_score:
        return []

    # ─── Step 2: 按 source_chunk_id 聚合 — 同 chunk 下不同 vector record 的分数求和 ───
    chunk_score: dict[str, float] = {}
    chunk_record_ids: dict[str, list[str]] = {}
    for rid, score in record_score.items():
        cid = record_hit[rid]["source_chunk_id"]
        chunk_score[cid] = chunk_score.get(cid, 0.0) + score
        chunk_record_ids.setdefault(cid, []).append(rid)

    # ─── Step 3: Top-N 截断 — 按 chunk-level 分数降序;同分按 source_chunk_id 字母序(确定性) ───
    sorted_chunk_ids = sorted(
        chunk_score.keys(),
        key=lambda c: (-chunk_score[c], c),
    )[:top_n]

    # ─── Step 4: PG 批量回查(仅截断后存活、且需要 summary/question 文本的 chunk) ───
    pg_data_by_chunk: dict[str, dict] = {}
    if pg_chunk_lookup is not None:
        chunks_needing_pg = {
            cid for cid in sorted_chunk_ids
            if any(record_hit[rid]["vector_type"] != "original" for rid in chunk_record_ids[cid])
        }
        if chunks_needing_pg:
            pg_data_by_chunk = pg_chunk_lookup(chunks_needing_pg)

    # ─── Step 5: 装配输出(vector_hits 按 first-seen rank 升序,确定性) ───
    out: list[dict] = []
    for cid in sorted_chunk_ids:
        rids = sorted(chunk_record_ids[cid], key=lambda r: record_first_rank[r])
        vector_hits = [
            {
                "vector_type": record_hit[rid]["vector_type"],
                "rank": record_first_rank[rid],
                "matched_text": _matched_text_for_hit(record_hit[rid], pg_data_by_chunk.get(cid)),
            }
            for rid in rids
        ]
        out.append({
            "source_chunk_id": cid,
            "rrf_score": chunk_score[cid],
            "vector_hits": vector_hits,
        })
    return out
