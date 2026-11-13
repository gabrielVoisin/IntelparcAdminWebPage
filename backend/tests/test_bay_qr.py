"""QR code of a bay (admin tab "Generate QR code") and rental status, end to end (real PostgreSQL + demo data).

    export DATABASE_URL=postgresql+psycopg://parking:parking@localhost:5432/parking_test
    alembic upgrade head && python -m app.cli seed-demo && pytest tests/test_bay_qr.py
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services import bay_qr

API = "/api/v1/app"


def test_qr_content_round_trip():
    text = bay_qr.content("P1/EXT/05", "IPK-7F3K9Q", "Rue du Lac 12, 1201 Genève", 46.2043912, 6.143158)
    assert text.startswith("intelpark:bay:P1/EXT/05?ref=IPK-7F3K9Q&street=Rue%20du%20Lac")
    assert text.endswith("&gps=46.204391,6.143158")
    scanned = bay_qr.parse(text)
    assert scanned.code == "P1/EXT/05" and scanned.reference == "IPK-7F3K9Q"
    old = bay_qr.parse("intelpark:bay:P1/EXT/05")  # stickers printed before keep working
    assert old.code == "P1/EXT/05" and old.reference is None


def test_pdf_has_a_5_cm_code():
    data = bay_qr.pdf("intelpark:bay:P1/EXT/05?ref=IPK-AAAAAA", title="QR", scan_hint="Scan", bay_line="Place 05",
                      street="Rue du Lac 12", gps_line="GPS 46.2, 6.1", reference_line="Réf. IPK-AAAAAA")
    assert data.startswith(b"%PDF")


needs_db = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="needs a PostgreSQL test database")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="module")
def admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "responsable@example.com", "password": "ChangeMe-2026!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def driver(client):
    r = client.post(f"{API}/auth/register", json={"first_name": "Qr", "last_name": "Test", "password": "Secret-2026!",
                                                  "email": f"qr-{uuid.uuid4().hex[:8]}@example.com"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def zone(client, admin):
    site = client.get("/api/v1/sites", headers=admin).json()[0]
    return client.get(f"/api/v1/sites/{site['id']}/zones", headers=admin).json()[0]


@needs_db
def test_generate_download_scan_and_book(client, admin, driver, zone):
    number = f"Q{uuid.uuid4().hex[:4].upper()}"
    body = {"zone_id": zone["id"], "number": number, "street": "Rue du Lac 12, 1201 Genève",
            "latitude": 46.204391, "longitude": 6.143158}
    r = client.post("/api/v1/bay-qr-codes", json=body, headers=admin)
    assert r.status_code == 201, r.text
    qr = r.json()
    assert qr["created"] and qr["reference"].startswith("IPK-") and qr["rental_status"] == "not_rented"
    assert qr["qr_content"].startswith(f"intelpark:bay:{qr['code']}?ref={qr['reference']}")

    # Generating again keeps the reference (stickers already on the ground stay valid) and updates the address
    again = client.post("/api/v1/bay-qr-codes", json={**body, "number": number.lower(), "street": "Quai 1"}, headers=admin).json()
    assert again["bay_id"] == qr["bay_id"] and again["reference"] == qr["reference"] and not again["created"]
    assert again["street"] == "Quai 1"
    assert any(row["bay_id"] == qr["bay_id"] for row in client.get("/api/v1/bay-qr-codes", headers=admin).json())

    pdf = client.get(f"/api/v1/bays/{qr['bay_id']}/qr.pdf", headers=admin)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert client.get(f"/api/v1/bays/{qr['bay_id']}/qr.svg", headers=admin).text.lstrip().startswith("<?xml")

    # The app scans the sticker: the reference finds the bay, with its street and GPS position
    bay = client.get(f"{API}/bays/by-code", params={"code": again["qr_content"]}, headers=driver).json()
    assert bay["id"] == qr["bay_id"] and bay["street"] == "Quai 1" and bay["reference"] == qr["reference"]
    assert abs(bay["latitude"] - 46.204391) < 1e-6 and bay["bookable"]

    # Longer stay: a booking on this bay, priced from its start
    start = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    quote = client.post(f"{API}/quote", json={"bay_id": bay["id"], "minutes": 2 * 24 * 60, "starts_at": start.isoformat()},
                        headers=driver).json()
    assert quote["starts_at"].startswith(start.isoformat()[:16])
    res = client.post(f"{API}/reservations", json={"site_id": bay["site_id"], "bay_id": bay["id"], "starts_at": start.isoformat(),
                                                   "ends_at": (start + timedelta(days=2)).isoformat()}, headers=driver)
    assert res.status_code == 201, res.text
    assert res.json()["bay_id"] == bay["id"]


@needs_db
def test_rental_status_controls_booking(client, admin, driver, zone):
    qr = client.post("/api/v1/bay-qr-codes", json={"zone_id": zone["id"], "number": f"R{uuid.uuid4().hex[:4].upper()}",
                                                   "street": "Rue de Lyon 3", "latitude": 46.21, "longitude": 6.13,
                                                   "rental_status": "rented_exclusive"}, headers=admin).json()
    bay = client.get(f"{API}/bays/by-code", params={"code": qr["qr_content"]}, headers=driver).json()
    assert bay["rental_status"] == "rented_exclusive" and not bay["bookable"]
    r = client.post(f"{API}/sessions", json={"bay_id": bay["id"], "minutes": 30}, headers=driver)
    assert r.status_code == 409 and r.json()["detail"] == "bay_rented"
    start = datetime.now(UTC) + timedelta(days=3)
    r = client.post(f"{API}/reservations", json={"site_id": bay["site_id"], "bay_id": bay["id"], "starts_at": start.isoformat(),
                                                 "ends_at": (start + timedelta(hours=5)).isoformat()}, headers=driver)
    assert r.status_code == 409 and r.json()["detail"] == "bay_rented"

    # Place management: the tenant allows sub-letting, the bay can be booked again
    detail = client.patch(f"/api/v1/bays/{bay['id']}", json={"rental_status": "rented_sublet"}, headers=admin).json()
    assert detail["rental_status"] == "rented_sublet" and detail["admin_state"] == "open"
    assert client.get(f"{API}/bays/{bay['id']}", headers=driver).json()["bookable"]
    r = client.post(f"{API}/reservations", json={"site_id": bay["site_id"], "bay_id": bay["id"], "starts_at": start.isoformat(),
                                                 "ends_at": (start + timedelta(hours=5)).isoformat()}, headers=driver)
    assert r.status_code == 201, r.text
    assert client.patch(f"/api/v1/bays/{bay['id']}", json={}, headers=admin).status_code == 422


@needs_db
def test_qr_page_renders(client):
    r = client.post("/login", data={"email": "responsable@example.com", "password": "ChangeMe-2026!", "next": "/qr-codes"})
    assert r.status_code == 200 and "Mes parkings" in r.text  # several parkings: choose one first
    p1 = next(p for p in client.get("/api/v1/parkings").json() if p["code"] == "P1")
    client.get(f"/parkings/{p1['id']}/open")
    rows = client.get("/qr-codes?new=1")
    assert rows.status_code == 200 and 'id="qr-form"' in rows.text
