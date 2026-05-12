"""src/rag/retrieval/query_processing.py — Sparse 路查询预处理工具(DEV_SPEC §3.2.1 Step 2)。

确定性的"术语扩展(Synonym Expansion) → 词袋拼接"工具集,供 Agent ② `build_query`
节点构造 `sparse_queries`(list[str])使用。

流程(spec §3.2.1 Step 2):
    给定 concept_id 列表
        → 从 terms_collection 拉全部别名(含口语 / 缩写 / 英文)
        → 过滤长度 ≤ 1 的过短别名(防泛化匹配,例:单字"痛"会命中所有疼痛 chunk)
        → 拼成空格分隔的词袋字符串(Milvus BM25 中文 analyzer 自切词)
        → 每个症状维度一项,组装成 sparse_queries

LLM 调用与 prompt 由 ② build_query 节点持有(`src/prompts/agent.py` 的
`build_query_construction_prompt`);本模块只暴露**确定性工具函数**,不调 LLM。

边界处理:某维度别名全 ≤ 1 字符或 concept_id 不存在 → 词袋为空字符串,
`build_sparse_queries` 自动跳过空词袋,保证最终 sparse_queries 项项非空有效。
"""
from __future__ import annotations

from src.db.milvus.terms_collection import query_aliases_by_concept_id


_MIN_ALIAS_LENGTH = 2


def expand_aliases(concept_ids: list[str]) -> list[str]:
    """合并多个 concept_id 的别名,去重 + 过滤过短别名,按字母序返回。

    同一别名出现在多个 concept_id 下只保留一份(set 去重);
    长度 < `_MIN_ALIAS_LENGTH`(= 2)的过短别名按 spec §3.2.1 Step 2 规则丢弃。
    """
    seen: set[str] = set()
    for cid in concept_ids:
        for alias in query_aliases_by_concept_id(cid):
            if len(alias) >= _MIN_ALIAS_LENGTH:
                seen.add(alias)
    return sorted(seen)


def build_sparse_query_bag(concept_ids: list[str]) -> str:
    """单个症状维度 → 一个 BM25 词袋字符串(空格分隔)。

    若所有别名都被过滤掉或 concept_id 全部不存在 → 返回空字符串。
    上层(`build_sparse_queries` 或 ② build_query 节点)按需丢弃空词袋。
    """
    return " ".join(expand_aliases(concept_ids))


def build_sparse_queries(grouped_concept_ids: list[list[str]]) -> list[str]:
    """主入口:把"按症状维度分组的 concept_id 列表"转成 sparse_queries。

    输入示例:[["R10.4"], ["R50", "R50.0"], ["R11"]]
              ↑ 腹痛维度  ↑ 发热维度(同概念多 ICD 码) ↑ 呕吐维度

    输出形态(spec §4.1.1 例):
        ["腹痛 肚子疼 胃痛 abdominal pain", "发热 发烧 体温升高", "呕吐 想吐"]

    空词袋(该维度别名全被过滤或概念不存在)自动跳过 —— 项项非空,下游 ③ retrieve
    节点不必再做 truthy 过滤即可逐项喂 BM25。
    """
    out: list[str] = []
    for cids in grouped_concept_ids:
        bag = build_sparse_query_bag(cids)
        if bag:
            out.append(bag)
    return out
