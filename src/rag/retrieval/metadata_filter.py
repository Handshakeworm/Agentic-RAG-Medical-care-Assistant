"""src/rag/retrieval/metadata_filter.py — Post-filter 元数据过滤框架(DEV_SPEC §3.2.3)。

Pre-filter 已在 E2/E3 接口里通过 `source_id_filter` 参数透传到底层 Milvus
(`docs_collection.search_dense` / `search_sparse_bm25`),覆盖 spec §3.2.3
"硬约束在底层索引提前过滤"的部分。

本模块实现 spec §3.2.3 的 **post-filter 框架**:在 RRF 融合后、Reranker 前
对 candidate_chunks 做兜底过滤,处理"底层索引不支持"或"字段质量不稳"的
过滤维度(如 doc_type / language / time_range / access_level 等)。

强约束(spec §3.2.3 行 1838):
- "字段缺失时默认宽松包含(missing → include),避免误杀召回"
- 实现:predicate 返回 `None` 或抛 KeyError/AttributeError/TypeError → 视为通过

设计:不强行规定 predicate 套件,只提供框架函数 + 1 个常用 factory(source_id
allowlist)。具体过滤维度由 ④/⑩ 节点按业务需要组装 predicate list。
"""
from __future__ import annotations

from collections.abc import Callable, Iterable


CandidatePredicate = Callable[[dict], bool | None]
"""返回 True/None → 保留;返回 False → 剔除;抛 KeyError/AttributeError/TypeError → 视为 None。"""


def apply_post_filters(
    candidates: list[dict],
    predicates: list[CandidatePredicate] | None = None,
) -> list[dict]:
    """对 candidate_chunks 应用一组 predicate,全部通过才保留。

    Args:
        candidates: §3.2.2 形态的 candidate_chunks 列表
        predicates: predicate 列表;每个签名 `(chunk: dict) -> bool | None`。
            None / 空列表 → 透传(不过滤)

    Returns:
        过滤后的 candidates 列表(顺序保留)。

    spec §3.2.3 强约束 - 宽松策略实现:
    - predicate 返回 True → 保留
    - predicate 返回 None → 视为缺失,保留
    - predicate 抛 KeyError/AttributeError/TypeError → 视为字段缺失,保留
    - predicate 返回 False → 剔除
    - predicate 抛其他异常 → 让其冒泡(逻辑错误,不该静默)
    """
    if not predicates:
        return list(candidates)

    out: list[dict] = []
    for cand in candidates:
        keep = True
        for pred in predicates:
            try:
                result = pred(cand)
            except (KeyError, AttributeError, TypeError):
                # spec §3.2.3 宽松策略:字段缺失视为通过
                continue
            if result is False:
                keep = False
                break
        if keep:
            out.append(cand)
    return out


def source_id_in_allowlist(
    allowed: Iterable[str],
    field: str = "source_id",
) -> CandidatePredicate:
    """factory:返回一个判断 chunk[field] 是否在 allowed 集合内的 predicate。

    用于 candidate 形态里携带了 source_id 字段时的 post-filter(默认 spec §3.2.2
    candidate 形态不含 source_id,需要 ④/⑩ 节点先回查 PG 后再用本 predicate;
    或者直接用 E2/E3 的 pre-filter)。

    `field 不存在` → predicate 返回 None(宽松保留);`field 存在但不在 allowed`
    → 返回 False(剔除)。
    """
    allowed_set = set(allowed)

    def _pred(cand: dict) -> bool | None:
        if field not in cand:
            return None  # missing → include
        return cand[field] in allowed_set

    return _pred
