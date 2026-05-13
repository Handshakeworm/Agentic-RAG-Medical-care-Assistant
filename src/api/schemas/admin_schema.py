"""管理员接口 Pydantic 模型(DEV_SPEC §8.4 G6 / §5.2.3 / §5.3)。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


# ────────────────────────────────────────────────────────────────────────────
# 用户管理
# ────────────────────────────────────────────────────────────────────────────


class AdminUserOut(BaseModel):
    """`GET /admin/users` 列表行。不返 password 哈希。"""

    id: str
    email: EmailStr
    role: str
    created_at: datetime


# ────────────────────────────────────────────────────────────────────────────
# 系统配置(spec §5.3 system_config + §5.2.3.3 config_change_log)
# ────────────────────────────────────────────────────────────────────────────


class SystemConfigOut(BaseModel):
    """system_config 一行的展示。`value` JSONB 任意类型。"""

    key_name: str
    value: Any | None
    value_type: str | None
    description: str | None
    updated_at: datetime


class SystemConfigUpsert(BaseModel):
    """`PUT /admin/config/{key}` 请求体。

    spec §5.3.1 末:admin 改值时**同事务**写 system_config + config_change_log
    (G6 视图函数内裸代码,不封装 helper)。

    `change_reason` 对应 config_change_log.change_reason —— 强制 admin 写说明
    避免变更追溯不到源头(spec §5.2.3.3 强调"前后对比""回滚决策"用的就是这字段)。
    """

    value: Any
    value_type: Literal["INT", "FLOAT", "STRING", "BOOL", "JSON"] | None = None
    description: str | None = None
    change_reason: str = Field(..., min_length=1, max_length=500)


# ────────────────────────────────────────────────────────────────────────────
# 知识库上传 stub
# ────────────────────────────────────────────────────────────────────────────


class KbUploadStubResponse(BaseModel):
    """`POST /admin/kb/upload` 返回 — 提示该走脚本路径(C7 pipeline 未做完)。"""

    status: Literal["unimplemented"]
    instruction: str
