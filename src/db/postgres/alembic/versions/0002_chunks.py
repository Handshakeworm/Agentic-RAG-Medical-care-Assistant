"""0002 chunks — chunks 元数据核心表(DEV_SPEC §2.4.2)。

revision id = 0002_chunks
"""
from __future__ import annotations

from typing import Sequence, Union

from src.db.postgres.alembic._helpers import execute_sql_file


revision: str = "0002_chunks"
down_revision: Union[str, Sequence[str], None] = "0001_raw_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    execute_sql_file("0002_chunks")


def downgrade() -> None:
    raise NotImplementedError("downgrade 暂不支持(spec MVP 阶段不需要回滚)")
