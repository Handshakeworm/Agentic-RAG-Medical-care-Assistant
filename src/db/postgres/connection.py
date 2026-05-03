"""PostgreSQL 连接池与 Session 管理(DEV_SPEC §2.4)。

薄层封装 SQLAlchemy 2.0 Engine + sessionmaker,提供:
- `get_engine()`:模块级单例 Engine,QueuePool(默认 5 + 10 overflow),pre_ping 防 stale 连接
- `session_scope()`:上下文管理器,自动 commit / rollback / close,业务代码不直接拿 Session
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(
        settings.postgres.dsn,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def _get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务边界:进作用域开 Session,正常退出 commit,异常 rollback,无论如何 close。"""
    session = _get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
