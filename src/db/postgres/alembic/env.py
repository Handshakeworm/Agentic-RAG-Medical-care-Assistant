"""Alembic env.py — 注入 DSN + 加载所有 ORM 模型(DEV_SPEC §8.4 B2)。

关键点:
- DSN 走 config.settings,不在 alembic.ini 重复硬编码
- `from src.db.postgres import Base` 触发所有子模块导入,Base.metadata 含全部 20 张表
- offline 模式生成 SQL(CI 检查用),online 模式真连 DB 跑
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config.settings import settings
from src.db.postgres import Base  # noqa: F401  ── 触发全部 ORM 模块加载

config = context.config

# 把 DSN 注入 alembic context(只在 alembic.ini 没设值时才覆盖,
# 这样测试可以通过 cfg.set_main_option 自定义 DSN 走隔离 schema)
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.postgres.dsn)

# 测试场景:把 alembic_version 表也路由到指定 schema,避免读到 public 下已 stamp 的版本
_VERSION_TABLE_SCHEMA = os.getenv("ALEMBIC_VERSION_TABLE_SCHEMA")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _common_configure_kwargs() -> dict:
    kw = {"target_metadata": target_metadata}
    if _VERSION_TABLE_SCHEMA:
        kw["version_table_schema"] = _VERSION_TABLE_SCHEMA
    return kw


def run_migrations_offline() -> None:
    """生成 SQL 脚本(`alembic upgrade --sql head`),不连 DB。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_common_configure_kwargs(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """连 DB 实际执行 migration(`alembic upgrade head`)。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            **_common_configure_kwargs(),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
