"""Alembic 辅助函数 — revision 文件复用。

`execute_sql_file(name)` 读 src/db/postgres/migrations/<name>.sql,去掉 BEGIN/COMMIT
(Alembic 自己包了事务,SQL 文件里的显式事务会与之冲突),通过 driver-level
exec_driver_sql 一次性提交给 PG —— **必须**绕开 SQLAlchemy `text()` 的 `:name`
bind parameter 解析,否则 SQL 注释里出现的半角 `:`(如"用药列表:safety_gate ⑪")
会被误识别为待绑定参数。psycopg3 支持 multi-statement,DDL 顺序执行无副作用。
"""
from __future__ import annotations

from pathlib import Path

from alembic import op


_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def execute_sql_file(name: str) -> None:
    """把 migrations/<name>.sql 内容剔除 BEGIN/COMMIT 后 driver-level execute。"""
    sql = (_MIGRATIONS_DIR / f"{name}.sql").read_text(encoding="utf-8")
    cleaned_lines = [
        line
        for line in sql.splitlines()
        if line.strip().rstrip(";").upper() not in {"BEGIN", "COMMIT"}
    ]
    op.get_bind().exec_driver_sql("\n".join(cleaned_lines))
