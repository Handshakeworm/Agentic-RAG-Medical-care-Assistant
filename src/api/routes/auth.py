"""Auth 路由(DEV_SPEC §8.4 G2)— 注册 / 登录 / 看自己身份。

3 个端点:
- `POST /auth/register` 写 users 表 + 返新签 token(注册即登录,免去前端二次请求)
- `POST /auth/login`    校验密码 + 返 token
- `GET  /auth/me`       读 JWT payload 返当前身份(不打 DB,纯解 token)

下一步任务依赖关系:
- G4 `POST /diagnose` 用 `Depends(get_current_user)` 拿 patient_id
- G5 `/patients/...` 用 `Depends(require_role("patient"))` 限自己改自己的数据
- G6 `/admin/...` 用 `Depends(require_role("admin"))`
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.settings import settings
from src.api.middleware.auth_middleware import (
    CurrentUser,
    encode_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from src.api.schemas.auth_schema import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from src.db.postgres.connection import _get_session_factory
from src.db.postgres.models_patient import User


_logger = logging.getLogger(__name__)
router = APIRouter()


# ────────────────────────────────────────────────────────────────────────────
# DB session Depends — 让端点内部不直接管 session_scope
# ────────────────────────────────────────────────────────────────────────────


def get_db():
    """FastAPI 风格 DB session 注入。

    与 `session_scope` 区别:Depends 生命周期 = 一个 HTTP 请求,异常 rollback、
    成功 commit 由 FastAPI 框架在 finally 兜住。session_scope 是普通脚本/任务用的
    上下文管理器。两者都走同一个 session factory,数据一致性由 SQLAlchemy 兜底。
    """
    session: Session = _get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _make_token_response(user: User) -> TokenResponse:
    """复用 register / login 两端的 token 签发逻辑。"""
    token = encode_access_token(user_id=user.id, role=user.role, email=user.email)
    return TokenResponse(
        access_token=token,
        expires_in_seconds=settings.jwt.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# ────────────────────────────────────────────────────────────────────────────
# POST /auth/register
# ────────────────────────────────────────────────────────────────────────────


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="注册并签发 token",
)
def register(req: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """注册即登录 — 返新签 token,前端拿到后立刻可调业务接口。

    email 已存在 → 409。spec §2.4.5 users.email UNIQUE,DB 兜底 IntegrityError。
    """
    user = User(
        email=req.email,
        password=hash_password(req.password.get_secret_value()),
        role=req.role,
    )
    db.add(user)
    try:
        db.flush()  # 触发 INSERT 拿到 id;commit 在 get_db 退出时
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"邮箱 {req.email} 已被注册",
        ) from e
    db.refresh(user)
    return _make_token_response(user)


# ────────────────────────────────────────────────────────────────────────────
# POST /auth/login
# ────────────────────────────────────────────────────────────────────────────


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="登录返 token",
)
def login(req: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """密码错 / 用户不存在 → 统一 401(不暴露"用户不存在"避免枚举攻击)。"""
    user: User | None = (
        db.query(User).filter(User.email == req.email).one_or_none()
    )
    if user is None or not verify_password(
        req.password.get_secret_value(), user.password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _make_token_response(user)


# ────────────────────────────────────────────────────────────────────────────
# GET /auth/me
# ────────────────────────────────────────────────────────────────────────────


@router.get(
    "/me",
    response_model=UserOut,
    summary="返当前 token 持有者身份",
)
def me(current_user: CurrentUser = Depends(get_current_user)) -> UserOut:
    """纯解 token,不打 DB — 想要全量字段(name/birth_date 等)走 G5 `/patients/me`。"""
    return UserOut(
        user_id=current_user.user_id,
        email=current_user.email,
        role=current_user.role,
    )
