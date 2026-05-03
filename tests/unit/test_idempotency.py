"""tests/unit/test_idempotency.py — 锁住 C3 ID 生成规则与 DEV_SPEC §3.1.4 一致。

C3 是无 IO 纯函数库,所有测试都是确定性 input → output 断言,无需起任何外部服务。
"""

from __future__ import annotations

import hashlib

import pytest

from src.rag.ingestion.idempotency import (
    compute_chunk_id,
    compute_content_hash,
    compute_heading_path_id,
    compute_parent_chunk_id,
    compute_source_id,
    normalize,
)


# ───────────────────────── normalize(spec §3.1.4.2)─────────────────────────


def test_normalize_full_to_half_width() -> None:
    """全角字母/数字/空格转半角(spec §3.1.4.2 步骤 2)。"""
    assert normalize("ＡＢＣ") == "abc"
    assert normalize("１２３") == "123"
    assert normalize("Ａ Ｂ") == "a b"


def test_normalize_lowercases_latin() -> None:
    assert normalize("Diagnosis") == "diagnosis"
    assert normalize("ABC123") == "abc123"


def test_normalize_strips_and_collapses_whitespace() -> None:
    """首尾空白去除 + 内部连续空白压缩成单空格。"""
    assert normalize("  hello   world  ") == "hello world"
    assert normalize("a\t\tb\n c") == "a b c"


def test_normalize_keeps_chinese_intact() -> None:
    """中文字符不改动(NFC 已规范),标点保留(spec 设计说明)。"""
    assert normalize("第一章 常见症状") == "第一章 常见症状"
    assert normalize("发热(Fever):定义") == "发热(fever):定义"


def test_normalize_nfc_combines_unicode() -> None:
    """NFC 把组合字符合并(é 的两种 Unicode 表示规范成同一种)。"""
    composed = "é"  # U+00E9
    decomposed = "é"  # U+0065 + U+0301
    assert normalize(composed) == normalize(decomposed)


def test_normalize_is_idempotent() -> None:
    """normalize(normalize(x)) == normalize(x)。"""
    samples = ["Ａ b　 C", "  HELLO  ", "第一章　发热"]
    for s in samples:
        assert normalize(normalize(s)) == normalize(s)


# ───────────────────── source_id(spec §3.1.4.1)─────────────────────────────


def test_source_id_is_16_hex_chars() -> None:
    sid = compute_source_id("诊断学.pdf")
    assert len(sid) == 16
    assert all(c in "0123456789abcdef" for c in sid)


def test_source_id_strips_extension() -> None:
    """扩展名不影响:.pdf / .md / .docx 应得同一个 source_id。"""
    a = compute_source_id("诊断学.pdf")
    b = compute_source_id("诊断学.md")
    c = compute_source_id("诊断学.docx")
    assert a == b == c


def test_source_id_is_normalized_robust() -> None:
    """全角文件名 / 大小写差异 / 多空格,经 normalize 后应得同一 source_id。"""
    a = compute_source_id("诊断学.pdf")
    b = compute_source_id("　诊断学.pdf")  # 含全角空格
    c = compute_source_id("诊断学.PDF")  # 大写扩展名
    assert a == b == c


def test_source_id_different_for_different_files() -> None:
    a = compute_source_id("诊断学.pdf")
    b = compute_source_id("内科学.pdf")
    assert a != b


def test_source_id_is_deterministic() -> None:
    """同输入 → 同输出,跨进程跨次。"""
    for _ in range(5):
        assert compute_source_id("诊断学.pdf") == compute_source_id("诊断学.pdf")


# ───────────────────── heading_path_id(spec §3.1.4.2)──────────────────────


def test_heading_path_id_is_64_hex() -> None:
    """SHA-256 全长 = 64 hex(spec §3.1.4.2 未截断)。"""
    h = compute_heading_path_id(["第一章 常见症状", "第一节 发热"])
    assert len(h) == 64


def test_heading_path_id_changes_with_path() -> None:
    """不同章节路径 → 不同 ID。"""
    h1 = compute_heading_path_id(["第一章 常见症状", "第一节 发热"])
    h2 = compute_heading_path_id(["第一章 常见症状", "第二节 头痛"])
    h3 = compute_heading_path_id(["第二章 体格检查", "第一节 视诊"])
    assert h1 != h2 != h3 != h1


def test_heading_path_id_only_uses_existing_levels() -> None:
    """`["A", "B"]` 与 `["A", "B", ""]` 应不等(空层不补)。"""
    h_two = compute_heading_path_id(["第一章", "第一节"])
    h_three_with_empty = compute_heading_path_id(["第一章", "第一节", ""])
    assert h_two != h_three_with_empty


def test_heading_path_id_handles_empty_list() -> None:
    """顶层无标题兜底:空列表也得给出确定性 ID,且与任意非空路径不冲突。"""
    h_empty = compute_heading_path_id([])
    h_nonempty = compute_heading_path_id(["第一章"])
    assert len(h_empty) == 64
    assert h_empty != h_nonempty


def test_heading_path_id_normalized() -> None:
    """全角/大小写/空白扰动经 normalize 后应得同一 ID。"""
    h1 = compute_heading_path_id(["第一章 常见症状", "第一节 发热"])
    h2 = compute_heading_path_id(["第一章　常见症状", "第一节  发热"])  # 全角 + 多空格
    assert h1 == h2


