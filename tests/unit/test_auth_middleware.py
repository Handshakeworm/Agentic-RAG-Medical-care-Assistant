"""tests/unit/test_auth_middleware.py — JWT + bcrypt 纯算法 unit 测试。

不连 PG/Redis。只验:
- bcrypt hash/verify roundtrip + 哈希自带 salt → 同明文哈希结果不同
- JWT encode/decode roundtrip + 过期/伪造/缺字段统一 401
- require_role 工厂行为(允许角色通过、拒角色返 403)

闭环 endpoint 测试(需要真 PG)走 tests/integration/test_auth_routes.py。
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import jwt
import pytest
from fastapi import HTTPException

from src.api.middleware.auth_middleware import (
    CurrentUser,
    decode_access_token,
    encode_access_token,
    hash_password,
    require_role,
    verify_password,
)
from config.settings import settings


# ────────────────────────────────────────────────────────────────────────────
# 密码哈希
# ────────────────────────────────────────────────────────────────────────────


def test_hash_password_returns_ascii_str() -> None:
    """bcrypt 哈希结果是 60 字符 ASCII 字符串(可直接进 DB TEXT 列)。"""
    h = hash_password("hunter2")
    assert isinstance(h, str)
    assert h.isascii()
    assert h.startswith("$2")  # bcrypt 标识


def test_hash_password_salt_differs_each_call() -> None:
    """同明文每次哈希不同(salt 随机)— 防 rainbow table。"""
    assert hash_password("same") != hash_password("same")


def test_verify_password_roundtrip() -> None:
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True
    assert verify_password("wrong", h) is False


def test_verify_password_garbage_hash_returns_false() -> None:
    """异常哈希格式不抛 — 防止泄漏内部状态(timing/exception 探测)。"""
    assert verify_password("anything", "not-a-valid-bcrypt-hash") is False
    assert verify_password("anything", "") is False


# ────────────────────────────────────────────────────────────────────────────
# JWT 签发 + 校验
# ────────────────────────────────────────────────────────────────────────────


def test_encode_decode_roundtrip() -> None:
    token = encode_access_token(user_id="u-123", role="patient", email="a@b.com")
    payload = decode_access_token(token)
    assert payload["sub"] == "u-123"
    assert payload["role"] == "patient"
    assert payload["email"] == "a@b.com"
    assert "exp" in payload and "iat" in payload


def test_decode_expired_token_returns_401() -> None:
    """spec §G2:过期 token → 401。"""
    token = encode_access_token(
        user_id="u-123", role="patient", expires_delta=timedelta(seconds=-1)
    )
    with pytest.raises(HTTPException) as exc:
        decode_access_token(token)
    assert exc.value.status_code == 401
    assert "过期" in exc.value.detail


def test_decode_tampered_signature_returns_401() -> None:
    """token 被改一个字 → 签名校验失败 → 401。"""
    token = encode_access_token(user_id="u-123", role="patient")
    tampered = token[:-3] + ("ABC" if token[-3:] != "ABC" else "XYZ")
    with pytest.raises(HTTPException) as exc:
        decode_access_token(tampered)
    assert exc.value.status_code == 401


def test_decode_wrong_secret_returns_401() -> None:
    """用别的密钥签的 token 进来 → 401。"""
    fake = jwt.encode(
        {"sub": "u-123", "role": "patient", "exp": 9999999999},
        "different-secret",
        algorithm=settings.jwt.ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        decode_access_token(fake)
    assert exc.value.status_code == 401


def test_decode_token_missing_sub_returns_401() -> None:
    """payload 缺 sub → 401(防止"用别人密钥签了个空 token 又凑巧密钥一致"的极端 case)。"""
    bad = jwt.encode(
        {"role": "patient", "exp": 9999999999},
        settings.jwt.SECRET_KEY,
        algorithm=settings.jwt.ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        decode_access_token(bad)
    assert exc.value.status_code == 401
    assert "缺必要字段" in exc.value.detail


# ────────────────────────────────────────────────────────────────────────────
# require_role
# ────────────────────────────────────────────────────────────────────────────


def test_require_role_allows_matching_role() -> None:
    check = require_role("admin")
    user = CurrentUser(user_id="u", role="admin", email=None)
    assert check(user) is user  # 通过


def test_require_role_rejects_other_role_with_403() -> None:
    check = require_role("admin")
    user = CurrentUser(user_id="u", role="patient", email=None)
    with pytest.raises(HTTPException) as exc:
        check(user)
    assert exc.value.status_code == 403


def test_require_role_accepts_multiple_allowed_roles() -> None:
    """场景:某端点 admin 和 patient 都能访问(只挡未实现的 'doctor')。"""
    check = require_role("admin", "patient")
    assert check(CurrentUser(user_id="u", role="admin")).role == "admin"
    assert check(CurrentUser(user_id="u", role="patient")).role == "patient"
    with pytest.raises(HTTPException):
        check(CurrentUser(user_id="u", role="doctor"))


def test_require_role_empty_args_raises() -> None:
    """误用防护:require_role() 不写参数 → 启动时炸,不让悄悄放行所有人。"""
    with pytest.raises(ValueError):
        require_role()


# ────────────────────────────────────────────────────────────────────────────
# settings.jwt 读取冒烟
# ────────────────────────────────────────────────────────────────────────────


def test_token_expiration_respects_settings(monkeypatch) -> None:
    """ACCESS_TOKEN_EXPIRE_MINUTES 改 → 新签 token 的 exp - iat 跟着变。"""
    with patch.object(settings.jwt, "ACCESS_TOKEN_EXPIRE_MINUTES", 30):
        token = encode_access_token(user_id="u", role="patient")
    payload = decode_access_token(token)
    delta = payload["exp"] - payload["iat"]
    assert 29 * 60 <= delta <= 31 * 60  # 30 分钟,±60s 容差
