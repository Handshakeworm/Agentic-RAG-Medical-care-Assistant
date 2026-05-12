"""0004 users_patients — users + patients + 8 张患者历史表(DEV_SPEC §2.4.5)。

revision id = 0004_users_patients
"""
from __future__ import annotations

from typing import Sequence, Union

from src.db.postgres.alembic._helpers import execute_sql_file


revision: str = "0004_users_patients"
down_revision: Union[str, Sequence[str], None] = "0003_chunks_media_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    execute_sql_file("0004_users_patients")


def downgrade() -> None:
    raise NotImplementedError("downgrade 暂不支持(spec MVP 阶段不需要回滚)")
