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
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.v1.bay_qr import qr_rows
from app.config import get_settings
from app.db import get_db
from app.deps import ACCESS_COOKIE, PERMISSIONS, allowed_site_ids, user_can_sign_in
from app.i18n import LOCALE_COOKIE, Translator, negotiate_locale
from app.models import Controller, Device, NotificationRule, Organization, Site, StaffUser, Zone
from app.security import create_access_token, decode_access_token, verify_password
from app.services import account as account_service
from app.services import queries, reports, watchdog
from app.web import formatting as f

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
# Appended to the CSS/JS links (?v=...) so browsers fetch the new files after an update instead of a cached copy.
ASSET_VERSION = str(int(max(p.stat().st_mtime for p in (Path(__file__).parent / "static").rglob("*") if p.is_file())))
router = APIRouter(include_in_schema=False)

SITE_COOKIE = "pk_site"  # the parking the person opened from "Mon compte"
ALL_MODES = ("qr_code", "presence_sensor", "smart_barrier")
EQUIPMENT_MODES = ("presence_sensor", "smart_barrier")

# Menu of an opened parking: (key, href, label, management modes where the entry exists)
NAV = [
    ("dashboard", "/", "nav.dashboard", ALL_MODES),
    ("plan", "/plan", "nav.plan", ALL_MODES),
    ("qr", "/qr-codes", "nav.places_qr", ("qr_code",)),
    ("qr", "/qr-codes", "nav.qr", ("presence_sensor",)),
    ("equipment", "/equipment", "nav.sensors", ("presence_sensor",)),
    ("equipment", "/equipment", "nav.barriers", ("smart_barrier",)),
    ("reservations", "/reservations", "nav.reservations", ALL_MODES),
    ("subscriptions", "/subscriptions", "nav.subscriptions", ALL_MODES),
    ("customers", "/customers", "nav.customers", ALL_MODES),
    ("pricing", "/pricing", "nav.pricing", ALL_MODES),
    ("reports", "/reports", "nav.reports", ALL_MODES),
    ("settings", "/settings", "nav.settings", ALL_MODES),
    ("devices", "/devices", "nav.devices", EQUIPMENT_MODES),
]
# Menu of "Mon compte" (no parking needed)
ACCOUNT_NAV = [
    ("parkings", "/parkings", "nav.my_parkings"),
    ("users", "/account/users", "nav.users"),
    ("bank", "/account/bank", "nav.bank"),
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
    if user is None or not user_can_sign_in(user):
        raise LoginRequired
    return user


def current_parking(request: Request, db: Session, user: StaffUser) -> dict | None:
    """The parking opened from "Mon compte" (cookie pk_site), if this person may still see it.
    A person whose account has a single parking does not need to choose it."""
    allowed = allowed_site_ids(user)
    raw = request.cookies.get(SITE_COOKIE)
    try:
        site_id = uuid.UUID(raw) if raw else None
    except ValueError:
        site_id = None
    if site_id is not None and (allowed is None or site_id in allowed):
        rows = queries.sites(db, {site_id})
        if rows:
            return rows[0]
    if allowed is not None and len(allowed) == 1:
        rows = queries.sites(db, allowed)
        return rows[0] if rows else None
    return None


def render(request: Request, template: str, user: StaffUser | None, active: str = "", **ctx: object) -> HTMLResponse:
    locale = negotiate_locale(request, user.locale if user else None)
    t = Translator(locale)
    parking = getattr(request.state, "parking", None)
    db = getattr(request.state, "db", None)
    my_parkings = queries.sites(db, allowed_site_ids(user)) if user is not None and db is not None else []
    default_currency = (queries.settings(db, parking["id"]).get("general.currency") if parking and db is not None else None) or "CHF"
    context = {
        "request": request,
        "t": t,
        "locale": locale,
        "locales": get_settings().supported_locales,
        "user": user,
        "nav": [(k, h, label) for k, h, label, modes in NAV if parking and parking["management_mode"] in modes],
        "account_nav": ACCOUNT_NAV + ([("admin", "/admin", "nav.admin")] if user is not None and user.is_superuser else []),
        "parking": parking,
        "my_parkings": my_parkings,
        "organization": user.organization if user is not None else None,
        "active": active,
        "now": datetime.now(UTC),
        "can": (lambda perm: user is not None and (user.is_superuser or user.role in PERMISSIONS[perm])),
        "money": lambda cents, currency=None, decimals=None: f.fmt_money(cents, locale, currency or default_currency, decimals),
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
    """Decorator of the pages of a parking: redirect to /login when the cookie is missing or expired, and to
    "Mes parkings" when no parking is opened. The handler receives the opened parking (a row of queries.sites)."""

    def wrapper(request: Request, db: Session = Depends(get_db)) -> Response:
        try:
            user = current_user(request, db)
        except LoginRequired:
            return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
        request.state.db = db
        parking = current_parking(request, db, user)
        if parking is None:
            return RedirectResponse("/parkings", status_code=303)
        request.state.parking = parking
        return handler(request, db, user, parking)

    wrapper.__name__ = handler.__name__
    return wrapper


def account_page(handler):
    """Decorator of the "Mon compte" pages: signed in, no parking needed."""

    def wrapper(request: Request, db: Session = Depends(get_db)) -> Response:
        try:
            user = current_user(request, db)
        except LoginRequired:
            return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
        request.state.db = db
        request.state.parking = current_parking(request, db, user)
        return handler(request, db, user)

    wrapper.__name__ = handler.__name__
    return wrapper


# --- Authentication and language --------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/") -> HTMLResponse:
    return render(request, "login.html", None, next=next, error=None, turbo=get_settings().turbo_login)


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request) -> HTMLResponse:
    """Public sign-up: commune, State, property manager or owner. The creator is the account's administrator."""
    return render(request, "signup.html", None)


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
    if not user_can_sign_in(user):
        return render(request, "login.html", None, next=next, error="login.suspended", turbo=get_settings().turbo_login)
    return _signed_in(user, next)


@router.post("/login/turbo")
def login_turbo(next: str = Form("/"), db: Session = Depends(get_db)) -> Response:
    """TEST ONLY (TURBO_LOGIN=true): sign in as the super user (DR Automation), without a password."""
    if not get_settings().turbo_login:
        return Response(status_code=404)
    user = db.scalar(select(StaffUser).where(StaffUser.is_superuser, StaffUser.is_active)
                     .order_by(StaffUser.created_at).limit(1))
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return _signed_in(user, next)


@router.get("/logout")
def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(ACCESS_COOKIE)
    response.delete_cookie(SITE_COOKIE)
    return response


@router.get("/lang/{code}")
def set_language(code: str, request: Request) -> Response:
    back = request.headers.get("referer") or "/"
    response = RedirectResponse(back, status_code=303)
    if code in get_settings().supported_locales:
        response.set_cookie(LOCALE_COOKIE, code, max_age=365 * 24 * 3600, samesite="lax")
    return response


# --- Helpers ------------------------------------------------------------------------------
def _main_zone(db: Session, site_id: uuid.UUID) -> Zone | None:
    """The zone drawn on the dashboard: the outdoor zone of the prototype, else the parking's first zone."""
    return (db.scalar(select(Zone).where(Zone.site_id == site_id, Zone.code == "EXT"))
            or db.scalar(select(Zone).where(Zone.site_id == site_id).order_by(Zone.sort_order, Zone.code).limit(1)))


def _uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


# --- Mon compte: parkings, opening a parking, users, bank details, super user console ----------
@router.get("/parkings", response_class=HTMLResponse)
@account_page
def parkings_page(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    """Mes parkings: every parking of the account (of the platform for the super user)."""
    rows = queries.sites(db, allowed_site_ids(user))
    return render(request, "parkings.html", user, "parkings", parkings=rows,
                  can_create=user.is_superuser or user.role in ("admin", "manager"))


@router.get("/parkings/new", response_class=HTMLResponse)
@account_page
def parking_new(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    orgs = db.scalars(select(Organization).order_by(Organization.name)).all() if user.is_superuser else []
    return render(request, "parking_new.html", user, "parkings", organizations=orgs,
                  mode=request.query_params.get("mode", "qr_code"))


@router.get("/parkings/{site_id}/open")
def parking_open(site_id: uuid.UUID, request: Request, welcome: int = 0, db: Session = Depends(get_db)) -> Response:
    """Open a parking: every page of the back office then works on this parking."""
    try:
        user = current_user(request, db)
    except LoginRequired:
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
    allowed = allowed_site_ids(user)
    site = db.get(Site, site_id)
    if site is None or (allowed is not None and site_id not in allowed):
        return RedirectResponse("/parkings", status_code=303)
    target = "/"
    if welcome:
        target = "/qr-codes?welcome=1" if site.management_mode == "qr_code" else "/equipment?welcome=1"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(SITE_COOKIE, str(site.id), max_age=365 * 24 * 3600, httponly=True, samesite="lax",
                        secure=get_settings().cookie_secure)
    return response


@router.get("/account", response_class=HTMLResponse)
@account_page
def account_home(request: Request, db: Session, user: StaffUser) -> Response:
    return RedirectResponse("/parkings", status_code=303)


@router.get("/account/users", response_class=HTMLResponse)
@account_page
def account_users(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    """Utilisateurs of the account (formerly "Équipe de gestion")."""
    members = queries.team(db, user.organization_id)
    return render(
        request, "team.html", user, "users",
        members=members, permissions=PERMISSIONS, sites=queries.sites(db, allowed_site_ids(user)),
        stats={
            "active": sum(1 for m in members if m["is_active"] and not m["pending_invitation"]),
            "admins": sum(1 for m in members if m["role"] == "admin" and m["is_active"]),
            "agents": sum(1 for m in members if m["role"] == "agent" and m["is_active"]),
            "pending": sum(1 for m in members if m["pending_invitation"]),
        },
    )


@router.get("/team")
def team_moved() -> Response:
    return RedirectResponse("/account/users", status_code=301)


@router.get("/account/bank", response_class=HTMLResponse)
@account_page
def account_bank(request: Request, db: Session, user: StaffUser) -> HTMLResponse:
    """Account details, bank details (IBAN, BIC) and the monthly statements of the transfers."""
    org = user.organization
    payouts = account_service.payouts(db, org.id) if org is not None else []
    months: dict = {}
    for p in payouts:
        months.setdefault(p["month"], []).append(p)
    return render(request, "account_bank.html", user, "bank", org=account_service.organization_out(org) if org else None,
                  months=sorted(months.items(), reverse=True), is_admin=user.is_superuser or user.role == "admin")


@router.get("/admin", response_class=HTMLResponse)
@account_page
def admin_console(request: Request, db: Session, user: StaffUser) -> Response:
    """DR Automation console (super user): every account and every parking of the platform."""
    if not user.is_superuser:
        return RedirectResponse("/parkings", status_code=303)
    from app.api.v1.admin import organizations

    return render(request, "admin.html", user, "admin", organizations=organizations(db), parkings=queries.sites(db, None))


# --- 1. Dashboard ---------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
@page
def dashboard(request: Request, db: Session, user: StaffUser, parking: dict) -> HTMLResponse:
    scope = {parking["id"]}
    zone = _main_zone(db, parking["id"])
    aerial = queries.bays(db, zone.id) if zone else []
    return render(
        request, "dashboard.html", user, "dashboard",
        kpi=queries.dashboard(db, parking["id"], scope),
        sites=queries.sites(db, allowed_site_ids(user)), site=parking,
        aerial=aerial,
        aerial_counts={k: sum(1 for b in aerial if b["status"] == k) for k in ("occupied", "alarm", "free", "reserved", "blocked")},
        alarms=queries.payment_alarms(db, scope, None, limit=6),
        hourly=queries.effective_tariffs(db, parking["id"])["current"].get("hourly"),
    )


# --- 2. Bay map -----------------------------------------------------------------------------
@router.get("/plan", response_class=HTMLResponse)
@page
def plan(request: Request, db: Session, user: StaffUser, parking: dict) -> HTMLResponse:
    site = parking
    zones = queries.zones(db, site["id"])
    zone_id = request.query_params.get("zone")
    zone = next((z for z in zones if str(z["id"]) == zone_id), zones[0] if zones else None)
    bays = queries.bays(db, zone["id"]) if zone else []
    flt = request.query_params.get("filter", "all")
    bay_id = _uuid(request.query_params.get("bay"))
    detail = queries.bay_detail(db, bay_id) if bay_id and any(b["id"] == bay_id for b in bays) else None
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
        sites=[site], site=site, zones=zones, zone=zone, rows=[rows[k] for k in sorted(rows)], counts=counts,
        flt=flt, detail=detail, show_photo=bool(zone and zone["code"] == "EXT" and site["code"] == "P1"),
    )


# --- 2b. Places and QR codes ----------------------------------------------------------------------
@router.get("/qr-codes", response_class=HTMLResponse)
@page
def qr_codes(request: Request, db: Session, user: StaffUser, parking: dict) -> HTMLResponse:
    if parking["management_mode"] == "smart_barrier":
        return RedirectResponse("/equipment", status_code=303)
    scope = {parking["id"]}
    zones = [(parking, queries.zones(db, parking["id"]))]
    rows = qr_rows(db, scope)
    selected = None
    bay_id = _uuid(request.query_params.get("bay"))
    if bay_id:
        found = qr_rows(db, scope, bay_id)
        selected = found[0] if found and found[0]["reference"] else None
    edit = None
    edit_id = _uuid(request.query_params.get("edit"))
    if edit_id:
        found = qr_rows(db, scope, edit_id)
        edit = queries.bay_detail(db, edit_id) if found else None
    show_form = bool(request.query_params.get("new") or edit)
    return render(request, "qr_codes.html", user, "qr", zones=zones, rows=rows, selected=selected, edit=edit,
                  show_form=show_form, welcome=bool(request.query_params.get("welcome")))


# --- 2c. Presence sensors or smart barriers (with their QR codes) ------------------------------
SQL_EQUIPMENT = """
SELECT d.id, d.code, d.kind::text AS kind, d.label, d.address, d.live_url, d.status::text AS status, d.reference,
       d.qr_generated_at, d.created_at, c.id AS controller_id, c.code AS controller_code, c.name AS controller_name,
       b.id AS bay_id, b.number AS bay_number, b.reference AS bay_reference,
       coalesce(v.status, 'free') AS bay_status
FROM devices d JOIN controllers c ON c.id = d.controller_id
LEFT JOIN bays b ON b.id = d.bay_id LEFT JOIN v_bay_status v ON v.bay_id = b.id
WHERE c.site_id = :s AND d.kind IN ({kinds})
ORDER BY d.code
"""


@router.get("/equipment", response_class=HTMLResponse)
@page
def equipment(request: Request, db: Session, user: StaffUser, parking: dict) -> HTMLResponse:
    mode = parking["management_mode"]
    if mode == "qr_code":
        return RedirectResponse("/qr-codes", status_code=303)
    kinds = "'barrier'" if mode == "smart_barrier" else "'front_presence_sensor', 'vertical_presence_sensor'"
    items = [dict(r._mapping) for r in db.execute(text(SQL_EQUIPMENT.format(kinds=kinds)), {"s": parking["id"]})]
    for d in items:
        if d["kind"] == "barrier":
            d["name"], _, d["direction"] = (d["label"] or d["code"]).partition(" · ")
            d["direction"] = d["direction"] or "entry"
    cabinets = db.scalars(select(Controller).where(Controller.site_id == parking["id"]).order_by(Controller.code)).all()
    return render(request, "equipment.html", user, "equipment", items=items, cabinets=cabinets, mode=mode,
                  welcome=bool(request.query_params.get("welcome")),
                  can_edit=user.is_superuser or user.role in ("admin", "manager"))


# --- 3. Reservations ----------------------------------------------------------------------
@router.get("/reservations", response_class=HTMLResponse)
@page
def reservations(request: Request, db: Session, user: StaffUser, parking: dict) -> HTMLResponse:
    scope = {parking["id"]}
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
        extra["customers"] = queries.customer_search(db, "", limit=200, site_ids=scope, org_id=parking["organization_id"])
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
def subscriptions(request: Request, db: Session, user: StaffUser, parking: dict) -> HTMLResponse:
    scope = {parking["id"]}
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


# --- 5. Customers (drivers) ---------------------------------------------------------------------
@router.get("/customers", response_class=HTMLResponse)
@page
def customers(request: Request, db: Session, user: StaffUser, parking: dict) -> HTMLResponse:
    scope, org_id = {parking["id"]}, parking["organization_id"]
    q = (request.query_params.get("q") or "").strip()
    results = queries.customer_search(db, q, site_ids=scope, org_id=org_id) if len(q) >= 2 else []
    customer_id = _uuid(request.query_params.get("id"))
    if not customer_id and len(results) == 1:
        customer_id = results[0]["id"]
    if not customer_id and not q:
        first = queries.customer_search(db, "", limit=1, site_ids=scope, org_id=org_id)
        customer_id = first[0]["id"] if first else None
    profile = None
    if customer_id and queries.customer_visible(db, customer_id, scope, org_id):
        profile = queries.customer_profile(db, customer_id, scope)
    total = queries.customer_count(db, scope, org_id)
    return render(request, "customers.html", user, "customers", q=q, results=results, profile=profile, total=total)


# --- 6. Pricing --------------------------------------------------------------------------------
@router.get("/pricing", response_class=HTMLResponse)
@page
def pricing(request: Request, db: Session, user: StaffUser, parking: dict) -> HTMLResponse:
    data = queries.effective_tariffs(db, parking["id"])
    currency = queries.settings(db, parking["id"]).get("general.currency") or "CHF"
    return render(request, "pricing.html", user, "pricing", current=data["current"], history=data["history"][:12],
                  currency=currency)


# --- 7. Reports -------------------------------------------------------------------------------
@router.get("/reports", response_class=HTMLResponse)
@page
def reports_page(request: Request, db: Session, user: StaffUser, parking: dict) -> HTMLResponse:
    sites = [parking]
    period = request.query_params.get("period", "month")
    start, end = queries.period_bounds(period)
    return render(
        request, "reports.html", user, "reports",
        sites=sites, site_param=str(parking["id"]), period=period, start=start, end=end,
        report_types=list(reports.REPORTS), exports=queries.recent_exports(db, site_ids={parking["id"]}),
        site_names={s["id"]: s["name"] for s in sites},
    )


# --- 8. Settings of the parking ---------------------------------------------------------------
@router.get("/settings", response_class=HTMLResponse)
@page
def settings_page(request: Request, db: Session, user: StaffUser, parking: dict) -> HTMLResponse:
    sections = ["parking", "general", "control", "payments"] + (["notifications", "security"] if user.is_superuser else [])
    section = request.query_params.get("section", "parking")
    if section not in sections:
        section = "parking"
    rules = {(r.event, r.channel): r.enabled for r in db.scalars(select(NotificationRule))}
    return render(
        request, "settings.html", user, "settings", sections=sections,
        section=section, values=queries.settings(db, None if section == "security" else parking["id"]), rules=rules,
    )


# --- 10. Control devices (presence sensor and smart barrier modes) ------------------------------
@router.get("/devices", response_class=HTMLResponse)
@page
def devices(request: Request, db: Session, user: StaffUser, parking: dict) -> HTMLResponse:
    if parking["management_mode"] == "qr_code":
        return RedirectResponse("/", status_code=303)
    items = queries.controllers(db, {parking["id"]})
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
        c["watchdog"] = watchdog.status(c["id"])
    return render(
        request, "devices.html", user, "devices", controllers=items, flt=flt,
        totals={"alarms": sum(c["active_alarms"] for c in items), "warnings": sum(c["active_warnings"] for c in items)},
    )


@router.get("/live/state.json")
def live_state(request: Request, scope: str = "bays", db: Session = Depends(get_db)) -> Response:
    """Polled by every page every 2 s (0.5 Hz): the data fingerprint (the page reloads when it changes)
    and the communication watchdog of each cabinet."""
    try:
        user = current_user(request, db)
    except LoginRequired:
        return JSONResponse({"detail": "login_required"}, status_code=401)
    allowed = allowed_site_ids(user)
    cabinets = db.scalars(select(Controller).order_by(Controller.code)).all()
    return JSONResponse(jsonable_encoder({
        "version": queries.live_version(db, scope),
        "period_s": watchdog.PERIOD_S,
        "cabinets": {str(c.id): watchdog.status(c.id) for c in cabinets if allowed is None or c.site_id in allowed},
    }), headers={"Cache-Control": "no-store"})


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
