"""tests/unit/test_node_diagnose.py — F10 ⑩ diagnose 单元测试(DEV_SPEC §4.1.2 ⑩)。

5 条路径(spec §8.3 F10 验收):
1. followup_round 触顶 → failure_reason == "followup_round_capped"
2. 正常三步成功 → failure_reason is None
3. Step 1 失败 → failure_reason.startswith("step_1_structured_output_failed")
4. Step 2 失败 → failure_reason.startswith("step_2_structured_output_failed")
5. Step 3 失败 → failure_reason.startswith("step_3_structured_output_failed")

兜底场景共同断言:differentiation_type == "insufficient" 且 probability == 0.0,
last_diagnose_prompt / raw_output 已写入(供审计)。
"""
from __future__ import annotations

from unittest.mock import patch

from config.settings import settings
from src.agent.schemas.diagnosis import (
    CandidateEvidence,
    DiagnosisOutput,
    DiagnosisRanking,
    EvidenceSheet,
    RankedDisease,
)
from src.agent.state import create_initial_state


def _state_for_diagnose():
    s = create_initial_state(patient_id="P", patient_input="x")
    s.chief_complaint = "腹痛"
    s.confirmed_symptoms = ["腹痛"]
    s.candidate_chunks = [
        {
            "source_chunk_id": "c1",
            "rrf_score": 0.1,
            "vector_hits": [
                {"vector_type": "original", "rank": 1, "matched_text": "胆囊炎症状"}
            ],
        }
    ]
    return s


def _ok_evidence():
    return EvidenceSheet(
        candidates=[
            CandidateEvidence(disease="胆囊炎", supporting=["腹痛"]),
        ]
    )


def _ok_ranking():
    return DiagnosisRanking(
        ranked=[
            RankedDisease(
                disease="胆囊炎",
                probability=0.7,
                evidence_chain=["症状典型"],
                differentiation_type="confirmed",
            )
        ]
    )


def _ok_output():
    return DiagnosisOutput(
        results=[
            RankedDisease(
                disease="胆囊炎",
                probability=0.7,
                evidence_chain=["症状典型"],
                differentiation_type="confirmed",
            )
        ]
    )


# ────────────────────────────────────────────────────────────────────────────
# 路径 1:Step -1 触顶兜底
# ────────────────────────────────────────────────────────────────────────────


def test_followup_round_cap_short_circuits():
    from src.agent.nodes.diagnose import diagnose

    s = _state_for_diagnose()
    s.followup_round = settings.agent_limits.MAX_FOLLOWUP_ROUNDS
    update = diagnose(s)

    res = update["diagnosis_result"][0]
    assert res["failure_reason"] == "followup_round_capped"
    assert res["differentiation_type"] == "insufficient"
    assert res["probability"] == 0.0


# ────────────────────────────────────────────────────────────────────────────
# 路径 2:正常三步成功
# ────────────────────────────────────────────────────────────────────────────


@patch(
    "src.agent.nodes.diagnose.rerank_with_fallback", return_value=[0]
)
@patch(
    "src.agent.nodes.diagnose.lookup_chunk_content",
    return_value={"c1": {"chunk_raw_text": "胆囊炎诊断标准...", "parent_chunk_id": None}},
)
@patch("src.agent.nodes.diagnose.get_llm")
def test_normal_three_steps_succeed(mock_llm, _lookup, _rerank):
    from src.agent.nodes.diagnose import diagnose

    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.side_effect = [_ok_evidence(), _ok_ranking(), _ok_output()]

    s = _state_for_diagnose()
    update = diagnose(s)

    res = update["diagnosis_result"][0]
    assert res["failure_reason"] is None
    assert res["disease"] == "胆囊炎"
    assert "last_reranked_chunks" in update
    # 正常路径不写 last_diagnose_prompt / raw_output
    assert "last_diagnose_prompt" not in update
    assert "last_diagnose_raw_output" not in update


# ────────────────────────────────────────────────────────────────────────────
# 路径 3/4/5:Step 1/2/3 失败
# ────────────────────────────────────────────────────────────────────────────


def _make_step_failure_test(step_num: int):
    @patch("src.agent.nodes.diagnose.rerank_with_fallback", return_value=[0])
    @patch(
        "src.agent.nodes.diagnose.lookup_chunk_content",
        return_value={"c1": {"chunk_raw_text": "x", "parent_chunk_id": None}},
    )
    @patch("src.agent.nodes.diagnose.get_llm")
    def _test(mock_llm, _lookup, _rerank):
        from src.agent.nodes.diagnose import diagnose

        mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
        invoke_returns = [_ok_evidence(), _ok_ranking(), _ok_output()]
        # 第 step_num 次调用抛异常
        invoke_returns[step_num - 1] = ValueError(f"schema rejected at step {step_num}")

        def fake_invoke(prompt, config=None):
            r = fake_invoke.queue.pop(0)
            if isinstance(r, BaseException):
                raise r
            return r

        fake_invoke.queue = invoke_returns
        mock_chain.invoke.side_effect = fake_invoke

        s = _state_for_diagnose()
        update = diagnose(s)
        res = update["diagnosis_result"][0]
        assert res["failure_reason"].startswith(
            f"step_{step_num}_structured_output_failed"
        )
        assert res["differentiation_type"] == "insufficient"
        assert res["probability"] == 0.0
        assert update["last_diagnose_prompt"] is not None
        assert update["last_diagnose_raw_output"] is not None

    return _test


test_step1_failure = _make_step_failure_test(1)
test_step2_failure = _make_step_failure_test(2)
test_step3_failure = _make_step_failure_test(3)


# ────────────────────────────────────────────────────────────────────────────
# 路径 6:vision LLM 路由(spec §3.2.3 LLM 路由 + §9.3 diagnose Step 1)
# ────────────────────────────────────────────────────────────────────────────


@patch("src.agent.nodes.diagnose.rerank_with_fallback", return_value=[0])
@patch(
    "src.agent.nodes.diagnose.lookup_chunk_content",
    return_value={"c1": {"chunk_raw_text": "x", "parent_chunk_id": None}},
)
@patch("src.agent.nodes.diagnose.get_llm")
def test_step1_uses_vision_llm_step23_use_main_llm(mock_llm, _lookup, _rerank):
    """spec §3.2.3 LLM 路由:Step 1 走 vision LLM(传 vision 三件套),
    Step 2/3 走主链(无参数 get_llm())。"""
    from config.settings import settings
    from src.agent.nodes.diagnose import diagnose

    mock_chain = mock_llm.return_value.with_structured_output.return_value.with_retry.return_value
    mock_chain.invoke.side_effect = [_ok_evidence(), _ok_ranking(), _ok_output()]

    s = _state_for_diagnose()
    diagnose(s)

    # 验证 get_llm 被调用了至少 2 次:一次 vision 一次 main
    call_args_list = mock_llm.call_args_list
    assert len(call_args_list) >= 2

    # 第 1 次:vision LLM 调用(传 vision 三件套关键字参数)
    vision_call = call_args_list[0]
    assert vision_call.kwargs.get("model") == settings.llm.VISION_MODEL_NAME
    assert vision_call.kwargs.get("base_url") == settings.llm.VISION_BASE_URL

    # 第 2 次:主链 LLM 调用(无 model/base_url/api_key 覆盖)
    main_call = call_args_list[1]
    assert main_call.kwargs.get("model") is None or main_call.kwargs == {}
