"""0003 chunks_media_columns — 单行多列设计(DEV_SPEC 2026-05-12 §3.1.2 修订)。

revision id = 0003_chunks_media_columns
"""
from __future__ import annotations

from typing import Sequence, Union

from src.db.postgres.alembic._helpers import execute_sql_file


revision: str = "0003_chunks_media_columns"
down_revision: Union[str, Sequence[str], None] = "0002_chunks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    execute_sql_file("0003_chunks_media_columns")


def downgrade() -> None:
    raise NotImplementedError("downgrade 暂不支持(spec MVP 阶段不需要回滚)")