# ───────────────────── chunk_id(spec §3.1.4.2)─────────────────────────────


def test_chunk_id_unique_per_index() -> None:
    """同 source + 同 path 下,不同序号必须给不同 ID。"""
    sid = compute_source_id("诊断学.pdf")
    path = compute_heading_path_id(["第一章", "第一节"])
    ids = {compute_chunk_id(sid, path, i) for i in range(10)}
    assert len(ids) == 10


def test_chunk_id_changes_with_source_or_path() -> None:
    sid_a = compute_source_id("诊断学.pdf")
    sid_b = compute_source_id("内科学.pdf")
    path_a = compute_heading_path_id(["第一章", "第一节"])
    path_b = compute_heading_path_id(["第二章", "第一节"])

    base = compute_chunk_id(sid_a, path_a, 0)
    diff_source = compute_chunk_id(sid_b, path_a, 0)
    diff_path = compute_chunk_id(sid_a, path_b, 0)
    assert base != diff_source
    assert base != diff_path
    assert diff_source != diff_path


def test_chunk_id_is_64_hex() -> None:
    sid = compute_source_id("诊断学.pdf")
    path = compute_heading_path_id(["第一章"])
    cid = compute_chunk_id(sid, path, 0)
    assert len(cid) == 64


# ───────────────────── parent_chunk_id(spec §3.1.4.2 父块约定)─────────────


def test_parent_chunk_id_uses_parent_string() -> None:
    """父块 ID 应等价于 compute_chunk_id(..., "parent")(spec §3.1.4.2)。"""
    sid = compute_source_id("诊断学.pdf")
    path = compute_heading_path_id(["第一章", "第一节"])
    assert compute_parent_chunk_id(sid, path) == compute_chunk_id(sid, path, "parent")


def test_parent_chunk_id_never_collides_with_child_ids() -> None:
    """父块 ID 与 0~999 任意子块 ID 都不应相等(数字 vs "parent" 字符串永不重)。"""
    sid = compute_source_id("诊断学.pdf")
    path = compute_heading_path_id(["第一章", "第一节"])
    parent = compute_parent_chunk_id(sid, path)
    child_ids = {compute_chunk_id(sid, path, i) for i in range(1000)}
    assert parent not in child_ids


def test_parent_chunk_id_unique_per_heading() -> None:
    """每个 heading 节只有一个父块 ID;不同节的父块 ID 不同。"""
    sid = compute_source_id("诊断学.pdf")
    p1 = compute_parent_chunk_id(sid, compute_heading_path_id(["第一章", "第一节"]))
    p2 = compute_parent_chunk_id(sid, compute_heading_path_id(["第一章", "第二节"]))
    assert p1 != p2


# ───────────────────── content_hash(spec §3.1.4.3)─────────────────────────


def test_content_hash_is_64_hex_full_sha256() -> None:
    """spec §3.1.4.3:content_hash = SHA256(text) 全长,不截断。"""
    h = compute_content_hash("发热是机体在致热源作用下体温升高的状态")
    assert len(h) == 64


def test_content_hash_changes_on_any_text_edit() -> None:
    """文本任何变化(包括标点)都会导致 hash 变化。"""
    base = compute_content_hash("腹痛是消化系统疾病常见症状")
    add_period = compute_content_hash("腹痛是消化系统疾病常见症状。")
    one_char = compute_content_hash("腹痛是消化系统疾病常见体征")
    assert base != add_period
    assert base != one_char


def test_content_hash_does_not_normalize() -> None:
    """与 source_id / heading_path_id 不同,content_hash 不做 normalize——
    保留原文一字一标点的精确指纹,才能精确触发增量更新。"""
    a = compute_content_hash("Hello World")
    b = compute_content_hash("hello world")
    assert a != b


def test_content_hash_matches_raw_sha256() -> None:
    """直接用 hashlib 算应该等价(防止内部加盐/前缀污染)。"""
    text = "测试文本"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert compute_content_hash(text) == expected


# ───────────────────── 综合:全链路确定性 + 跨字段不冲突 ───────────────────


def test_full_pipeline_deterministic() -> None:
    """模拟 chunking 产物:同一文档第二次跑应得到完全相同的 chunk_id 集合。"""
    file_name = "诊断学 第10版.pdf"
    chunks = [
        (["第一章 常见症状", "第一节 发热"], 0, "发热定义..."),
        (["第一章 常见症状", "第一节 发热"], 1, "病因机制..."),
        (["第一章 常见症状", "第二节 头痛"], 0, "头痛分类..."),
    ]

    def run() -> set[str]:
        sid = compute_source_id(file_name)
        return {
            compute_chunk_id(sid, compute_heading_path_id(headings), idx)
            for headings, idx, _text in chunks
        }

    assert run() == run()


@pytest.mark.parametrize("n_chunks", [10, 100, 1000])
def test_no_chunk_id_collision_within_section(n_chunks: int) -> None:
    """同一 heading 节下大量子块,chunk_id 应全唯一。"""
    sid = compute_source_id("诊断学.pdf")
    path = compute_heading_path_id(["第一章", "第一节"])
    ids = {compute_chunk_id(sid, path, i) for i in range(n_chunks)}
    assert len(ids) == n_chunks
