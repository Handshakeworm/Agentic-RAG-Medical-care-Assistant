"""JWT 认证 + 角色守卫(DEV_SPEC §8.4 G2 / §5.3.2)。

提供四组工具:
- `hash_password` / `verify_password`           — bcrypt 包装(密码不可逆,login 比对走 verify)
- `encode_access_token` / `decode_access_token` — JWT 签发/校验,密钥来自 settings.jwt
- `get_current_user`                            — FastAPI Depends,从 Authorization header
                                                  解 token 返 `CurrentUser`,失败 401
- `require_role(*roles)`                        — Depends 工厂,挡角色不符的请求 403

设计取舍(对齐 spec):
- **不写 ASGI middleware 强制全局拦截** — `/auth/login`、`/metrics`、未来的 `/healthz`
  都不需要 token,用 `Depends(get_current_user)` 显式声明的端点才校验,符合 FastAPI 惯例
- **bcrypt 5.x cost=12** 默认 — 单次哈希 ~200ms,够慢挡暴力破解,又不至于把注册接口拖到秒级
- **JWT 用 HS256 对称加密** — secret 由部署侧通过 `JWT_SECRET_KEY` env 注入(spec §5.3:
  JWT secret 走 .env,不存 DB)。生产环境**必须**改掉 .env.example 里的占位密钥
- **`CurrentUser` 是 dataclass 而非 ORM `User` 实例** — 解 token 不查 DB,避免每个请求
  都打一次 PG;真要查全量 User 字段,业务端再 `db.get(User, current_user.user_id)`
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import settings


_logger = logging.getLogger(__name__)

# OpenAPI docs 上 "Authorize" 按钮用 Bearer scheme;auto_error=False 让我们自己抛
# 标准化的 401(默认 HTTPBearer 抛 403,语义对不上"未认证")
_bearer_scheme = HTTPBearer(auto_error=False)


# ────────────────────────────────────────────────────────────────────────────
# CurrentUser — Depends 注入业务端用的轻量身份对象
# ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CurrentUser:
    """业务端注入对象。仅含从 JWT payload 解出的字段,不含 password / created_at。

    要拿全量 ORM 行:`db.get(User, current_user.user_id)`(rare,大多业务只用 id+role)。
    """

    user_id: str
    role: str
    email: str | None = None  # 可选,登录时塞进 token 便于 `/auth/me` 直接返


# ────────────────────────────────────────────────────────────────────────────
# 密码哈希(bcrypt)
# ────────────────────────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    """bcrypt 哈希。返回 ASCII 字符串,可直接写 users.password 列。"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """常时间(constant-time)比对。哈希格式异常 → False(不抛,避免泄漏内部状态)。"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return False


# ────────────────────────────────────────────────────────────────────────────
# JWT 签发 + 校验
# ────────────────────────────────────────────────────────────────────────────


def encode_access_token(
    *,
    user_id: str,
    role: str,
    email: str | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """签发 access token。`exp` claim 走 settings.jwt.ACCESS_TOKEN_EXPIRE_MINUTES。

    PyJWT 在 verify 时会自动比对 `exp` 与当前 UTC 时间,无需业务端手动算过期。
    """
    now = datetime.now(timezone.utc)
    delta = expires_delta or timedelta(
        minutes=settings.jwt.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + delta).timestamp()),
    }
    if email:
        payload["email"] = email
    return jwt.encode(
        payload, settings.jwt.SECRET_KEY, algorithm=settings.jwt.ALGORITHM
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """校验签名 + 过期。失败统一抛 401(过期 / 签名错 / claim 缺 sub 都是同一类错)。"""
    try:
        payload = jwt.decode(
            token,
            settings.jwt.SECRET_KEY,
            algorithms=[settings.jwt.ALGORITHM],
        )
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 已过期,请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except jwt.InvalidTokenError as e:
        # 涵盖 InvalidSignatureError / DecodeError / MissingRequiredClaimError 等所有子类
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 无效",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    if "sub" not in payload or "role" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token payload 缺必要字段",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


# ────────────────────────────────────────────────────────────────────────────
# FastAPI Depends:get_current_user / require_role
# ────────────────────────────────────────────────────────────────────────────


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    """注入到业务端点:`def view(..., user: CurrentUser = Depends(get_current_user))`。

    无 Authorization header / token 解析失败 → 401。
    """
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(creds.credentials)
    return CurrentUser(
        user_id=payload["sub"],
        role=payload["role"],
        email=payload.get("email"),
    )


def require_role(*allowed_roles: str):
    """Depends 工厂:挡角色不符的请求(403)。

    用法:`def admin_view(..., _ = Depends(require_role("admin")))`。
    `_` 占位是因为我们只关心副作用(挡 403),不需要返回值;真要拿 user
    用 `Depends(get_current_user)` 即可。

    spec §5.3.2:角色集 `patient` / `admin`(`doctor` 留给后续)。
    """

    if not allowed_roles:
        raise ValueError("require_role 至少需要一个允许的角色")

    def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要角色 {set(allowed_roles)},当前是 '{user.role}'",
            )
        return user

    return _check
