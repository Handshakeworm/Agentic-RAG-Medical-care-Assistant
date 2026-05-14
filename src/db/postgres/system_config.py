"""src/db/postgres/system_config.py — system_config 读写与 Cache-Aside 接入(DEV_SPEC §5.3 + §8.4 H7)。

职责:
- `get_dynamic_config(key, default)` — 业务读路径,经 Redis 缓存(H1),60s TTL
- `set_dynamic_config(key, value, ...)` — admin 写路径,同事务写 system_config + config_change_log,
  完成后调 `invalidate_config()` 让全节点最多 60s 切换到新值
- `list_dynamic_configs()` — admin UI 用,直接读 PG 不走缓存(管理端要看实时)

**严禁**(对齐 spec §5.3 / §9.7):
- agent_limits 七个常量 + 基础设施连接串 + Prompt 模板**禁止**进 system_config
  (settings.py 钦定;DB 改它们会绕过部署期校验)。本模块不做主动校验,但
  在 docstring + 注释中反复提醒,避免误用
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from src.db.postgres.connection import session_scope
from src.db.postgres.models_audit import ConfigChangeLog, SystemConfig
from src.db.redis.cache import get_config_cached, invalidate_config


_logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# 读路径(经缓存)
# ────────────────────────────────────────────────────────────────────────────


def get_dynamic_config(key_name: str, default: Any = None) -> Any:
    """读 system_config(经 Redis 60s 缓存,缓存层在 H1)。

    `key_name` 必须是 system_config 表里的 key(如 `llm_temperature`、`reranker_enabled`)。
    缓存未命中 → 回源 PG;PG 也没有 → 返回 `default`。

    **注意**:`agent_limits` 七个常量 / 基础设施连接串 / Prompt 模板**不在本表**
    (spec §5.3 显式分界)。业务代码若 import 错了,会拿到 default 静默 fallback。
    """
    val = get_config_cached(key_name, lambda: _load_from_pg(key_name))
    return val if val is not None else default


def _load_from_pg(key_name: str) -> Any | None:
    """PG 回源:`SELECT value FROM system_config WHERE key_name = :k`,无值返 None。

    H1 cache 约定:loader 返回 None 时不污染缓存(避免管理员补完配置等 60s 才生效)。
    """
    try:
        with session_scope() as s:
            row = s.execute(
                select(SystemConfig.value).where(SystemConfig.key_name == key_name)
            ).first()
            return row[0] if row else None
    except Exception as e:
        # PG 不可用:整个 readiness 应该已挂(/readyz 探测会失败),但配置读取
        # 不应让业务请求挂 — 走 default 兜底,WARNING 日志运维可见
        _logger.warning("PG 读 system_config[%s] 失败,走 default:%s", key_name, e)
        return None


# ────────────────────────────────────────────────────────────────────────────
# 写路径(admin 调用)
# ────────────────────────────────────────────────────────────────────────────


def set_dynamic_config(
    key_name: str,
    new_value: Any,
    *,
    operator_id: str,
    value_type: str | None = None,
    description: str | None = None,
    change_reason: str | None = None,
) -> None:
    """admin 改配置 — 同事务写 system_config + config_change_log,提交后失效缓存。

    spec §5.3 末:管理员修改配置时,应用层在同一事务中更新 system_config 表并向
    config_change_log 写入变更记录。

    `value_type`:首次插入必填;若漏传则按 Python 类型推断(`int`→`INT`,`float`→`FLOAT`,
    `str`→`STRING`,`bool`→`BOOL`,其余 `JSON`)。
    """
    inferred_type = value_type or _infer_value_type(new_value)
    with session_scope() as s:
        existing = s.get(SystemConfig, key_name)
        old_value = existing.value if existing else None

        if existing is None:
            s.add(SystemConfig(
                key_name=key_name,
                value=new_value,
                value_type=inferred_type,
                description=description,
                updated_by=operator_id,
            ))
        else:
            existing.value = new_value
            existing.value_type = inferred_type
            existing.updated_by = operator_id
            if description is not None:
                existing.description = description

        s.add(ConfigChangeLog(
            operator_id=operator_id,
            config_key=key_name,
            old_value=old_value,
            new_value=new_value,
            change_reason=change_reason,
        ))

    # 提交后刷缓存,下一次读最多 60s 内全节点切换;Redis 不可用也无所谓(TTL 自然兜底)
    invalidate_config(key_name)


# ────────────────────────────────────────────────────────────────────────────
# 列表读(admin UI 用)
# ────────────────────────────────────────────────────────────────────────────


def list_dynamic_configs() -> list[dict]:
    """直读 PG,**不走缓存**(管理端要看实时值,缓存可能滞后 60s)。"""
    with session_scope() as s:
        rows = s.execute(select(SystemConfig)).scalars().all()
        return [
            {
                "key_name": r.key_name,
                "value": r.value,
                "value_type": r.value_type,
                "description": r.description,
                "updated_by": r.updated_by,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]


# ────────────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────────────


def _infer_value_type(v: Any) -> str:
    if isinstance(v, bool):  # bool 必须在 int 之前,bool 是 int 子类
        return "BOOL"
    if isinstance(v, int):
        return "INT"
    if isinstance(v, float):
        return "FLOAT"
    if isinstance(v, str):
        return "STRING"
    return "JSON"
