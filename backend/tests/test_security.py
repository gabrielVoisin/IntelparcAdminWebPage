import uuid

import jwt
import pytest

from app.security import (
    controller_key_matches, create_access_token, decode_access_token, hash_password,
    new_controller_key, new_refresh_token, sha256_hex, split_controller_key, verify_password,
)

SEED_HASH = "scrypt$16384$8$1$8I9IHLLJd8cJ4J3WQ4zt8Q==$9DMxTwBhitOj9X6pYKSOgSlwBHEPZ9EK3E+YDykR/SM="


def test_password_round_trip():
    stored = hash_password("correct horse battery")
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("anything", None)


def test_demo_seed_password_matches():
    assert verify_password("ChangeMe-2026!", SEED_HASH)


def test_access_token_round_trip():
    user_id = uuid.uuid4()
    token, expires_in = create_access_token(user_id, "manager", "de")
    payload = decode_access_token(token)
    assert payload["sub"] == str(user_id)
    assert payload["role"] == "manager"
    assert payload["locale"] == "de"
    assert expires_in > 0


def test_tampered_token_is_rejected():
    token, _ = create_access_token(uuid.uuid4(), "admin", "fr")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1])


def test_refresh_token_only_hash_is_kept():
    token, token_hash, expires = new_refresh_token()
    assert token_hash == sha256_hex(token) and token not in token_hash
    assert expires is not None


def test_controller_key():
    key, prefix, key_hash = new_controller_key()
    assert split_controller_key(key) == prefix and len(prefix) == 8
    assert controller_key_matches(key, key_hash)
    assert not controller_key_matches(key + "x", key_hash)
    assert split_controller_key("no-dot-here") is None


def test_demo_controller_key_matches_seed():
    assert controller_key_matches(
        "ctrl0001.demo-secret-change-me-1", "863b469ff63b9d6eec8ee7a21fc74084179e149ca7ffded1624bce5a74f2a131"
    )
