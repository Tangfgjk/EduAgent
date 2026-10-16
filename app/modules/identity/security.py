from __future__ import annotations

import hashlib
import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.modules.errors import DomainError

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def username(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", value):
        raise DomainError(422, "invalid_username", "账号须为 3–64 位字母、数字或 ._-，以字母或数字开头")
    return value


def password_hash(value: str) -> str:
    if not 12 <= len(value) <= 128:
        raise DomainError(422, "invalid_password", "密码长度须为 12–128 个字符")
    return HASHER.hash(value)


def verify(encoded: str, value: str) -> bool:
    try:
        return HASHER.verify(encoded, value)
    except (VerificationError, InvalidHashError):
        return False
