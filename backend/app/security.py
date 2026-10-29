"""Passwords, JWT access tokens, refresh tokens and controller API keys.

* Passwords: scrypt (Python standard library), format "scrypt$N$r$p$salt$hash".
* Access tokens: short-lived JWT (HS256). Sent as "Authorization: Bearer ..." by the
  Android app, and as an httpOnly cookie by the website.
* Refresh tokens: random 256-bit strings; only their SHA-256 is stored.
* Controller keys (Raspberry Pi): "<8-char prefix>.<secret>", sent in the
  "X-Controller-Key" header; only the SHA-256 of the full key is stored.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --- JWT -------------------------------------------------------------------
def create_access_token(user_id: uuid.UUID, role: str, locale: str) -> tuple[str, int]:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_in = settings.access_token_minutes * 60
    payload = {
        "sub": str(user_id),
        "role": role,
        "locale": locale,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), expires_in


def create_customer_access_token(customer_id: uuid.UUID, locale: str) -> tuple[str, int]:
    """Access token of a driver (Android app). typ "customer": never accepted by the staff endpoints."""
    settings = get_settings()
    now = datetime.now(UTC)
    expires_in = settings.access_token_minutes * 60
    payload = {
        "sub": str(customer_id),
        "locale": locale,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "typ": "customer",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), expires_in


def decode_customer_access_token(token: str) -> dict:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("typ") != "customer":
        raise jwt.InvalidTokenError("not a customer token")
    return payload


def new_sms_code() -> tuple[str, str]:
    """Return (6-digit code sent by SMS, hash to store)."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    return code, sha256_hex(code)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("not an access token")
    return payload


def new_refresh_token() -> tuple[str, str, datetime]:
    """Return (token to give to the client, hash to store, expiry)."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days)
    return token, sha256_hex(token), expires


# --- Controller API keys ----------------------------------------------------
def new_controller_key() -> tuple[str, str, str]:
    """Return (full key shown once to the installer, prefix, hash to store)."""
    prefix = secrets.token_hex(4)
    key = f"{prefix}.{secrets.token_urlsafe(32)}"
    return key, prefix, sha256_hex(key)


def split_controller_key(key: str) -> str | None:
    prefix, sep, secret = key.partition(".")
    if not sep or len(prefix) != 8 or not secret:
        return None
    return prefix


def controller_key_matches(key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(sha256_hex(key), stored_hash)
