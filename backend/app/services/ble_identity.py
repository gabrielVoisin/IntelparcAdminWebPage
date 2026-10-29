"""Bluetooth identity tokens for the driver app (Intel Park).

Goal: the "smart bay" must know WHICH driver is parked, even when the cabinet's Raspberry Pi
cannot reach the server. The phone therefore carries a token signed by the server, and proves
it owns that token with a key that never leaves the phone.

1. The app creates an EC P-256 key pair in the Android Keystore (not exportable) and sends the
   public key to POST /api/v1/app/ble/token.
2. The server returns a token = "<payload>.<signature>":
       payload   = base64url(JSON {"v":1, "cid": customer id, "veh": [{"id", "plate"}], "pk": phone public key,
                                   "iat", "exp"})
       signature = base64url(Ed25519 signature of the ASCII payload string)
   Every cabinet knows the server's Ed25519 PUBLIC key (GET /api/v1/device/config, field ble.public_key),
   so it verifies tokens offline. It cannot create them.
3. Over Bluetooth the cabinet sends a random, single-use challenge (valid 60 s); the phone signs
       "intelpark-ble-v1|<controller code>|<challenge>|<bay id or empty>"
   with its Keystore key (SHA256withECDSA). The cabinet checks this signature with the "pk" of the
   token: a token copied from another phone is useless without that phone's private key, and a
   recorded answer cannot be replayed (the challenge changes every time).

The Raspberry Pi side lives in raspberry-agent/agent/identity.py (same format, tested against this file).
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from functools import lru_cache

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.config import get_settings

TOKEN_VERSION = 1
PROOF_PREFIX = "intelpark-ble-v1"


class TokenError(ValueError):
    """Invalid, expired or forged token; the message is an error code (e.g. "ble_token_expired")."""


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@lru_cache
def signing_key() -> Ed25519PrivateKey:
    """The server's Ed25519 key: BLE_SIGNING_KEY if set, else derived from JWT_SECRET."""
    settings = get_settings()
    if settings.ble_signing_key:
        seed = base64.b64decode(settings.ble_signing_key)
        if len(seed) != 32:
            raise RuntimeError("BLE_SIGNING_KEY must be the base64 of exactly 32 bytes")
    else:
        seed = hashlib.sha256(b"intelpark-ble-signing-key|" + settings.jwt_secret.encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key_b64() -> str:
    """Raw 32-byte Ed25519 public key, standard base64: given to every cabinet."""
    raw = signing_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def load_phone_public_key(spki_b64: str) -> ec.EllipticCurvePublicKey:
    """Phone key as sent by Android (KeyPair.public.encoded = X.509 SubjectPublicKeyInfo, base64)."""
    try:
        key = serialization.load_der_public_key(base64.b64decode(spki_b64))
    except (ValueError, TypeError) as exc:
        raise TokenError("ble_public_key_invalid") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise TokenError("ble_public_key_invalid")
    return key


def issue_token(
    customer_id: uuid.UUID,
    vehicles: list[tuple[uuid.UUID, str]],
    phone_public_key_b64: str,
    ttl_s: int,
    now: int | None = None,
) -> tuple[str, int]:
    """Return (token, expiry unix time). vehicles = [(vehicle id, normalised plate)]."""
    load_phone_public_key(phone_public_key_b64)  # refuse anything that is not a P-256 key
    iat = int(now if now is not None else time.time())
    payload = {
        "v": TOKEN_VERSION,
        "cid": str(customer_id),
        "veh": [{"id": str(vid), "plate": plate} for vid, plate in vehicles],
        "pk": phone_public_key_b64,
        "iat": iat,
        "exp": iat + ttl_s,
    }
    encoded = b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = signing_key().sign(encoded.encode("ascii"))
    return f"{encoded}.{b64u(signature)}", payload["exp"]


def verify_token(token: str, public_key_b64: str, now: int | None = None) -> dict:
    """Check the server signature and the expiry; return the payload. Mirrors agent/identity.py."""
    try:
        encoded, sig = token.split(".")
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        public_key.verify(b64u_decode(sig), encoded.encode("ascii"))
        payload = json.loads(b64u_decode(encoded))
    except (ValueError, InvalidSignature) as exc:
        raise TokenError("ble_token_invalid") from exc
    if payload.get("v") != TOKEN_VERSION:
        raise TokenError("ble_token_invalid")
    if int(payload["exp"]) < int(now if now is not None else time.time()):
        raise TokenError("ble_token_expired")
    return payload


def proof_message(controller_code: str, challenge_b64u: str, bay_id: str | None) -> bytes:
    return f"{PROOF_PREFIX}|{controller_code}|{challenge_b64u}|{bay_id or ''}".encode()
