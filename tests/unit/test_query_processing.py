"""tests/unit/test_query_processing.py — DEV_SPEC §3.2.1 Step 2 工具函数单测。

目标:用 monkeypatch 替换 `query_aliases_by_concept_id` 的数据源,验证
expand_aliases / build_sparse_query_bag / build_sparse_queries 三个纯函数:
- 多 concept_id 别名合并 + 去重
- 长度 ≤ 1 的过短别名被丢弃(spec 防泛化匹配规则)
- 词袋以空格拼接 + 字母序确定性
- 空词袋(过滤后无别名 / concept_id 不存在)在 build_sparse_queries 入口被跳过
"""
from __future__ import annotations

import pytest

import src.rag.retrieval.query_processing as qp


@pytest.fixture
def fake_terms(monkeypatch):
    """注入一个内存版 concept_id → aliases 字典,替换真 Milvus 查询。"""
    table: dict[str, list[str]] = {
        "R10.4": ["腹痛", "肚子疼", "胃痛", "腹部疼痛", "abdominal pain", "痛"],
        "R50": ["发热", "发烧", "体温升高", "fever"],
        "R50.0": ["寒战发热", "发热"],  # "发热" 与 R50 重复,验证去重
        "R11": ["呕吐", "想吐", "吐"],  # "吐" 单字应被过滤
        "EMPTY_AFTER_FILTER": ["A", "B", "痛"],  # 过滤后全空
        "MISSING_ID": [],  # 模拟 concept_id 不存在
    }

    def _fake_query(concept_id: str) -> list[str]:
        return sorted(table.get(concept_id, []))

    monkeypatch.setattr(qp, "query_aliases_by_concept_id", _fake_query)
    return table


# ───────────────────────────────────────────────────────────
# expand_aliases
# ───────────────────────────────────────────────────────────


def test_expand_aliases_single_concept_filters_short_alias(fake_terms) -> None:
    """单个 concept_id:返回全部别名,长度 ≤ 1 的"痛"被过滤,字母序排序。"""
    out = qp.expand_aliases(["R10.4"])
    assert "痛" not in out, "长度 1 的'痛'应被丢弃(防泛化匹配)"
    assert "腹痛" in out and "abdominal pain" in out
    assert out == sorted(out), "返回必须按字母序(确定性)"


def test_expand_aliases_dedup_across_concepts(fake_terms) -> None:
    """两个 concept_id 共享别名'发热',合并后只保留一份。"""
    out = qp.expand_aliases(["R50", "R50.0"])
    assert out.count("发热") == 1, "共享别名'发热'应被去重"
    assert "fever" in out and "寒战发热" in out


def test_expand_aliases_filters_single_char_alias(fake_terms) -> None:
    """R11 的'吐'是单字,必须被过滤;'呕吐''想吐'保留。"""
    out = qp.expand_aliases(["R11"])
    assert "吐" not in out
    assert "呕吐" in out
    assert "想吐" in out


def test_expand_aliases_empty_input(fake_terms) -> None:
    """空 concept_id 列表 → 返回空列表,不报错。"""
    assert qp.expand_aliases([]) == []


def test_expand_aliases_unknown_concept_id(fake_terms) -> None:
    """不存在的 concept_id → 不抛错,返回 []。"""
    assert qp.expand_aliases(["MISSING_ID"]) == []


def test_expand_aliases_all_filtered_returns_empty(fake_terms) -> None:
    """所有别名长度 ≤ 1 → 返回 []。"""
    assert qp.expand_aliases(["EMPTY_AFTER_FILTER"]) == []


# ───────────────────────────────────────────────────────────
# build_sparse_query_bag
# ───────────────────────────────────────────────────────────


def test_build_sparse_query_bag_joins_with_space(fake_terms) -> None:
    """词袋用空格拼接(BM25 中文 analyzer 切词的输入形态)。"""
    bag = qp.build_sparse_query_bag(["R11"])
    parts = bag.split(" ")
    assert "呕吐" in parts and "想吐" in parts
    assert "吐" not in parts  # 单字过滤
    # 全部 token 都非空
    assert all(p for p in parts)


def test_build_sparse_query_bag_empty_when_all_filtered(fake_terms) -> None:
    """全过滤后返回空字符串,不抛错。"""
    assert qp.build_sparse_query_bag(["EMPTY_AFTER_FILTER"]) == ""


def test_build_sparse_query_bag_empty_input(fake_terms) -> None:
    """空 concept_id 列表 → 空字符串。"""
    assert qp.build_sparse_query_bag([]) == ""


def test_build_sparse_query_bag_unknown_concept(fake_terms) -> None:
    """不存在的 concept_id → 空字符串。"""
    assert qp.build_sparse_query_bag(["MISSING_ID"]) == ""


# ───────────────────────────────────────────────────────────
# build_sparse_queries (主入口)
# ───────────────────────────────────────────────────────────


def test_build_sparse_queries_one_bag_per_dimension(fake_terms) -> None:
    """每个维度一个词袋,返回 list[str] 长度 = 维度数(无空词袋时)。"""
    out = qp.build_sparse_queries([["R10.4"], ["R50"], ["R11"]])
    assert len(out) == 3
    # 每项都是非空字符串
    assert all(item for item in out)
    # 验证粗略内容(具体顺序由字母序决定,这里只验证关键词出现)
    assert any("腹痛" in item for item in out)
    assert any("发热" in item for item in out)
    assert any("呕吐" in item for item in out)


def test_build_sparse_queries_skips_empty_bag(fake_terms) -> None:
    """中间维度词袋空了 → 自动跳过,sparse_queries 项项非空。"""
    out = qp.build_sparse_queries([
        ["R10.4"],
        ["EMPTY_AFTER_FILTER"],  # 这一维度应被跳过
        ["R11"],
    ])
    assert len(out) == 2  # 跳过 1 项
    assert all(item for item in out)
    assert any("腹痛" in item for item in out)
    assert any("呕吐" in item for item in out)


def test_build_sparse_queries_all_empty_returns_empty_list(fake_terms) -> None:
    """所有维度都空 → 返回 [];不抛错(责任在上层 build_query 节点决策)。"""
    out = qp.build_sparse_queries([
        ["EMPTY_AFTER_FILTER"],
        ["MISSING_ID"],
        [],
    ])
    assert out == []


def test_build_sparse_queries_multi_concept_per_dimension(fake_terms) -> None:
    """单个维度内多个 concept_id (如同概念多 ICD 码),别名合并去重为一个词袋。"""
    out = qp.build_sparse_queries([["R50", "R50.0"]])
    assert len(out) == 1
    bag = out[0]
    parts = bag.split(" ")
    # 共享别名"发热"只出现一次
    assert parts.count("发热") == 1
    assert "fever" in parts
    assert "寒战发热" in parts


def test_build_sparse_queries_empty_input(fake_terms) -> None:
    """空输入 → 空列表。"""
    assert qp.build_sparse_queries([]) == []
