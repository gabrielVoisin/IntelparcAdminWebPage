"""Bluetooth identity tokens: server signature, expiry, phone proof (no database needed)."""

import base64
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.services import ble_identity


def phone_key() -> tuple[ec.EllipticCurvePrivateKey, str]:
    """Same key type as the Android Keystore key of the app (EC P-256)."""
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, base64.b64encode(spki).decode()


def test_token_round_trip():
    _, pk = phone_key()
    cid, vid = uuid.uuid4(), uuid.uuid4()
    token, exp = ble_identity.issue_token(cid, [(vid, "GE215908")], pk, ttl_s=3600)
    payload = ble_identity.verify_token(token, ble_identity.public_key_b64())
    assert payload["cid"] == str(cid)
    assert payload["veh"] == [{"id": str(vid), "plate": "GE215908"}]
    assert payload["pk"] == pk and payload["exp"] == exp


def test_tampered_token_is_refused():
    _, pk = phone_key()
    token, _ = ble_identity.issue_token(uuid.uuid4(), [], pk, ttl_s=3600)
    encoded, sig = token.split(".")
    other_payload = ble_identity.b64u(ble_identity.b64u_decode(encoded).replace(b'"v":1', b'"v":1 '))
    with pytest.raises(ble_identity.TokenError, match="ble_token_invalid"):
        ble_identity.verify_token(f"{other_payload}.{sig}", ble_identity.public_key_b64())


def test_expired_token_is_refused():
    _, pk = phone_key()
    token, _ = ble_identity.issue_token(uuid.uuid4(), [], pk, ttl_s=60, now=int(time.time()) - 3600)
    with pytest.raises(ble_identity.TokenError, match="ble_token_expired"):
        ble_identity.verify_token(token, ble_identity.public_key_b64())


def test_only_p256_phone_keys_are_accepted():
    rsa_like = base64.b64encode(b"not a key").decode()
    with pytest.raises(ble_identity.TokenError, match="ble_public_key_invalid"):
        ble_identity.issue_token(uuid.uuid4(), [], rsa_like, ttl_s=60)
    p384 = ec.generate_private_key(ec.SECP384R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    with pytest.raises(ble_identity.TokenError):
        ble_identity.issue_token(uuid.uuid4(), [], base64.b64encode(p384).decode(), ttl_s=60)


def test_phone_proof_is_checked_with_the_key_in_the_token():
    key, pk = phone_key()
    message = ble_identity.proof_message("CTRL-01", "q3cB2a9Zm0x8m3UWbQ1zZg", None)
    assert message == b"intelpark-ble-v1|CTRL-01|q3cB2a9Zm0x8m3UWbQ1zZg|"
    signature = key.sign(message, ec.ECDSA(hashes.SHA256()))  # what Signature("SHA256withECDSA") produces on Android
    ble_identity.load_phone_public_key(pk).verify(signature, message, ec.ECDSA(hashes.SHA256()))
