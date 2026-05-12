"""tests/integration/test_alembic_upgrade.py — 真起 PG 验证 `alembic upgrade head` 闭环。

spec §8.4 B2 验收标准:"Alembic upgrade head 可创建全部表结构与索引"。

实现思路:在隔离 schema(`alembic_test_<uuid>`)里跑全部 6 个 revision,验证 20 张表
+ 关键索引建出。teardown 直接 DROP SCHEMA CASCADE 清理。

不污染 public schema(public 已经是常规开发 DB,有真实数据)。
"""
from __future__ import annotations

import os
import socket
import uuid

import pytest
from sqlalchemy import create_engine, text


def _pg_alive() -> bool:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    try:
        socket.create_connection((host, port), timeout=2).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _pg_alive(), reason="PG 不可达,启动 docker compose 后再跑")


_EXPECTED_TABLES = {
    # 0001 + 0002 + 0003
    "sources",
    "raw_documents",
    "chunks",
    # 0004
    "users",
    "patients",
    "medical_history",
    "surgical_trauma_history",
    "transfusion_history",
    "allergies",
    "medications",
    "family_history",
    "menstrual_reproductive",
    "exam_reports",
    # 0005
    "sessions",
    "conversations",
    # 0006
    "rag_trace",
    "kb_change_log",
    "config_change_log",
    "diagnosis_feedback",
    "system_config",
}


@pytest.fixture
def isolated_schema():
    """创建隔离 schema,yield 名字,teardown DROP CASCADE。"""
    from config.settings import settings

    schema = f"alembic_test_{uuid.uuid4().hex[:8]}"
    engine = create_engine(settings.postgres.dsn, future=True)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield schema
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_alembic_upgrade_head_builds_all_20_tables(isolated_schema) -> None:
    """alembic upgrade head 在 fresh schema 上建出全部 20 张表(spec §8.4 B2 验收)。

    实现:开 PG 连接 → SET search_path 到隔离 schema → alembic 操作走该 schema。
    Alembic 通过 env.py 读 settings.postgres.dsn,我们把 search_path 通过
    options 注入到 DSN 即可。
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from config.settings import settings

    # 拼一个带 search_path 的 DSN,让 alembic 在隔离 schema 里建表。
    # 注意 alembic 走 configparser,`%` 必须 escape 成 `%%`(否则触发 % 插值)。
    dsn_for_engine = settings.postgres.dsn + f"?options=-csearch_path%3D{isolated_schema}"
    dsn_for_alembic = settings.postgres.dsn + f"?options=-csearch_path%%3D{isolated_schema}"

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", dsn_for_alembic)

    # 让 env.py 把 alembic_version 表也建到隔离 schema,
    # 避免读到 public.alembic_version 已 stamp 的版本号导致"已是 head"误判
    prev_env = os.environ.get("ALEMBIC_VERSION_TABLE_SCHEMA")
    os.environ["ALEMBIC_VERSION_TABLE_SCHEMA"] = isolated_schema
    try:
        command.upgrade(cfg, "head")
    finally:
        if prev_env is None:
            os.environ.pop("ALEMBIC_VERSION_TABLE_SCHEMA", None)
        else:
            os.environ["ALEMBIC_VERSION_TABLE_SCHEMA"] = prev_env

    # 验证 20 张表全部建出
    engine = create_engine(dsn_for_engine, future=True)
    try:
        with engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = :sch AND tablename != 'alembic_version'"
                    ),
                    {"sch": isolated_schema},
                )
            }
        assert tables == _EXPECTED_TABLES, (
            f"缺表:{_EXPECTED_TABLES - tables};多表:{tables - _EXPECTED_TABLES}"
        )

        # 验证关键索引:rag_trace 3 个,medications 当前用药 partial index
        with engine.connect() as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = :sch"
                    ),
                    {"sch": isolated_schema},
                )
            }
            for required in (
                "idx_rag_trace_session",
                "idx_rag_trace_user",
                "idx_rag_trace_created",
                "idx_medications_active",
                "idx_sessions_status_active",
            ):
                assert required in indexes, f"缺索引 {required}"

        # 验证 alembic_version 标记 head
        with engine.connect() as conn:
            head = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert head == "0006_audit_config"
    finally:
        engine.dispose()
