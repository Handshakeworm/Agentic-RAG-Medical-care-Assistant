"""tests/unit/test_dialog_audit_models.py — 锁住 §2.4.3 / §5.2.3 / §5.3 ORM schema。

覆盖:Session / Conversation / RagTrace(15 字段)/ KbChangeLog / ConfigChangeLog /
DiagnosisFeedback / SystemConfig。CRUD 走 tests/integration/test_dialog_audit_crud.py。
"""
from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB

from src.db.postgres.models_audit import (
    ConfigChangeLog,
    DiagnosisFeedback,
    KbChangeLog,
    RagTrace,
    SystemConfig,
)
from src.db.postgres.models_dialog import Conversation, Session


# ────────────────────────────────────────────────────────────────────────────
# §2.4.3 sessions / conversations
# ────────────────────────────────────────────────────────────────────────────


def test_sessions_pk_and_fk_to_users() -> None:
    assert Session.__tablename__ == "sessions"
    pk = [c.name for c in Session.__table__.primary_key.columns]
    assert pk == ["id"]

    fks = list(Session.__table__.c["user_id"].foreign_keys)
    assert len(fks) == 1 and fks[0].column.table.name == "users"

    status = Session.__table__.c["status"]
    assert isinstance(status.type, String) and status.type.length == 20
    assert "active" in str(status.server_default.arg)


def test_conversations_redundant_user_id_for_join_avoidance() -> None:
    """spec §2.4.3 注:`user_id` 冗余存,避免跨表 JOIN。"""
    table = Conversation.__table__
    assert {"session_id", "user_id"}.issubset({c.name for c in table.columns})
    for name in ("user_input", "llm_output"):
        assert isinstance(table.c[name].type, Text)
        assert not table.c[name].nullable
    assert isinstance(table.c["rag_context"].type, JSONB)


# ────────────────────────────────────────────────────────────────────────────
# §5.2.3.1 rag_trace — 15 字段(对应 §9.6.2 数据来源对照表)
# ────────────────────────────────────────────────────────────────────────────


def test_rag_trace_has_exactly_15_fields_from_spec_9_6_2() -> None:
    """§9.6.2 列出 15 字段对照表,这里逐一锁住列名集合。"""
    cols = {c.name for c in RagTrace.__table__.columns}
    assert cols == {
        "trace_id",
        "session_id",
        "user_id",
        "raw_query",
        "intent_result",
        "retrieved_chunks",
        "reranked_chunks",
        "final_prompt",
        "llm_raw_output",
        "final_response",
        "model_name",
        "token_usage",
        "latency_ms",
        "error_info",
        "created_at",
    }


def test_rag_trace_optional_fields_for_normal_path() -> None:
    """spec §9.6.2:final_prompt / llm_raw_output 正常路径 NULL,
    intent_result / retrieved_chunks / reranked_chunks / token_usage / latency_ms /
    error_info 也允许 NULL(对应 capped 短路或部分失败兜底)。"""
    table = RagTrace.__table__
    for name in (
        "final_prompt",
        "llm_raw_output",
        "intent_result",
        "retrieved_chunks",
        "reranked_chunks",
        "token_usage",
        "latency_ms",
        "error_info",
    ):
        assert table.c[name].nullable, f"{name} 应允许 NULL(§9.6.2)"


def test_rag_trace_required_text_fields() -> None:
    table = RagTrace.__table__
    for name in ("raw_query", "final_response"):
        assert isinstance(table.c[name].type, Text) and not table.c[name].nullable


def test_rag_trace_model_name_varchar64() -> None:
    """spec §5.2.3.1 / §9.6.2:model_name VARCHAR(64) NOT NULL。"""
    col = RagTrace.__table__.c["model_name"]
    assert isinstance(col.type, String) and col.type.length == 64
    assert not col.nullable


# ────────────────────────────────────────────────────────────────────────────
# §5.2.3.2 / §5.2.3.3 / §5.2.3.4 — kb_change / config_change / diagnosis_feedback
# ────────────────────────────────────────────────────────────────────────────


def test_kb_change_log_operation_required() -> None:
    table = KbChangeLog.__table__
    op_col = table.c["operation"]
    assert isinstance(op_col.type, String) and op_col.type.length == 32
    assert not op_col.nullable


def test_config_change_log_old_new_jsonb() -> None:
    table = ConfigChangeLog.__table__
    assert isinstance(table.c["old_value"].type, JSONB)
    assert isinstance(table.c["new_value"].type, JSONB)


def test_diagnosis_feedback_links_trace_and_reviewer() -> None:
    """spec §5.2.3.4:trace_id FK→rag_trace,reviewer_id FK→users。"""
    table = DiagnosisFeedback.__table__
    trace_fks = list(table.c["trace_id"].foreign_keys)
    assert len(trace_fks) == 1 and trace_fks[0].column.table.name == "rag_trace"
    reviewer_fks = list(table.c["reviewer_id"].foreign_keys)
    assert len(reviewer_fks) == 1 and reviewer_fks[0].column.table.name == "users"


# ────────────────────────────────────────────────────────────────────────────
# §5.3 system_config
# ────────────────────────────────────────────────────────────────────────────


def test_system_config_pk_is_key_name() -> None:
    """key_name = PK,值 JSONB,value_type 供前端校验(spec §5.3)。"""
    pk = [c.name for c in SystemConfig.__table__.primary_key.columns]
    assert pk == ["key_name"]
    table = SystemConfig.__table__
    assert isinstance(table.c["value"].type, JSONB)
    val_type = table.c["value_type"]
    assert isinstance(val_type.type, String) and val_type.type.length == 32


def test_system_config_updated_by_optional_fk_to_users() -> None:
    """初次系统种子配置 updated_by 可空(没有真人操作),admin 改后填值。"""
    col = SystemConfig.__table__.c["updated_by"]
    assert col.nullable
    fks = list(col.foreign_keys)
    assert len(fks) == 1 and fks[0].column.table.name == "users"
