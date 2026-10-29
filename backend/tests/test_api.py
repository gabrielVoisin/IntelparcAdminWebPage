"""End-to-end API tests against a real PostgreSQL loaded with seed_demo.sql.

    export DATABASE_URL=postgresql+psycopg://parking:parking@localhost:5432/parking_test
    alembic upgrade head && python -m app.cli seed-demo && pytest
Skipped automatically when no database is configured.
"""
import os
import uuid
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="needs a PostgreSQL test database")

PI_KEY = {"X-Controller-Key": "ctrl0001.demo-secret-change-me-1"}


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="module")
def admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "responsable@example.com", "password": "ChangeMe-2026!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_health(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_login_rejects_bad_password(client):
    r = client.post("/api/v1/auth/login", json={"email": "responsable@example.com", "password": "nope"})
    assert r.status_code == 401


def test_dashboard_and_alarms(client, admin):
    dash = client.get("/api/v1/dashboard", headers=admin).json()
    assert dash["total_bays"] > 0
    alarms = client.get("/api/v1/payment-alarms", headers=admin).json()
    assert any(a["plate"] for a in alarms)


def test_refresh_rotation(client):
    r = client.post("/api/v1/auth/login", json={"email": "responsable@example.com", "password": "ChangeMe-2026!"}).json()
    first = client.post("/api/v1/auth/refresh", json={"refresh_token": r["refresh_token"]})
    assert first.status_code == 200
    again = client.post("/api/v1/auth/refresh", json={"refresh_token": r["refresh_token"]})
    assert again.status_code == 401  # a refresh token works only once


def test_cookie_write_needs_csrf_header(client, admin):
    r = client.post("/api/v1/auth/login", json={"email": "responsable@example.com", "password": "ChangeMe-2026!"})
    client.cookies.set("pk_access", r.json()["access_token"])
    assert client.put("/api/v1/settings", json={"values": {}}).status_code == 403
    client.cookies.clear()


def test_pi_requires_key(client):
    assert client.get("/api/v1/device/config").status_code == 401
    assert client.get("/api/v1/device/config", headers={"X-Controller-Key": "ctrl0001.wrong"}).status_code == 401


def test_pi_config_heartbeat_and_idempotent_events(client):
    cfg = client.get("/api/v1/device/config", headers=PI_KEY).json()
    assert cfg["controller_code"] == "CTRL-01" and cfg["devices"]
    assert client.post("/api/v1/device/heartbeat", headers=PI_KEY, json={"agent_version": "1.0.0"}).json()["ok"]
    event = {"uid": f"test-{uuid.uuid4()}", "code": "CAM-01", "priority": "alarm",
             "message_key": "device.camera_faulty", "raised_at": datetime.now(UTC).isoformat()}
    first = client.post("/api/v1/device/events", headers=PI_KEY, json={"events": [event]}).json()
    second = client.post("/api/v1/device/events", headers=PI_KEY, json={"events": [event]}).json()
    assert first["accepted"] == 1 and second["duplicates"] == 1


def test_pi_reading_frees_a_bay(client):
    sensor = next(d for d in client.get("/api/v1/device/config", headers=PI_KEY).json()["devices"]
                  if d["kind"] == "vertical_presence_sensor")
    reading = {"uid": f"test-{uuid.uuid4()}", "device_code": sensor["code"], "is_occupied": False,
               "measured_at": datetime.now(UTC).isoformat()}
    assert client.post("/api/v1/device/readings", headers=PI_KEY, json={"readings": [reading]}).json()["accepted"] == 1
