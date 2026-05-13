"""问诊接口(DEV_SPEC §8.4 G4 + §9.6)。

`POST /diagnose` 把 F 阶段 LangGraph app 接进 HTTP:
- 首次:建 sessions 行 → invoke graph(initial_state)
- 追问/检查回传:用 LangGraph `Command(resume=...)` 恢复 interrupt
- 终态:按 §9.6.2 / §9.6.5 裸代码模板写一行 rag_trace 15 字段 + conversations
- 任意轮 graph 抛异常:logger.error + 500;rag_trace 写不写以是否到达终态为准

实现风格按 §9.6.5 强制:**不**封装 AuditWriter / @audit_rag_trace 装饰器,
所有字段在视图函数内裸组装。

⚠️ TODO(留给用户拍板):
- checkpointer 当前用 `InMemorySaver` 模块级单例 — 进程重启会丢 in-flight session;
  生产应换 `langgraph-checkpoint-postgres.PostgresSaver`(deps 已有),
  需要单独建 `langgraph_checkpoints` 表 + Alembic 迁移
- conversations 表本端只在 completed 终态写一行 user_input=raw / llm_output=final;
  spec §2.4.3 一行 = 一次"用户-系统交互",理论上每个追问轮也算一次,
  但首版收口在终态,降低中途异常时的脏数据
"""
from __future__ import annotations

import logging
import time
import uuid
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.types import Command
from sqlalchemy.orm import Session as OrmSession

from config.settings import settings
from src.agent.graph import build_app
from src.agent.state import MedicalState
from src.api.middleware.auth_middleware import CurrentUser, get_current_user
from src.api.routes.auth import get_db  # 复用 G2 的 session Depends
from src.api.schemas.diagnosis_schema import DiagnoseRequest, DiagnoseResponse
from src.db.postgres.models_audit import RagTrace
from src.db.postgres.models_dialog import Conversation, Session as SessionRow


_logger = logging.getLogger(__name__)
router = APIRouter()


# ────────────────────────────────────────────────────────────────────────────
# Compiled graph 单例 — checkpointer 跨请求保存 interrupt 状态
# ────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _get_compiled_graph():
    """模块级 compiled graph + InMemorySaver。

    interrupt → resume 必须用同一个 checkpointer 实例,所以走单例。
    """
    return build_app()


# ────────────────────────────────────────────────────────────────────────────
# 辅助:rag_trace error_info 派生(spec §9.6.3)
# ────────────────────────────────────────────────────────────────────────────


def _build_error_info(diagnosis_result: list[dict]) -> dict | None:
    """spec §9.6.3 的精确转写。正常 LLM 推理 → None;系统级失败 → 结构化 dict。"""
    if not diagnosis_result:
        return {"source": "diagnose", "failure_reason": "empty_diagnosis_result", "step": None}
    reason = diagnosis_result[0].get("failure_reason")
    if reason is None:
        return None
    step = None
    if reason.startswith("step_") and "_structured_output_failed" in reason:
        try:
            step = int(reason.split("_")[1])
        except (IndexError, ValueError):
            step = None
    return {"source": "diagnose", "failure_reason": reason, "step": step}


# ────────────────────────────────────────────────────────────────────────────
# 主端点
# ────────────────────────────────────────────────────────────────────────────


