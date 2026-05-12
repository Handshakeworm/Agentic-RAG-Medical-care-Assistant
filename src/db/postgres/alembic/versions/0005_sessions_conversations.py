"""0005 sessions_conversations — sessions + conversations(DEV_SPEC §2.4.3)。

revision id = 0005_sessions_conversations
"""
from __future__ import annotations

from typing import Sequence, Union

from src.db.postgres.alembic._helpers import execute_sql_file


revision: str = "0005_sessions_conversations"
down_revision: Union[str, Sequence[str], None] = "0004_users_patients"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    execute_sql_file("0005_sessions_conversations")


def downgrade() -> None:
    raise NotImplementedError("downgrade 暂不支持(spec MVP 阶段不需要回滚)")
