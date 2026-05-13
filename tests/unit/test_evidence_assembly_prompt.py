"""tests/unit/test_evidence_assembly_prompt.py — ⑩ Step 1 prompt builder
多模态消息组装(DEV_SPEC §3.2.3 LLM 路由 + §9.3 diagnose Step 1)。

覆盖:
- 无图时返回纯文本 HumanMessage(content: str)
- 有图时返回 multimodal HumanMessage(content: list[{text}, {image_url}, ...])
- prompt_text 镜像始终是 str(供 §9.6 final_prompt 审计)
- medical_statement 字段不出现在 prompt 文本里(spec §3.1.5.1 关键认知)
- figures 含 image_data_uri = None 的(加载失败的)→ 文本进 prompt,image_url 块不附加
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from src.prompts.agent import build_evidence_assembly_prompt


_BASE_KWARGS = dict(
    confirmed_symptoms=["腹痛"],
    denied_symptoms=[],
    slots={"location": "右上腹"},
    history_summary="既往胆囊结石",
    report_findings=[],
)


def test_no_figures_returns_text_only_message():
    messages, prompt_text = build_evidence_assembly_prompt(
        parent_texts=["父块全文 A", "父块全文 B"],
        figures=[],
        vector_hints=[],
        **_BASE_KWARGS,
    )
    assert len(messages) == 1
    msg = messages[0]
    assert isinstance(msg, HumanMessage)
    assert isinstance(msg.content, str)
    assert "父块全文 A" in msg.content
    assert msg.content == prompt_text


def test_figures_with_image_uri_yield_multimodal_message():
    figures = [
        {
            "chunk_id": "f1",
            "chunk_type": "figure",
            "chunk_raw_text": "图 2-4 急性胆囊炎超声",
            "title": "胆囊超声",
            "image_data_uri": "data:image/jpeg;base64,AAAA",
        },
        {
            "chunk_id": "t1",
            "chunk_type": "table",
            "chunk_raw_text": "<table>TG18</table>",
            "title": None,
            "image_data_uri": "data:image/jpeg;base64,BBBB",
        },
    ]
    messages, prompt_text = build_evidence_assembly_prompt(
        parent_texts=["父块"],
        figures=figures,
        vector_hints=[],
        **_BASE_KWARGS,
    )
    msg = messages[0]
    assert isinstance(msg.content, list)
    # 第一块是 text,后两块是 image_url
    assert msg.content[0]["type"] == "text"
    assert msg.content[1]["type"] == "image_url"
    assert msg.content[1]["image_url"]["url"] == "data:image/jpeg;base64,AAAA"
    assert msg.content[2]["image_url"]["url"] == "data:image/jpeg;base64,BBBB"
    # prompt_text 是文本镜像,不含 base64
    assert "AAAA" not in prompt_text
    assert "BBBB" not in prompt_text
    # figure 文本陈述应进 prompt 文本
    assert "图 2-4 急性胆囊炎超声" in prompt_text
    assert "TG18" in prompt_text


def test_figure_with_none_uri_skips_image_block_but_keeps_text():
    """image 加载失败(image_data_uri=None)→ 文本仍进 prompt,image_url 块不附加。"""
    figures = [
        {
            "chunk_id": "f_bad",
            "chunk_type": "figure",
            "chunk_raw_text": "图 X 加载失败仍带 caption",
            "title": None,
            "image_data_uri": None,
        }
    ]
    messages, prompt_text = build_evidence_assembly_prompt(
        parent_texts=["父块"],
        figures=figures,
        vector_hints=[],
        **_BASE_KWARGS,
    )
    msg = messages[0]
    # 无可加载图 → 退化为纯文本 message,避免空 list content 让 provider 误判
    assert isinstance(msg.content, str)
    assert "图 X 加载失败仍带 caption" in msg.content


def test_medical_statement_not_leaked_into_prompt():
    """spec §3.1.5.1:medical_statement 仅作召回辅助,不进 prompt。
    本测试模拟 figures 字段意外携带 medical_statement,确认 prompt builder 不消费它。"""
    figures = [
        {
            "chunk_id": "f1",
            "chunk_type": "figure",
            "chunk_raw_text": "caption only",
            "title": None,
            "image_data_uri": None,
            "medical_statement": "ENRICHMENT_STMT_SHOULD_NOT_APPEAR",
        }
    ]
    _, prompt_text = build_evidence_assembly_prompt(
        parent_texts=["父块"],
        figures=figures,
        vector_hints=[],
        **_BASE_KWARGS,
    )
    assert "ENRICHMENT_STMT_SHOULD_NOT_APPEAR" not in prompt_text


def test_vector_hints_appear_in_prompt():
    _, prompt_text = build_evidence_assembly_prompt(
        parent_texts=["父块"],
        figures=[],
        vector_hints=["胆囊炎概述", "怎么判断是胆囊炎"],
        **_BASE_KWARGS,
    )
    assert "胆囊炎概述" in prompt_text
    assert "怎么判断是胆囊炎" in prompt_text
