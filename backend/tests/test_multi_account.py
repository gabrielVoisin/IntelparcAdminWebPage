"""Multi-account platform: sign-up, parkings and their management modes, bank details, super user console.

Runs against the same PostgreSQL test database as test_api.py (seed_demo loaded, TURBO_LOGIN not needed).
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="needs a PostgreSQL test database")

H = {"X-Requested-With": "fetch"}
PASSWORD = "Correct-Horse-2026"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def owner(client):
    """A commune that signs up on the public page."""
    email = f"commune-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/account/signup", json={"organization_name": "Commune de Test", "kind": "municipality",
                                                    "full_name": "Marie Test", "email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["role"] == "admin" and body["user"]["is_superuser"] is False
    client.cookies.clear()
    return {"headers": _bearer(body["access_token"]), "email": email, "org_id": body["user"]["organization_id"]}


@pytest.fixture(scope="module")
def superuser(client):
    r = client.post("/api/v1/auth/login", json={"email": "responsable@example.com", "password": "ChangeMe-2026!"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["is_superuser"] is True  # the prototype's administrator became the DR Automation super user
    client.cookies.clear()
    return _bearer(r.json()["access_token"])


def test_signup_rejects_duplicates_and_weak_passwords(client, owner):
    r = client.post("/api/v1/account/signup", json={"organization_name": "Doublon", "full_name": "X Y", "email": owner["email"],
                                                    "password": PASSWORD})
    assert r.status_code == 409 and r.json()["detail"] == "email_already_used"
    r = client.post("/api/v1/account/signup", json={"organization_name": "Court", "full_name": "X Y",
                                                    "email": "court@example.com", "password": "short"})
    assert r.status_code == 422


def test_new_account_sees_only_its_parkings(client, owner):
    assert client.get("/api/v1/parkings", headers=owner["headers"]).json() == []
    assert client.get("/api/v1/sites", headers=owner["headers"]).json() == []
    assert client.get("/api/v1/admin/organizations", headers=owner["headers"]).status_code == 403


def test_qr_code_parking_generates_places(client, owner):
    r = client.post("/api/v1/parkings", headers=owner["headers"], json={
        "name": "Parking de la Gare", "capacity": 10, "management_mode": "qr_code", "address_line": "Place de la Gare 1",
        "postcode": "1260", "city": "Nyon", "latitude": 46.383, "longitude": 6.239})
    assert r.status_code == 201, r.text
    parking = r.json()
    assert parking["management_mode"] == "qr_code" and parking["places"] == 0 and parking["redirect"].endswith("welcome=1")
    r = client.post(f"/api/v1/parkings/{parking['id']}/places", headers=owner["headers"], json={"count": 3})
    assert r.status_code == 201, r.text
    assert [p["number"] for p in r.json()["created"]] == ["01", "02", "03"]
    again = client.post(f"/api/v1/parkings/{parking['id']}/places", headers=owner["headers"], json={"count": 2}).json()
    assert again["first"] == "04" and again["last"] == "05"
    listed = next(p for p in client.get("/api/v1/parkings", headers=owner["headers"]).json() if p["id"] == parking["id"])
    assert listed["places"] == 5 and listed["free_bays"] == 5
    # each place has its QR code (street and GPS of the parking)
    qr = client.get("/api/v1/bay-qr-codes", headers=owner["headers"]).json()
    assert len(qr) == 5 and all(row["reference"] and "Place de la Gare 1" in row["street"] for row in qr)
    assert client.get(f"/api/v1/bays/{r.json()['created'][0]['id']}/qr.pdf", headers=owner["headers"]).status_code == 200
    # no control device in QR code mode
    r = client.post(f"/api/v1/parkings/{parking['id']}/controllers", headers=owner["headers"], json={})
    assert r.status_code == 409
    # a place can be removed while unused
    assert client.delete(f"/api/v1/bays/{qr[0]['bay_id']}", headers=owner["headers"]).status_code == 204


def test_presence_sensor_creates_its_place(client, owner):
    parking = client.post("/api/v1/parkings", headers=owner["headers"], json={
        "name": "Parking du Lac", "capacity": 4, "management_mode": "presence_sensor", "city": "Nyon"}).json()
    r = client.post(f"/api/v1/parkings/{parking['id']}/sensors", headers=owner["headers"], json={"kind": "front"})
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["api_key"] and first["bay_number"] == "01" and first["code"] == "CP-001" and first["reference"]
    second = client.post(f"/api/v1/parkings/{parking['id']}/sensors", headers=owner["headers"], json={"kind": "vertical"}).json()
    assert second["api_key"] is None and second["bay_number"] == "02" and second["controller_id"] == first["controller_id"]
    listed = next(p for p in client.get("/api/v1/parkings", headers=owner["headers"]).json() if p["id"] == parking["id"])
    assert listed["places"] == 2 and listed["devices"] == 2
    # removing the sensor removes its unused place
    assert client.delete(f"/api/v1/devices/{second['id']}", headers=owner["headers"]).status_code == 204
    listed = next(p for p in client.get("/api/v1/parkings", headers=owner["headers"]).json() if p["id"] == parking["id"])
    assert listed["places"] == 1


def test_smart_barrier_parking_and_barrier_qr(client, owner):
    parking = client.post("/api/v1/parkings", headers=owner["headers"], json={
        "name": "Parking Centre", "capacity": 12, "management_mode": "smart_barrier", "address_line": "Rue Centrale 3",
        "city": "Nyon", "latitude": 46.38, "longitude": 6.24}).json()
    assert parking["places"] == 12
    r = client.post(f"/api/v1/parkings/{parking['id']}/barriers", headers=owner["headers"], json={"name": "Entrée", "direction": "entry"})
    assert r.status_code == 201, r.text
    barrier = r.json()
    assert barrier["api_key"] and barrier["reference"]
    svg = client.get(f"/api/v1/devices/{barrier['id']}/qr.svg", headers=owner["headers"])
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg")
    assert client.get(f"/api/v1/devices/{barrier['id']}/qr.pdf", headers=owner["headers"]).status_code == 200
    # the driver app resolves the barrier's code to a free place of the parking
    code = f"{parking['code']}/A/{barrier['code']}"
    reg = client.post("/api/v1/app/auth/register", json={"first_name": "Bea", "last_name": "Barrier", "password": "Secret-2026!",
                                                         "email": f"driver-{uuid.uuid4().hex[:8]}@example.com"})
    assert reg.status_code == 201, reg.text
    r = client.get("/api/v1/app/bays/by-code", params={"code": f"intelpark:bay:{code}?ref={barrier['reference']}"},
                   headers=_bearer(reg.json()["access_token"]))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "free" and r.json()["site_id"] == parking["id"]
    typed = client.get("/api/v1/app/bays/by-code", params={"code": code}, headers=_bearer(reg.json()["access_token"]))
    assert typed.status_code == 200 and typed.json()["site_id"] == parking["id"]


def test_parking_prices_and_settings_are_per_parking(client, owner):
    parking = client.get("/api/v1/parkings", headers=owner["headers"]).json()[0]
    r = client.put("/api/v1/tariffs/hourly", headers=owner["headers"], json={"amount_cents": 250, "site_id": parking["id"]})
    assert r.status_code == 200, r.text
    current = client.get("/api/v1/tariffs", params={"site_id": parking["id"]}, headers=owner["headers"]).json()["current"]
    assert current["hourly"]["amount_cents"] == 250
    # the platform's default prices and settings belong to the super user
    assert client.put("/api/v1/tariffs/hourly", headers=owner["headers"], json={"amount_cents": 1}).status_code == 403
    assert client.put("/api/v1/settings", headers=owner["headers"], json={"values": {"control.grace_alarm_min": 9}}).status_code == 403
    r = client.put("/api/v1/settings", params={"site_id": parking["id"]}, headers=owner["headers"],
                   json={"values": {"control.grace_alarm_min": 9}})
    assert r.status_code == 200 and r.json()["values"]["control.grace_alarm_min"] == 9


def test_bank_details(client, owner):
    r = client.put("/api/v1/account/bank", headers=owner["headers"], json={"holder": "Commune de Test", "iban": "CH93 0076 2011 6238 5295 8",
                                                                          "bic": "POFICHBEXXX"})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_iban"
    r = client.put("/api/v1/account/bank", headers=owner["headers"], json={"holder": "Commune de Test", "iban": "CH93 0076 2011 6238 5295 7",
                                                                          "bic": "POFICHBEXXX"})
    assert r.status_code == 200, r.text
    assert r.json()["bank_iban_masked"] == "CH93 •••• •••• 2957"
    assert client.get("/api/v1/account/payouts", headers=owner["headers"]).status_code == 200


def test_other_accounts_parkings_are_hidden(client, owner, superuser):
    prototype = next(p for p in client.get("/api/v1/parkings", headers=superuser).json() if p["code"] == "P1")
    assert client.get(f"/api/v1/sites/{prototype['id']}/zones", headers=owner["headers"]).status_code in (403, 404)
    assert client.post(f"/api/v1/parkings/{prototype['id']}/places", headers=owner["headers"], json={"count": 1}).status_code in (403, 404)
    # only the super user deletes parkings
    mine = client.get("/api/v1/parkings", headers=owner["headers"]).json()[0]
    assert client.request("DELETE", f"/api/v1/parkings/{mine['id']}", headers=owner["headers"],
                          json={"confirm_name": mine["name"]}).status_code == 403


def test_superuser_suspends_and_deletes_an_account(client, owner, superuser):
    orgs = client.get("/api/v1/admin/organizations", headers=superuser).json()
    org = next(o for o in orgs if o["id"] == owner["org_id"])
    assert org["parkings"] == 3 and org["bank_ok"]
    assert client.patch(f"/api/v1/admin/organizations/{org['id']}", headers=superuser, json={"is_active": False}).json()["is_active"] is False
    r = client.post("/api/v1/auth/login", json={"email": owner["email"], "password": PASSWORD})
    assert r.status_code == 403 and r.json()["detail"] == "organization_suspended"
    internal = next(o for o in orgs if o["kind"] == "internal")
    assert client.request("DELETE", f"/api/v1/admin/organizations/{internal['id']}", headers=superuser,
                          json={"confirm_name": internal["name"]}).status_code == 409
    assert client.request("DELETE", f"/api/v1/admin/organizations/{org['id']}", headers=superuser,
                          json={"confirm_name": "wrong"}).status_code == 422
    assert client.request("DELETE", f"/api/v1/admin/organizations/{org['id']}", headers=superuser,
                          json={"confirm_name": org["name"]}).status_code == 204
    assert all(o["id"] != org["id"] for o in client.get("/api/v1/admin/organizations", headers=superuser).json())


def test_web_pages_of_an_opened_parking(client, superuser):
    client.cookies.clear()
    r = client.post("/login", data={"email": "responsable@example.com", "password": "ChangeMe-2026!"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/", follow_redirects=False).headers["location"] == "/parkings"
    page = client.get("/parkings")
    assert page.status_code == 200 and "Mon compte" in page.text
    p1 = next(p for p in client.get("/api/v1/parkings").json() if p["code"] == "P1")
    assert client.get(f"/parkings/{p1['id']}/open", follow_redirects=False).status_code == 303
    for path in ("/", "/plan", "/qr-codes", "/equipment", "/reservations", "/subscriptions", "/customers", "/pricing",
                 "/reports", "/settings", "/devices", "/account/users", "/account/bank", "/admin"):
        assert client.get(path).status_code == 200, path
    client.cookies.clear()
