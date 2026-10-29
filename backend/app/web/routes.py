"""Server-rendered pages of the back office (the 10 screens of the mockup).

Pages read data through app.services.queries; every action button calls the JSON API
(/api/v1/...) with fetch, exactly like the Android app does, so the business rules live
in one place.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import ACCESS_COOKIE, PERMISSIONS, allowed_site_ids
from app.i18n import LOCALE_COOKIE, Translator, negotiate_locale
from app.models import Controller, Customer, Device, NotificationRule, Site, StaffUser, Zone
from app.security import create_access_token, decode_access_token, verify_password
from app.services import queries, reports
from app.web import formatting as f

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
# Appended to the CSS/JS links (?v=...) so browsers fetch the new files after an update instead of a cached copy.
ASSET_VERSION = str(int(max(p.stat().st_mtime for p in (Path(__file__).parent / "static").rglob("*") if p.is_file())))
router = APIRouter(include_in_schema=False)

NAV = [
    ("dashboard", "/", "nav.dashboard"),
    ("plan", "/plan", "nav.plan"),
    ("reservations", "/reservations", "nav.reservations"),
    ("subscriptions", "/subscriptions", "nav.subscriptions"),
    ("customers", "/customers", "nav.customers"),
    ("pricing", "/pricing", "nav.pricing"),
    ("reports", "/reports", "nav.reports"),
    ("settings", "/settings", "nav.settings"),
    ("team", "/team", "nav.team"),
    ("devices", "/devices", "nav.devices"),
]


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: Session) -> StaffUser:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise LoginRequired
    try:
        payload = decode_access_token(token)
    except jwt.InvalidTokenError as exc:
        raise LoginRequired from exc
    user = db.get(StaffUser, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise LoginRequired
    return user


def render(request: Request, template: str, user: StaffUser | None, active: str = "", **ctx: object) -> HTMLResponse:
    locale = negotiate_locale(request, user.locale if user else None)
    t = Translator(locale)
    context = {
        "request": request,
        "t": t,
        "locale": locale,
        "locales": get_settings().supported_locales,
        "user": user,
        "nav": NAV,
        "active": active,
        "now": datetime.now(UTC),
        "can": (lambda perm: user is not None and user.role in PERMISSIONS[perm]),
        "money": lambda cents, currency="EUR", decimals=None: f.fmt_money(cents, locale, currency, decimals),
        "fdate": lambda v: f.fmt_date(v, locale),
        "fdt": lambda v: f.fmt_datetime(v, locale),
        "ftime": f.fmt_time,
        "fdur": f.fmt_duration,
        "bay_short": f.bay_short,
        "dtinput": f.fmt_input_datetime,
        "num": lambda v, digits=1: f.fmt_number(v, locale, digits),
        "today": f.fmt_today(locale, datetime.now(UTC)),
        "asset_v": ASSET_VERSION,
    }
    context.update(ctx)
    return templates.TemplateResponse(request, template, context)


def page(handler):
    """Decorator: redirect to /login when the cookie is missing or expired."""

    def wrapper(request: Request, db: Session = Depends(get_db)) -> Response:
        try:
            user = current_user(request, db)
        except LoginRequired:
            return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
        return handler(request, db, user)

    wrapper.__name__ = handler.__name__
    return wrapper


# --- Authentication and language --------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/") -> HTMLResponse:
    return render(request, "login.html", None, next=next, error=None, turbo=get_settings().turbo_login)


def _signed_in(user: StaffUser, next: str) -> Response:
    user.last_login_at = datetime.now(UTC)
    token, expires = create_access_token(user.id, user.role, user.locale)
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(ACCESS_COOKIE, token, max_age=expires, httponly=True, samesite="strict",
                        secure=get_settings().cookie_secure)
    return response


@router.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/"),
                 db: Session = Depends(get_db)) -> Response:
    user = db.scalar(select(StaffUser).where(func.lower(StaffUser.email) == email.strip().lower()))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return render(request, "login.html", None, next=next, error="login.error", turbo=get_settings().turbo_login)
    return _signed_in(user, next)


@router.post("/login/turbo")
def login_turbo(next: str = Form("/"), db: Session = Depends(get_db)) -> Response:
    """TEST ONLY (TURBO_LOGIN=true): sign in as the first active administrator, without a password."""
    if not get_settings().turbo_login:
        return Response(status_code=404)
    user = db.scalar(select(StaffUser).where(StaffUser.role == "admin", StaffUser.is_active)
                     .order_by(StaffUser.created_at).limit(1))
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return _signed_in(user, next)


@router.get("/logout")
def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(ACCESS_COOKIE)
    return response


@router.get("/lang/{code}")
def set_language(code: str, request: Request) -> Response:
    back = request.headers.get("referer") or "/"
    response = RedirectResponse(back, status_code=303)
    if code in get_settings().supported_locales:
        response.set_cookie(LOCALE_COOKIE, code, max_age=365 * 24 * 3600, samesite="lax")
    return response


# --- Helpers ------------------------------------------------------------------------------
def _outdoor_zone(db: Session, site_code: str = "P1") -> Zone | None:
    return db.scalar(select(Zone).join(Site, Site.id == Zone.site_id).where(Site.code == site_code, Zone.code == "EXT"))


# --- 1. Dashboard ---------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
@page
def dashboard(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    scope = allowed_site_ids(user)
    sites = queries.sites(db, scope)
    zone = _outdoor_zone(db)
    aerial = queries.bays(db, zone.id) if zone else []
    selected_site = request.query_params.get("site")
    site = next((s for s in sites if str(s["id"]) == selected_site), sites[0] if sites else None)
    return render(
        request, "dashboard.html", user, "dashboard",
        kpi=queries.dashboard(db, None, scope),
        sites=sites, site=site,
        aerial=aerial,
        aerial_counts={k: sum(1 for b in aerial if b["status"] == k) for k in ("occupied", "alarm", "free", "reserved", "blocked")},
        alarms=queries.payment_alarms(db, scope, None, limit=6),
        hourly=queries.tariffs(db)["current"].get("hourly"),
    )


# --- 2. Bay map -----------------------------------------------------------------------------
@router.get("/plan", response_class=HTMLResponse)
@page
def plan(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    scope = allowed_site_ids(user)
    sites = queries.sites(db, scope)
    site_id = request.query_params.get("site") or (str(sites[0]["id"]) if sites else None)
    site = next((s for s in sites if str(s["id"]) == site_id), sites[0] if sites else None)
    zones = queries.zones(db, site["id"]) if site else []
    zone_id = request.query_params.get("zone")
    zone = next((z for z in zones if str(z["id"]) == zone_id), zones[0] if zones else None)
    bays = queries.bays(db, zone["id"]) if zone else []
    flt = request.query_params.get("filter", "all")
    bay_param = request.query_params.get("bay")
    detail = queries.bay_detail(db, uuid.UUID(bay_param)) if bay_param else None
    if detail is None and bays:
        first_alarm = next((b for b in bays if b["status"] == "alarm"), bays[0])
        detail = queries.bay_detail(db, first_alarm["id"])
    rows: dict[int, list[dict]] = {}
    for b in bays:
        rows.setdefault(b["plan_row"] or 1, []).append(b)
    counts = {k: sum(1 for b in bays if b["status"] == k) for k in ("free", "occupied", "alarm", "reserved", "blocked")}
    counts["rate"] = round(100 * (counts["occupied"] + counts["alarm"]) / len(bays)) if bays else 0
    return render(
        request, "plan.html", user, "plan",
        sites=sites, site=site, zones=zones, zone=zone, rows=[rows[k] for k in sorted(rows)], counts=counts,
        flt=flt, detail=detail, show_photo=bool(zone and zone["code"] == "EXT" and site and site["code"] == "P1"),
    )


# --- 3. Reservations ----------------------------------------------------------------------
@router.get("/reservations", response_class=HTMLResponse)
@page
def reservations(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    scope = allowed_site_ids(user)
    tab = request.query_params.get("tab", "ongoing")
    q = request.query_params.get("q") or None
    manual = _manual_lookup(request)
    match = None
    if manual["name"] or manual["plate"]:
        mode = manual["mode"]
        rows_all = (queries.reservations(db, scope, "all", manual["name"] or manual["plate"]) if mode == "reservation"
                    else queries.subscriptions(db, scope, "all", manual["name"] or manual["plate"]))
        match = next((r for r in rows_all if _matches(r, manual)), None)
    extra = {}
    if request.query_params.get("new"):
        extra["customers"] = queries.customer_search(db, "", limit=200)
        extra["free_bays"] = queries.free_bays(db, scope)
    return render(
        request, "reservations.html", user, "reservations",
        tab=tab, q=q or "", rows=queries.reservations(db, scope, tab, q), stats=queries.reservation_stats(db, scope),
        manual=manual, match=match, new=bool(request.query_params.get("new")), **extra,
    )


def _manual_lookup(request: Request) -> dict:
    return {
        "mode": request.query_params.get("mode", "reservation"),
        "name": (request.query_params.get("mname") or "").strip(),
        "plate": (request.query_params.get("mplate") or "").strip(),
    }


def _norm(value: str | None) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def _matches(row: dict, manual: dict) -> bool:
    name_ok = not manual["name"] or manual["name"].lower() in row["customer_name"].lower()
    plate_ok = not manual["plate"] or _norm(manual["plate"]) in _norm(row.get("plate"))
    return name_ok and plate_ok


# --- 4. Subscriptions ---------------------------------------------------------------------
@router.get("/subscriptions", response_class=HTMLResponse)
@page
def subscriptions(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    scope = allowed_site_ids(user)
    tab = request.query_params.get("tab", "active")
    q = request.query_params.get("q") or None
    manual = _manual_lookup(request)
    manual["mode"] = "subscription"
    match = None
    if manual["name"] or manual["plate"]:
        rows_all = queries.subscriptions(db, scope, "all", manual["name"] or manual["plate"])
        match = next((r for r in rows_all if _matches(r, manual)), None)
    return render(
        request, "subscriptions.html", user, "subscriptions",
        tab=tab, q=q or "", rows=queries.subscriptions(db, scope, tab, q), stats=queries.subscription_stats(db, scope),
        manual=manual, match=match, plans=queries.plans(db),
    )


# --- 5. Customers ----------------------------------------------------------------------------
@router.get("/customers", response_class=HTMLResponse)
@page
def customers(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    q = (request.query_params.get("q") or "").strip()
    results = queries.customer_search(db, q) if len(q) >= 2 else []
    customer_id = request.query_params.get("id")
    if not customer_id and len(results) == 1:
        customer_id = str(results[0]["id"])
    if not customer_id and not q:
        first = queries.customer_search(db, "a", limit=1)
        customer_id = str(first[0]["id"]) if first else None
    profile = queries.customer_profile(db, uuid.UUID(customer_id)) if customer_id else None
    total = db.scalar(select(func.count(Customer.id)))
    return render(request, "customers.html", user, "customers", q=q, results=results, profile=profile, total=total)


# --- 6. Pricing --------------------------------------------------------------------------------
@router.get("/pricing", response_class=HTMLResponse)
@page
def pricing(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    data = queries.tariffs(db)
    return render(request, "pricing.html", user, "pricing", current=data["current"], history=data["history"][:12])


# --- 7. Reports -------------------------------------------------------------------------------
@router.get("/reports", response_class=HTMLResponse)
@page
def reports_page(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    sites = queries.sites(db, allowed_site_ids(user))
    period = request.query_params.get("period", "month")
    start, end = queries.period_bounds(period)
    return render(
        request, "reports.html", user, "reports",
        sites=sites, site_param=request.query_params.get("site", ""), period=period, start=start, end=end,
        report_types=list(reports.REPORTS), exports=queries.recent_exports(db),
        site_names={s["id"]: s["name"] for s in sites},
    )


# --- 8. Settings ------------------------------------------------------------------------------
@router.get("/settings", response_class=HTMLResponse)
@page
def settings_page(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    section = request.query_params.get("section", "general")
    rules = {(r.event, r.channel): r.enabled for r in db.scalars(select(NotificationRule))}
    return render(
        request, "settings.html", user, "settings",
        section=section, values=queries.settings(db), sites=queries.sites(db, None), rules=rules,
    )


# --- 9. Management team ----------------------------------------------------------------------
@router.get("/team", response_class=HTMLResponse)
@page
def team(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    members = queries.team(db)
    return render(
        request, "team.html", user, "team",
        members=members, permissions=PERMISSIONS, sites=queries.sites(db, None),
        stats={
            "active": sum(1 for m in members if m["is_active"] and not m["pending_invitation"]),
            "admins": sum(1 for m in members if m["role"] == "admin" and m["is_active"]),
            "agents": sum(1 for m in members if m["role"] == "agent" and m["is_active"]),
            "pending": sum(1 for m in members if m["pending_invitation"]),
        },
    )


# --- 10. Control devices ---------------------------------------------------------------------
@router.get("/devices", response_class=HTMLResponse)
@page
def devices(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    items = queries.controllers(db, allowed_site_ids(user))
    flt = request.query_params.get("filter", "all")
    for c in items:
        events = c["events"]
        if flt == "alarm":
            events = [e for e in events if e["priority"] == "alarm"]
        elif flt == "warning":
            events = [e for e in events if e["priority"] == "warning"]
        elif flt == "unack":
            events = [e for e in events if e["state"] == "active_unacknowledged"]
        c["shown"] = events
    return render(
        request, "devices.html", user, "devices", controllers=items, flt=flt,
        totals={"alarms": sum(c["active_alarms"] for c in items), "warnings": sum(c["active_warnings"] for c in items)},
    )


# --- Live view of a presence sensor (camera plate reader) ------------------------------------
def _live_device(request: Request, db: Session, device_id: uuid.UUID) -> Device | None:
    """The sensor if the signed-in staff member may see it and it has a live view, else None."""
    try:
        user = current_user(request, db)
    except LoginRequired:
        return None
    device = db.get(Device, device_id)
    if device is None or not device.live_url:
        return None
    allowed = allowed_site_ids(user)
    if allowed is not None and db.get(Controller, device.controller_id).site_id not in allowed:
        return None
    return device


def _sensor_request(device: Device, path: str) -> urllib.request.Request:
    req = urllib.request.Request(device.live_url.rstrip("/") + path)
    if get_settings().sensor_token:
        req.add_header("X-Sensor-Token", get_settings().sensor_token)
    return req


@router.get("/devices/{device_id}/live.json")
def sensor_live_values(device_id: uuid.UUID, request: Request, db: Session = Depends(get_db)) -> Response:
    """Current values of the sensor, relayed from its own service (the sensor is never exposed directly)."""
    device = _live_device(request, db, device_id)
    if device is None:
        return JSONResponse({"detail": "not_found"}, status_code=404)
    try:
        with urllib.request.urlopen(_sensor_request(device, "/status.json"), timeout=3) as res:
            data = json.loads(res.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return JSONResponse({"online": False, "error": str(exc)[:200]})
    return JSONResponse({"online": True, **data}, headers={"Cache-Control": "no-store"})


@router.get("/devices/{device_id}/stream.mjpg")
def sensor_live_stream(device_id: uuid.UUID, request: Request, db: Session = Depends(get_db)) -> Response:
    """MJPEG live view of the sensor's camera, relayed chunk by chunk."""
    device = _live_device(request, db, device_id)
    if device is None:
        return Response(status_code=404)
    try:
        upstream = urllib.request.urlopen(_sensor_request(device, "/stream.mjpg"), timeout=5)
    except (urllib.error.URLError, TimeoutError):
        return Response(status_code=502)

    def relay() -> Iterator[bytes]:
        try:
            while chunk := upstream.read1(65536):
                yield chunk
        finally:
            upstream.close()

    return StreamingResponse(relay(), media_type=upstream.headers.get("Content-Type", "multipart/x-mixed-replace; boundary=FRAME"),
                             headers={"Cache-Control": "no-store"})
