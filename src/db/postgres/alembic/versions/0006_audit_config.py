"""0006 audit_config — 4 张审计表 + system_config(DEV_SPEC §5.2.3 + §5.3)。

revision id = 0006_audit_config
"""
from __future__ import annotations

from typing import Sequence, Union

from src.db.postgres.alembic._helpers import execute_sql_file


revision: str = "0006_audit_config"
down_revision: Union[str, Sequence[str], None] = "0005_sessions_conversations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    execute_sql_file("0006_audit_config")


def downgrade() -> None:
    raise NotImplementedError("downgrade 暂不支持(spec MVP 阶段不需要回滚)")