@router.post(
    "/diagnose",
    response_model=DiagnoseResponse,
    summary="问诊主接口(支持多轮追问 / 检查回传)",
)
async def diagnose(
    req: DiagnoseRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> DiagnoseResponse:
    # ─── 1. session 寻址或新建 ───────────────────────────────────────
    if req.session_id:
        sess: SessionRow | None = db.get(SessionRow, req.session_id)
        if sess is None:
            raise HTTPException(404, f"session {req.session_id} 不存在")
        if sess.user_id != current_user.user_id:
            # 不能蹭别人的 session_id 接着跑
            raise HTTPException(403, "session 不属于当前用户")
        session_id = sess.id
        is_first_round = False
    else:
        if not req.patient_input:
            raise HTTPException(422, "首次问诊必须提供 patient_input")
        sess = SessionRow(user_id=current_user.user_id, title=req.patient_input[:60])
        db.add(sess)
        db.flush()
        db.refresh(sess)
        session_id = sess.id
        is_first_round = True

    # ─── 2. 构造 graph invoke 入参 ────────────────────────────────────
    graph_app = _get_compiled_graph()
    config = {"configurable": {"thread_id": f"session_{session_id}"}}

    if is_first_round:
        # 首次:用初始 State 拉起 graph
        graph_input: MedicalState | Command = MedicalState(
            patient_id=current_user.user_id,
            patient_input=req.patient_input or "",
        )
    else:
        # 后续:用 Command(resume=...) 恢复 interrupt
        # interrupt 节点写回 followup_answer(str) 或 pending_exam_results(list[dict])
        if req.followup_answer is not None:
            graph_input = Command(resume=req.followup_answer)
        elif req.exam_results is not None:
            graph_input = Command(resume=req.exam_results)
        else:
            raise HTTPException(
                422,
                "后续轮必须提供 followup_answer 或 exam_results 之一",
            )

    # ─── 3. 跑 graph(同步 invoke,interrupt 自然返回)─────────────────
    t0 = time.perf_counter()
    try:
        # ainvoke 走 async,但 graph 内部节点都是同步函数 — langgraph 自己 sched
        final_or_interrupt = await graph_app.ainvoke(graph_input, config=config)
    except Exception as e:
        _logger.error(
            "graph invoke failed for session %s: %s", session_id, e, exc_info=True
        )
        raise HTTPException(500, "诊断服务暂不可用,请稍后再试") from e
    invoke_latency_ms = int((time.perf_counter() - t0) * 1000)

    # ─── 4. 判终态 vs interrupt ───────────────────────────────────────
    # interrupt 时 final_or_interrupt 仍是 dict-like state(LangGraph 0.2+ 行为):
    # 通过 graph_app.aget_state 拿 next 元组判断是否还有节点待跑
    snapshot = await graph_app.aget_state(config)
    has_pending = bool(snapshot.next)

    # State 投影回 MedicalState 方便字段访问(snapshot.values 可能是 dict 也可能是 model)
    state_dict = (
        snapshot.values
        if isinstance(snapshot.values, dict)
        else snapshot.values.model_dump()
    )

    if has_pending:
        # interrupt 触发,snapshot.next 含暂停在哪个节点
        next_node = snapshot.next[0] if snapshot.next else ""
        if next_node == "wait_followup_answer":
            return DiagnoseResponse(
                session_id=session_id,
                status="ongoing_followup",
                pending_question=state_dict.get("followup_question") or "",
            )
        if next_node == "wait_exam_report":
            return DiagnoseResponse(
                session_id=session_id,
                status="ongoing_exam",
                recommended_tests=list(state_dict.get("recommended_tests") or []),
            )
        # 不预期的 interrupt 节点 — 防御日志,按追问处理(总比 500 强)
        _logger.warning(
            "session %s interrupt at unexpected node %s", session_id, next_node
        )
        return DiagnoseResponse(
            session_id=session_id,
            status="ongoing_followup",
            pending_question=state_dict.get("followup_question") or "(等待用户输入)",
        )

    # ─── 5. 终态:按 §9.6.5 裸代码模板写 rag_trace + conversation ─────
    s = state_dict
    trace_row = RagTrace(
        session_id=session_id,
        user_id=current_user.user_id,
        raw_query=s.get("patient_input") or "",
        intent_result={
            "chief_complaint": s.get("chief_complaint"),
            "confirmed_symptoms": s.get("confirmed_symptoms") or [],
            "denied_symptoms": s.get("denied_symptoms") or [],
            "standardized_entities": s.get("standardized_entities") or [],
        },
        retrieved_chunks=s.get("candidate_chunks") or [],
        reranked_chunks=s.get("last_reranked_chunks") or [],
        final_prompt=s.get("last_diagnose_prompt"),       # 正常路径 None → DB NULL
        llm_raw_output=s.get("last_diagnose_raw_output"),
        final_response=s.get("final_response") or "",
        model_name=settings.llm.MODEL_NAME,
        token_usage=s.get("session_token_usage") or {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0
        },
        latency_ms={
            **(s.get("session_latency_ms") or {}),
            "total": invoke_latency_ms,
        },
        error_info=_build_error_info(s.get("diagnosis_result") or []),
    )

    conv_row = Conversation(
        session_id=session_id,
        user_id=current_user.user_id,
        user_input=s.get("patient_input") or "",
        llm_output=s.get("final_response") or "",
        rag_context={
            "chunk_ids": [
                c.get("source_chunk_id")
                for c in (s.get("last_reranked_chunks") or [])
                if c.get("source_chunk_id")
            ],
        },
    )

    # spec §9.6.1 事务性:rag_trace / conversation 写失败不阻塞响应,但 error 日志告警
    try:
        db.add(trace_row)
        db.add(conv_row)
        db.flush()
    except Exception:
        db.rollback()
        _logger.error(
            "rag_trace / conversation write failed for session %s",
            session_id,
            exc_info=True,
        )
        # 不 raise:响应仍要给用户

    return DiagnoseResponse(
        session_id=session_id,
        status="completed",
        final_response=s.get("final_response"),
        diagnosis_result=s.get("diagnosis_result") or [],
        medication_advice=s.get("medication_advice") or [],
        risk_warnings=s.get("risk_warnings") or [],
    )
