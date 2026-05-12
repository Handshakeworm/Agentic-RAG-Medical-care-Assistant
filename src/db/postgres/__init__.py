"""PostgreSQL ORM 顶层导出 — 集中加载所有模型,供 Alembic env.py 扫描元数据。

DEV_SPEC §2.4 / §5.2.3 / §5.3 涉及的表全部在以下子模块内:
- `models`           Source / RawDocument / Chunk(§2.4.2 / §2.4.4)
- `models_patient`   User / Patient / 8 张患者历史表(§2.4.5)
- `models_dialog`    Session / Conversation(§2.4.3)
- `models_audit`     RagTrace / KbChangeLog / ConfigChangeLog / DiagnosisFeedback /
                     SystemConfig(§5.2.3.1~§5.2.3.4 + §5.3)

⚠️ 这里的 import 顺序(`models` 先于 `models_patient` 等)需要保持 ——
`models.Base` 是 DeclarativeBase 单例,其余子模块都 `from src.db.postgres.models import Base`
扩展同一份 metadata。Alembic env.py `target_metadata = Base.metadata` 即可拿到
所有 19 张表。
"""
from __future__ import annotations

from src.db.postgres.connection import get_engine, session_scope
from src.db.postgres.models import (
    Base,
    Chunk,
    RawDocument,
    Source,
    bulk_upsert_chunks,
    upsert_raw_document,
    upsert_source,
)
from src.db.postgres.models_audit import (
    ConfigChangeLog,
    DiagnosisFeedback,
    KbChangeLog,
    RagTrace,
    SystemConfig,
)
from src.db.postgres.models_dialog import Conversation, Session
from src.db.postgres.models_patient import (
    Allergy,
    ExamReport,
    FamilyHistory,
    MedicalHistory,
    Medication,
    MenstrualReproductive,
    Patient,
    SurgicalTraumaHistory,
    TransfusionHistory,
    User,
)

__all__ = [
    # 基础设施
    "Base",
    "get_engine",
    "session_scope",
    # 文档/chunk(§2.4.2 / §2.4.4)
    "Source",
    "RawDocument",
    "Chunk",
    "upsert_source",
    "upsert_raw_document",
    "bulk_upsert_chunks",
    # 用户/患者(§2.4.5)
    "User",
    "Patient",
    "MedicalHistory",
    "SurgicalTraumaHistory",
    "TransfusionHistory",
    "Allergy",
    "Medication",
    "FamilyHistory",
    "MenstrualReproductive",
    "ExamReport",
    # 会话(§2.4.3)
    "Session",
    "Conversation",
    # 审计 + 配置(§5.2.3 + §5.3)
    "RagTrace",
    "KbChangeLog",
    "ConfigChangeLog",
    "DiagnosisFeedback",
    "SystemConfig",
]
