"""End-to-end tests of the driver app API and the Bluetooth identification (real PostgreSQL + demo data).

    export DATABASE_URL=postgresql+psycopg://parking:parking@localhost:5432/parking_test
    alembic upgrade head && python -m app.cli seed-demo && pytest tests/test_driver_api.py
Skipped automatically when no database is configured.
"""

import base64
import os
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="needs a PostgreSQL test database")

PI_KEY = {"X-Controller-Key": "ctrl0001.demo-secret-change-me-1"}
API = "/api/v1/app"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="module")
def driver(client):
    """Full sign-up: account, plate, address, verified phone."""
    email = f"driver-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post(f"{API}/auth/register", json={"first_name": "Alex", "last_name": "Test", "email": email,
                                                  "password": "Secret-2026!", "client_name": "pytest"})
    assert r.status_code == 201, r.text
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert r.json()["driver"]["reference"].startswith("C-1")
    plate = f"TST-{uuid.uuid4().hex[:5].upper()}"
    assert client.post(f"{API}/vehicles", json={"plate_display": plate, "country": "CH"}, headers=auth).status_code == 201
    assert client.patch(f"{API}/me", json={"address_line": "Rue du Marché 8", "postcode": "74100", "city": "Annemasse"},
                        headers=auth).status_code == 200
    code = client.post(f"{API}/me/phone", json={"phone": "+41 79 123 45 67"}, headers=auth).json()["debug_code"]
    me = client.post(f"{API}/me/phone/verify", json={"code": code}, headers=auth).json()
    assert me["phone"] == "+41791234567" and me["phone_verified"] and me["profile_complete"]
    return {"auth": auth, "email": email, "plate": plate, "me": me, "refresh": r.json()["refresh_token"]}


def test_register_twice_and_login(client, driver):
    r = client.post(f"{API}/auth/register", json={"first_name": "A", "last_name": "B", "email": driver["email"].upper(),
                                                  "password": "Another-2026!"})
    assert r.status_code == 409 and r.json()["detail"] == "email_already_used"
    assert client.post(f"{API}/auth/login", json={"email": driver["email"], "password": "wrong"}).status_code == 401
    assert client.post(f"{API}/auth/login", json={"email": driver["email"], "password": "Secret-2026!"}).status_code == 200


def test_tokens_do_not_cross(client, driver):
    """A driver token never opens the staff API, and a staff token never opens the driver API."""
    assert client.get("/api/v1/dashboard", headers=driver["auth"]).status_code == 401
    staff = client.post("/api/v1/auth/login", json={"email": "responsable@example.com", "password": "ChangeMe-2026!"}).json()
    assert client.get(f"{API}/me", headers={"Authorization": f"Bearer {staff['access_token']}"}).status_code == 401


def test_wrong_phone_code_is_counted(client, driver):
    client.post(f"{API}/me/phone", json={"phone": "+41791234567"}, headers=driver["auth"])
    for _ in range(5):
        assert client.post(f"{API}/me/phone/verify", json={"code": "000000"}, headers=driver["auth"]).status_code in (422, 410)
    assert client.post(f"{API}/me/phone/verify", json={"code": "000000"}, headers=driver["auth"]).status_code == 410


def test_map_qr_quote_and_pay(client, driver):
    sites = client.get(f"{API}/sites", params={"lat": 46.19, "lng": 6.24}, headers=driver["auth"]).json()
    assert sites[0]["distance_m"] is not None and sites[0]["hourly"]["amount_cents"] > 0
    nearest = client.get(f"{API}/nearest", params={"lat": 46.19, "lng": 6.24}, headers=driver["auth"]).json()
    assert nearest["bay"]["status"] == "free"
    bay = client.get(f"{API}/bays/by-code", params={"code": f"intelpark:bay:{nearest['bay']['code']}"}, headers=driver["auth"]).json()
    assert bay["id"] == nearest["bay"]["id"]
    q = client.post(f"{API}/quote", json={"bay_id": bay["id"], "minutes": 90}, headers=driver["auth"]).json()
    assert q["price"]["amount_cents"] > 0
    s = client.post(f"{API}/sessions", json={"bay_id": bay["id"], "minutes": 90}, headers=driver["auth"])
    assert s.status_code == 201, s.text
    session = s.json()
    assert session["identified_by"] == "app" and session["right"] == "paid_session"
    assert session["last_payment"]["amount_cents"] == q["price"]["amount_cents"]
    ext = client.post(f"{API}/sessions/{session['id']}/extend", json={"minutes": 30}, headers=driver["auth"]).json()
    assert ext["paid_until"] > session["paid_until"]
    assert client.get(f"{API}/home", headers=driver["auth"]).json()["current_session"]["id"] == session["id"]


def test_book_cancel_and_subscribe(client, driver):
    p1 = next(s for s in client.get(f"{API}/sites", headers=driver["auth"]).json() if s["code"] == "P1")
    start = datetime.now(UTC) + timedelta(days=2)
    r = client.post(f"{API}/reservations", json={"site_id": p1["id"], "starts_at": start.isoformat(),
                                                 "ends_at": (start + timedelta(hours=2)).isoformat()}, headers=driver["auth"])
    assert r.status_code == 201, r.text
    cancelled = client.post(f"{API}/reservations/{r.json()['id']}/cancel", headers=driver["auth"]).json()
    assert cancelled["status"] == "cancelled"
    plan = client.get(f"{API}/subscription-plans", headers=driver["auth"]).json()[0]
    sub = client.post(f"{API}/subscriptions", json={"site_id": p1["id"], "plan_id": plan["id"],
                                                    "starts_on": date.today().isoformat()}, headers=driver["auth"])
    assert sub.status_code == 201, sub.text
    assert sub.json()["status"] == "active"


def _phone_key():
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, base64.b64encode(spki).decode()


def test_bluetooth_token_and_identification(client, driver):
    _, pk = _phone_key()
    tok = client.post(f"{API}/ble/token", json={"public_key": pk, "device_name": "pytest"}, headers=driver["auth"])
    assert tok.status_code == 200, tok.text
    config = client.get("/api/v1/device/config", headers=PI_KEY).json()
    assert config["ble"]["public_key"] == tok.json()["server_public_key"]

    from app.services import ble_identity

    payload = ble_identity.verify_token(tok.json()["token"], config["ble"]["public_key"])
    # Free bay EXT 03 of P1: a car arrives, then the cabinet identifies the phone (no bay given)
    bay03 = next(d for d in config["devices"] if d["code"] == "CPV-EXT-03")
    now = datetime.now(UTC)
    client.post("/api/v1/device/readings", headers=PI_KEY, json={"readings": [
        {"uid": f"r-{time.time_ns()}", "device_code": bay03["code"], "is_occupied": True, "measured_at": now.isoformat()}]})
    ident = {"uid": f"ble-{time.time_ns()}", "customer_id": payload["cid"], "rssi": -60, "identified_at": now.isoformat()}
    r = client.post("/api/v1/device/identifications", headers=PI_KEY, json={"identifications": [ident]})
    assert r.status_code == 200, r.text
    decision = r.json()["decisions"][0]
    assert decision["bay_id"] == bay03["bay_id"]
    assert decision["status"] in ("payment_required", "authorised")  # authorised once the driver has subscribed
    # idempotent: the Pi resends after a network failure
    again = client.post("/api/v1/device/identifications", headers=PI_KEY, json={"identifications": [ident]}).json()
    assert again["decisions"][0] == decision
