"""Auth 接口 Pydantic 请求/响应模型(DEV_SPEC §8.4 G2)。

放在 `src/api/schemas/` 而不是 `src/api/routes/auth.py` 内部 — 与 G4
`diagnosis_schema.py` / G5 `patient_schema.py` 命名一致;routes 里只剩业务 wiring,
schema 单独 import 利于前端联调时复用 OpenAPI 客户端代码生成。

设计取舍:
- `RegisterRequest.role` 走 `Literal["patient", "admin"]` 而非自由 str —
  防止前端误传 'doctor' / 'staff' 等本期未实现的角色,API 层兜住校验
- `password` 用 `SecretStr` 让 OpenAPI docs 渲染成密码框 + log/repr 时不泄漏明文
- `TokenResponse.token_type` 固定 'bearer' 但仍写在 schema 里 —
  OAuth2 / FastAPI security 客户端约定,前端 Authorization header 拼前缀用
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, SecretStr


# ────────────────────────────────────────────────────────────────────────────
# 注册
# ────────────────────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    """POST /auth/register 请求体。

    `role` 默认 'patient' — 真实业务里 admin 由运维手动建,不开放注册。
    保留 admin 选项是为了测试/seed 方便,生产部署应在 G6 admin 接口里加约束。
    """

    email: EmailStr
    password: SecretStr = Field(..., min_length=6, max_length=128)
    role: Literal["patient", "admin"] = "patient"


# ────────────────────────────────────────────────────────────────────────────
# 登录 + Token
# ────────────────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    """POST /auth/login 请求体。"""

    email: EmailStr
    password: SecretStr


class TokenResponse(BaseModel):
    """注册 / 登录共同响应 — 返新签发的 access token。"""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in_seconds: int  # 客户端用来定时 refresh / 提前重新登录


# ────────────────────────────────────────────────────────────────────────────
# /auth/me 当前身份
# ────────────────────────────────────────────────────────────────────────────


class UserOut(BaseModel):
    """GET /auth/me 响应体。来源:JWT payload(不打 DB),含字段必为登录时塞进的。"""

    user_id: str
    email: str | None
    role: str
