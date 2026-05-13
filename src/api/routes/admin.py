"""管理员接口(DEV_SPEC §8.4 G6 / §5.2.3 / §5.3)。

挂在 `/admin/*`,**全部** require_role("admin")。

端点列表:
- GET    /admin/users                  列出全部用户(分页 limit/offset)
- GET    /admin/config                 列出所有 system_config
- GET    /admin/config/{key}           读单条
- PUT    /admin/config/{key}           写/改一条 + 同事务写 config_change_log
- DELETE /admin/config/{key}           删一条
- POST   /admin/kb/upload              知识库上传(stub)

⚠️ TODO(留给用户拍板):
- 知识库上传 stub:C7 ingestion pipeline 入口函数(`ingest.py`)还没做完,
  现在只能调 scripts/ 下的脚本。本端点先返"请走脚本"提示;C7 完工后改成
  multipart 上传 + 触发后台 task + kb_change_log 写入
- 用户管理只做"列出 + 改 role + 删除",创建走 G2 /auth/register
- system_config 改值后没接 Redis cache invalidation — H6 对接 Redis 时补
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from src.api.middleware.auth_middleware import CurrentUser, require_role
from src.api.routes.auth import get_db
from src.api.schemas.admin_schema import (
    AdminUserOut,
    KbUploadStubResponse,
    SystemConfigOut,
    SystemConfigUpsert,
)
from src.db.postgres.models_audit import ConfigChangeLog, SystemConfig
from src.db.postgres.models_patient import User


_logger = logging.getLogger(__name__)
router = APIRouter()


# ────────────────────────────────────────────────────────────────────────────
# 用户管理
# ────────────────────────────────────────────────────────────────────────────


@router.get(
    "/users",
    response_model=list[AdminUserOut],
    summary="列出全部用户(分页)",
)
def list_users(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: CurrentUser = Depends(require_role("admin")),
    db: OrmSession = Depends(get_db),
) -> list[AdminUserOut]:
    rows = (
        db.execute(
            select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return [
        AdminUserOut(
            id=r.id, email=r.email, role=r.role, created_at=r.created_at
        )
        for r in rows
    ]


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除用户(级联清空 patients / 历史子表 / sessions / rag_trace)",
)
def delete_user(
    user_id: str,
    admin: CurrentUser = Depends(require_role("admin")),
    db: OrmSession = Depends(get_db),
) -> None:
    if user_id == admin.user_id:
        raise HTTPException(400, "不能删自己")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    db.delete(user)


# ────────────────────────────────────────────────────────────────────────────
# system_config(§5.3)
# ────────────────────────────────────────────────────────────────────────────


@router.get(
    "/config",
    response_model=list[SystemConfigOut],
    summary="列出全部动态配置",
)
def list_configs(
    _admin: CurrentUser = Depends(require_role("admin")),
    db: OrmSession = Depends(get_db),
) -> list[SystemConfigOut]:
    rows = db.execute(select(SystemConfig).order_by(SystemConfig.key_name)).scalars().all()
    return [
        SystemConfigOut(
            key_name=r.key_name,
            value=r.value,
            value_type=r.value_type,
            description=r.description,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.get(
    "/config/{key}",
    response_model=SystemConfigOut,
    summary="读单条配置",
)
def get_config(
    key: str,
    _admin: CurrentUser = Depends(require_role("admin")),
    db: OrmSession = Depends(get_db),
) -> SystemConfigOut:
    row = db.get(SystemConfig, key)
    if row is None:
        raise HTTPException(404, f"配置项 {key} 不存在")
    return SystemConfigOut(
        key_name=row.key_name,
        value=row.value,
        value_type=row.value_type,
        description=row.description,
        updated_at=row.updated_at,
    )


@router.put(
    "/config/{key}",
    response_model=SystemConfigOut,
    summary="写/改配置 + 同事务写 config_change_log(spec §5.3.1)",
)
def upsert_config(
    key: str,
    payload: SystemConfigUpsert,
    admin: CurrentUser = Depends(require_role("admin")),
    db: OrmSession = Depends(get_db),
) -> SystemConfigOut:
    existing = db.get(SystemConfig, key)
    old_value = existing.value if existing else None

    if existing is None:
        existing = SystemConfig(
            key_name=key,
            value=payload.value,
            value_type=payload.value_type,
            description=payload.description,
            updated_by=admin.user_id,
        )
        db.add(existing)
    else:
        existing.value = payload.value
        if payload.value_type is not None:
            existing.value_type = payload.value_type
        if payload.description is not None:
            existing.description = payload.description
        existing.updated_by = admin.user_id

    # 同事务写变更日志(spec §5.3.1 末)
    db.add(
        ConfigChangeLog(
            operator_id=admin.user_id,
            config_key=key,
            old_value=old_value,
            new_value=payload.value,
            change_reason=payload.change_reason,
        )
    )
    db.flush()
    db.refresh(existing)
    return SystemConfigOut(
        key_name=existing.key_name,
        value=existing.value,
        value_type=existing.value_type,
        description=existing.description,
        updated_at=existing.updated_at,
    )


@router.delete(
    "/config/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除配置 + 写 config_change_log(new_value=null 标记删除)",
)
def delete_config(
    key: str,
    admin: CurrentUser = Depends(require_role("admin")),
    db: OrmSession = Depends(get_db),
) -> None:
    row = db.get(SystemConfig, key)
    if row is None:
        raise HTTPException(404, f"配置项 {key} 不存在")
    db.add(
        ConfigChangeLog(
            operator_id=admin.user_id,
            config_key=key,
            old_value=row.value,
            new_value=None,
            change_reason="DELETE via /admin/config",
        )
    )
    db.delete(row)


# ────────────────────────────────────────────────────────────────────────────
# 知识库上传(stub — C7 pipeline 入口未做完)
# ────────────────────────────────────────────────────────────────────────────


@router.post(
    "/kb/upload",
    response_model=KbUploadStubResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="(stub)知识库上传 — C7 pipeline 完工后实现",
)
def upload_kb_stub(
    _admin: CurrentUser = Depends(require_role("admin")),
) -> KbUploadStubResponse:
    return KbUploadStubResponse(
        status="unimplemented",
        instruction=(
            "C7 ingestion pipeline 入口函数尚未完工。当前请走脚本路径:"
            "scripts/batch_parse_pdfs.sh 触发 MinerU 解析 → "
            "scripts/load_chunks_to_pg.py 灌 PG → "
            "scripts/load_chunk_embeddings_to_milvus.py 灌 Milvus。"
            "C7 完工后本端点改为 multipart 文件上传 + 后台 task + kb_change_log 写入。"
        ),
    )
