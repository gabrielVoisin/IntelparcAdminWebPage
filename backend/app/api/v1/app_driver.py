"""Driver app (Intel Park, Android): account, vehicles, find / pay / book a bay, subscriptions,
Bluetooth identity token. Authentication: "Authorization: Bearer <customer token>".

Staff tokens are not accepted here and customer tokens are not accepted by the staff API.
Payments: with the setting payments.provider = "demo" every payment succeeds immediately
(prototype). Plug a real provider (Stripe, TWINT, Datatrans...) in _charge() before going live.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.subscriptions import add_months
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_customer
from app.models import (
    Customer,
    CustomerPhoneCode,
    CustomerRefreshToken,
    ParkingSession,
    Payment,
    Reservation,
    Subscription,
    SubscriptionPlan,
    Vehicle,
)
from app.schemas import Money, RefreshIn, VehicleOut
from app.schemas_app import (
    AppBayOut,
    AppLoginIn,
    AppPlanOut,
    AppReservationIn,
    AppReservationOut,
    AppSessionOut,
    AppSiteOut,
    AppSubscriptionIn,
    AppSubscriptionOut,
    AppVehicleIn,
    BleTokenIn,
    BleTokenOut,
    DriverOut,
    DriverPatch,
    DriverTokenOut,
    ExtendSessionIn,
    HomeOut,
    NearestOut,
    PhoneCodeOut,
    PhoneIn,
    PhoneVerifyIn,
    QuoteIn,
    QuoteOut,
    RegisterIn,
    SessionIn,
)
from app.security import (
    create_customer_access_token,
    hash_password,
    new_refresh_token,
    new_sms_code,
    sha256_hex,
    verify_password,
)
from app.services import ble_identity, identification, settings_store
from app.services.audit import audit
from app.services.pricing import price_for_stay

router = APIRouter(prefix="/app", tags=["driver app"])

FREE_CANCELLATION = timedelta(hours=1)
SMS_CODE_TTL = timedelta(minutes=10)
SMS_MAX_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(db: Session, action: str, entity: str, entity_id: object, customer: Customer, data: dict | None = None) -> None:
    audit(db, action, entity, entity_id, actor_kind="customer", data={"customer_id": str(customer.id), **(data or {})})


# --- Account -------------------------------------------------------------------------
def _driver_out(customer: Customer) -> DriverOut:
    vehicles = [VehicleOut.model_validate(v) for v in customer.vehicles]
    return DriverOut(
        id=customer.id, reference=customer.reference, first_name=customer.first_name, last_name=customer.last_name,
        email=customer.email, phone=customer.phone, phone_verified=customer.phone_verified_at is not None,
        address_line=customer.address_line, postcode=customer.postcode, city=customer.city, country=customer.country,
        locale=customer.locale, vehicles=vehicles,
        profile_complete=bool(vehicles and customer.address_line and customer.phone_verified_at),
    )


def _issue(db: Session, customer: Customer, client_name: str | None) -> DriverTokenOut:
    access, expires_in = create_customer_access_token(customer.id, customer.locale)
    refresh, refresh_hash, refresh_expires = new_refresh_token()
    db.add(CustomerRefreshToken(customer_id=customer.id, token_hash=refresh_hash, client_name=client_name,
                                expires_at=refresh_expires))
    db.flush()
    db.refresh(customer)
    return DriverTokenOut(access_token=access, refresh_token=refresh, expires_in=expires_in, driver=_driver_out(customer))


@router.post("/auth/register", response_model=DriverTokenOut, status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)) -> DriverTokenOut:
    """Step 1 of the sign-up: identity and password. Plate, address and phone follow (PATCH /app/me...)."""
    taken = db.scalar(select(Customer.id).where(func.lower(Customer.email) == body.email, Customer.password_hash.is_not(None)))
    if taken:
        raise HTTPException(409, "email_already_used")
    reference = "C-" + str(db.execute(text("SELECT nextval('customer_reference_seq')")).scalar())
    customer = Customer(
        reference=reference, first_name=body.first_name.strip(), last_name=body.last_name.strip(), email=body.email,
        locale=body.locale, password_hash=hash_password(body.password), last_login_at=_now(),
    )
    db.add(customer)
    try:
        db.flush()
    except IntegrityError as exc:  # two sign-ups with the same e-mail at the same time
        raise HTTPException(409, "email_already_used") from exc
    _audit(db, "app.register", "customers", customer.id, customer)
    return _issue(db, customer, body.client_name)


@router.post("/auth/login", response_model=DriverTokenOut)
def login(body: AppLoginIn, db: Session = Depends(get_db)) -> DriverTokenOut:
    customer = db.scalar(
        select(Customer).where(func.lower(Customer.email) == body.email.strip().lower(), Customer.password_hash.is_not(None))
    )
    if customer is None or customer.anonymised_at is not None or not verify_password(body.password, customer.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
    customer.last_login_at = _now()
    return _issue(db, customer, body.client_name)


@router.post("/auth/refresh", response_model=DriverTokenOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db)) -> DriverTokenOut:
    token = db.scalar(select(CustomerRefreshToken).where(CustomerRefreshToken.token_hash == sha256_hex(body.refresh_token)))
    now = _now()
    if token is None or token.revoked_at is not None or token.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh_token_invalid")
    customer = db.get(Customer, token.customer_id)
    if customer is None or customer.password_hash is None or customer.anonymised_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="user_inactive")
    token.revoked_at = now  # rotation: a refresh token works once
    return _issue(db, customer, token.client_name)


@router.post("/auth/logout", status_code=204)
def logout(body: RefreshIn, db: Session = Depends(get_db)) -> Response:
    token = db.scalar(select(CustomerRefreshToken).where(CustomerRefreshToken.token_hash == sha256_hex(body.refresh_token)))
    if token is not None and token.revoked_at is None:
        token.revoked_at = _now()
    return Response(status_code=204)


@router.get("/me", response_model=DriverOut)
def me(customer: Customer = Depends(get_current_customer)) -> DriverOut:
    return _driver_out(customer)


@router.patch("/me", response_model=DriverOut)
def update_me(body: DriverPatch, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> DriverOut:
    """Step 3 of the sign-up (address), and later profile changes."""
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(customer, field, value.strip() if isinstance(value, str) else value)
    _audit(db, "app.profile_update", "customers", customer.id, customer, {"fields": sorted(changes)})
    return _driver_out(customer)


@router.post("/me/phone", response_model=PhoneCodeOut)
def send_phone_code(body: PhoneIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> PhoneCodeOut:
    """Step 4 of the sign-up: send a 6-digit code by SMS to the new number."""
    recent = db.scalar(
        select(func.count()).select_from(CustomerPhoneCode)
        .where(CustomerPhoneCode.customer_id == customer.id, CustomerPhoneCode.created_at > _now() - timedelta(hours=1))
    )
    if recent >= 5:
        raise HTTPException(429, "too_many_codes")
    code, code_hash = new_sms_code()
    db.add(CustomerPhoneCode(customer_id=customer.id, phone=body.phone, code_hash=code_hash, expires_at=_now() + SMS_CODE_TTL))
    _send_sms(body.phone, code)
    settings = get_settings()
    return PhoneCodeOut(sent_to=body.phone, expires_in=int(SMS_CODE_TTL.total_seconds()),
                        debug_code=code if settings.sms_debug else None)


def _send_sms(phone: str, code: str) -> None:
    """SMS gateway hook. Prototype: nothing is sent; with SMS_DEBUG the code is returned to the app.

    Connect your provider here (Swisscom, Twilio, OVH...). Message: "Intel Park : votre code est {code}".
    """
    import logging

    logging.getLogger("parking").info("SMS code requested for %s***", phone[:-4])


@router.post("/me/phone/verify", response_model=DriverOut)
def verify_phone(body: PhoneVerifyIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> DriverOut:
    row = db.scalar(
        select(CustomerPhoneCode)
        .where(CustomerPhoneCode.customer_id == customer.id, CustomerPhoneCode.used_at.is_(None))
        .order_by(CustomerPhoneCode.created_at.desc()).limit(1)
    )
    if row is None or row.expires_at <= _now() or row.attempts >= SMS_MAX_ATTEMPTS:
        raise HTTPException(410, "phone_code_expired")
    row.attempts += 1
    if sha256_hex(body.code) != row.code_hash:
        db.commit()  # keep the failed attempt: the request itself fails and would roll it back
        raise HTTPException(422, "phone_code_invalid")
    row.used_at = _now()
    customer.phone = row.phone
    customer.phone_verified_at = _now()
    _audit(db, "app.phone_verified", "customers", customer.id, customer)
    return _driver_out(customer)


# --- Vehicles -------------------------------------------------------------------------
@router.post("/vehicles", response_model=VehicleOut, status_code=201)
def add_vehicle(body: AppVehicleIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> Vehicle:
    """Step 2 of the sign-up: the licence plate (linked to every payment and to the camera fallback)."""
    plate_norm = "".join(ch for ch in body.plate_display.upper() if ch.isalnum())
    existing = db.scalar(select(Vehicle).where(Vehicle.plate_norm == plate_norm))
    if existing is not None:
        if existing.customer_id is None:  # removed earlier by its owner, or recorded without owner
            existing.customer_id = customer.id
            _audit(db, "app.vehicle_add", "vehicles", existing.id, customer)
        if existing.customer_id == customer.id:
            return existing
        raise HTTPException(409, "plate_already_registered")
    vehicle = Vehicle(customer_id=customer.id, **body.model_dump())
    vehicle.plate_display = vehicle.plate_display.strip().upper()
    db.add(vehicle)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "plate_already_registered") from exc
    db.refresh(vehicle)
    _audit(db, "app.vehicle_add", "vehicles", vehicle.id, customer)
    return vehicle


@router.delete("/vehicles/{vehicle_id}", status_code=204)
def remove_vehicle(vehicle_id: uuid.UUID, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> Response:
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None or vehicle.customer_id != customer.id:
        raise HTTPException(404, "vehicle_not_found")
    vehicle.customer_id = None
    _audit(db, "app.vehicle_remove", "vehicles", vehicle.id, customer)
    return Response(status_code=204)


def _own_vehicle(db: Session, customer: Customer, vehicle_id: uuid.UUID | None) -> Vehicle | None:
    if vehicle_id is None:
        return customer.vehicles[0] if customer.vehicles else None
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None or vehicle.customer_id != customer.id:
        raise HTTPException(404, "vehicle_not_found")
    return vehicle


# --- Sites, bays, prices ---------------------------------------------------------------
def _tariff(db: Session, site_id: uuid.UUID, kind: str) -> Money | None:
    row = db.execute(
        text("""SELECT amount_cents, currency FROM tariffs WHERE kind = :k AND valid_to IS NULL
                AND (site_id = :s OR site_id IS NULL) ORDER BY site_id NULLS LAST LIMIT 1"""),
        {"k": kind, "s": site_id},
    ).first()
    return Money(amount_cents=row.amount_cents, currency=row.currency.strip()) if row else None


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(2 * r * math.asin(math.sqrt(a)))


def _sites(db: Session, lat: float | None, lng: float | None) -> list[AppSiteOut]:
    rows = db.execute(text("""
        SELECT s.id, s.code, s.name, s.address_line, s.city, s.latitude::float AS latitude, s.longitude::float AS longitude,
               coalesce(o.free_bays, 0) AS free_bays, coalesce(o.total_bays, 0) AS total_bays
        FROM sites s LEFT JOIN v_site_occupancy o ON o.site_id = s.id
        WHERE s.is_active ORDER BY s.code""")).all()
    out = []
    for r in rows:
        distance = None
        if lat is not None and lng is not None and r.latitude is not None and r.longitude is not None:
            distance = _distance_m(lat, lng, r.latitude, r.longitude)
        out.append(AppSiteOut(**r._mapping, hourly=_tariff(db, r.id, "hourly"), distance_m=distance))
    if lat is not None and lng is not None:
        out.sort(key=lambda s: (s.distance_m is None, s.distance_m or 0))
    return out


@router.get("/sites", response_model=list[AppSiteOut])
def sites(lat: float | None = Query(None, ge=-90, le=90), lng: float | None = Query(None, ge=-180, le=180),
          customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> list[AppSiteOut]:
    """Car parks with their free bays and hourly price; sorted by distance when lat/lng are given."""
    return _sites(db, lat, lng)


SQL_BAY = """
SELECT v.bay_id AS id, s.code || '/' || z.code || '/' || v.number AS code, s.id AS site_id, s.code AS site_code,
       s.name AS site_name, z.code AS zone_code, z.name AS zone_name, v.number, v.kind::text AS kind, v.status,
       s.latitude::float AS latitude, s.longitude::float AS longitude
FROM v_bay_status v JOIN zones z ON z.id = v.zone_id JOIN sites s ON s.id = z.site_id
WHERE {where}
"""


def _bay_out(db: Session, where: str, params: dict) -> AppBayOut | None:
    row = db.execute(text(SQL_BAY.format(where=where)), params).first()
    if row is None:
        return None
    values = settings_store.get_all(db, row.site_id)
    hourly = _tariff(db, row.site_id, "hourly") or Money(amount_cents=0)
    daily = _tariff(db, row.site_id, "daily") or Money(amount_cents=hourly.amount_cents * 24, currency=hourly.currency)
    return AppBayOut(
        **row._mapping, hourly=hourly, daily=daily, billing_mode=str(values["payments.billing_mode"]),
        free_minutes=int(values["payments.free_minutes"]), max_stay_min=int(values["payments.max_stay_min"]),
    )


def _bay_or_404(db: Session, bay_id: uuid.UUID) -> AppBayOut:
    bay = _bay_out(db, "v.bay_id = :b", {"b": bay_id})
    if bay is None:
        raise HTTPException(404, "bay_not_found")
    return bay


@router.get("/bays/by-code", response_model=AppBayOut)
def bay_by_code(code: str = Query(..., description="Content of the QR code: 'intelpark:bay:P1/EXT/05' or 'P1/EXT/05'"),
                customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> AppBayOut:
    raw = code.strip()
    if raw.lower().startswith("intelpark:bay:"):
        raw = raw[len("intelpark:bay:"):]
    try:  # a QR code may also carry the bay id
        return _bay_or_404(db, uuid.UUID(raw))
    except ValueError:
        pass
    parts = raw.split("/")
    if len(parts) != 3:
        raise HTTPException(422, "bay_code_invalid")
    site_code, zone_code, number = (p.strip().upper() for p in parts)
    bay = _bay_out(db, "upper(s.code) = :s AND upper(z.code) = :z AND (v.number = :n OR ltrim(v.number, '0') = ltrim(:n, '0'))",
                   {"s": site_code, "z": zone_code, "n": number})
    if bay is None:
        raise HTTPException(404, "bay_not_found")
    return bay


@router.get("/bays/{bay_id}", response_model=AppBayOut)
def bay(bay_id: uuid.UUID, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> AppBayOut:
    return _bay_or_404(db, bay_id)


@router.get("/nearest", response_model=NearestOut)
def nearest(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180),
            customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> NearestOut:
    """Closest car park that has a free bay right now, and one free bay in it (outdoor zones first)."""
    candidates = [s for s in _sites(db, lat, lng) if s.free_bays > 0]
    if not candidates:
        raise HTTPException(404, "no_free_bay")
    site = candidates[0]
    bay = _bay_out(db, """v.site_id = :s AND v.status = 'free' AND v.kind <> 'accessible'
                          ORDER BY z.level DESC, z.sort_order, v.number LIMIT 1""", {"s": site.id})
    return NearestOut(site=site, bay=bay)


def _price(db: Session, bay: AppBayOut, start: datetime, end: datetime) -> int:
    return price_for_stay(start, end, bay.hourly.amount_cents, bay.daily.amount_cents, bay.billing_mode, bay.free_minutes)


@router.post("/quote", response_model=QuoteOut)
def quote(body: QuoteIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> QuoteOut:
    """Price of a stay, before paying (the app shows it live while the driver changes the duration)."""
    bay = _bay_or_404(db, body.bay_id)
    start = _now()
    end = start + timedelta(minutes=body.minutes)
    return QuoteOut(bay_id=bay.id, minutes=body.minutes, starts_at=start, ends_at=end,
                    price=Money(amount_cents=_price(db, bay, start, end), currency=bay.hourly.currency))


# --- Payments -----------------------------------------------------------------------------
def _charge(db: Session, customer: Customer, site_id: uuid.UUID, amount: int, currency: str, method: str, **links) -> Payment:
    """Record a payment. Only the "demo" provider exists in this prototype: the payment succeeds at once."""
    provider = str(settings_store.get(db, "payments.provider", site_id))
    if provider != "demo":
        raise HTTPException(501, "payment_provider_not_configured")
    payment = Payment(
        site_id=site_id, customer_id=customer.id, amount_cents=amount, currency=currency,
        vat_rate_bp=int(settings_store.get(db, "payments.vat_bp", site_id)), method=method, status="succeeded",
        provider=provider, provider_ref=f"demo-{uuid.uuid4().hex[:12]}", paid_at=_now(), **links,
    )
    db.add(payment)
    db.flush()
    return payment


# --- Parking sessions ---------------------------------------------------------------------
def _session_out(db: Session, session: ParkingSession, last_payment: Payment | None = None) -> AppSessionOut:
    right = None
    if session.subscription_id:
        right = "subscription"
    elif session.reservation_id:
        right = "reservation"
    elif session.paid_until and session.paid_until > _now():
        right = "paid_session"
    site_name = db.execute(text("SELECT name FROM sites WHERE id = :s"), {"s": session.site_id}).scalar()
    return AppSessionOut(
        id=session.id, bay_id=session.bay_id, bay_label=identification.bay_label(db, session.bay_id), site_name=site_name,
        plate=session.plate_display, entered_at=session.entered_at, paid_until=session.paid_until, status=session.status,
        identified_by=session.identified_by, right=right,
        last_payment=Money(amount_cents=last_payment.amount_cents, currency=last_payment.currency) if last_payment else None,
    )


@router.get("/sessions/current", response_model=AppSessionOut | None)
def current_session(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> AppSessionOut | None:
    session = db.scalar(
        select(ParkingSession).where(ParkingSession.customer_id == customer.id, ParkingSession.status == "open")
        .order_by(ParkingSession.entered_at.desc()).limit(1)
    )
    return _session_out(db, session) if session else None


def _check_max_stay(db: Session, bay: AppBayOut, paid_until: datetime) -> None:
    if bay.max_stay_min and paid_until - _now() > timedelta(minutes=bay.max_stay_min + 1):
        raise HTTPException(422, "max_stay_exceeded")


@router.post("/sessions", response_model=AppSessionOut, status_code=201)
def start_session(body: SessionIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> AppSessionOut:
    """The driver scanned the bay's QR code and pays for a duration.

    Joins the session opened by the presence sensor when the car was detected, or opens it
    (the sensor confirms it a few seconds later). The driver is then identified on the bay ("app").
    """
    bay = _bay_or_404(db, body.bay_id)
    if bay.status == "blocked":
        raise HTTPException(409, "bay_blocked")
    vehicle = _own_vehicle(db, customer, body.vehicle_id)
    now = _now()
    session = db.scalar(select(ParkingSession).where(ParkingSession.bay_id == bay.id, ParkingSession.status == "open").with_for_update())
    if session is not None and session.customer_id not in (None, customer.id):
        raise HTTPException(409, "bay_taken_by_other")
    if session is None:
        session = ParkingSession(site_id=bay.site_id, bay_id=bay.id, entered_at=now,
                                 plate_display=vehicle.plate_display if vehicle else None)
        db.add(session)
        db.flush()
    elif session.plate_display is None and vehicle is not None:
        session.plate_display = vehicle.plate_display
    identification.link_session(db, session, customer.id, vehicle.id if vehicle else None, "app", now)
    start = max(session.paid_until or now, now)
    end = start + timedelta(minutes=body.minutes)
    _check_max_stay(db, bay, end)
    amount = _price(db, bay, start, end)
    payment = _charge(db, customer, bay.site_id, amount, bay.hourly.currency, body.payment_method, session_id=session.id)
    session.paid_until = end
    _audit(db, "app.session_pay", "parking_sessions", session.id, customer, {"minutes": body.minutes, "amount_cents": amount})
    return _session_out(db, session, payment)


@router.post("/sessions/{session_id}/extend", response_model=AppSessionOut)
def extend_session(session_id: uuid.UUID, body: ExtendSessionIn, customer: Customer = Depends(get_current_customer),
                   db: Session = Depends(get_db)) -> AppSessionOut:
    session = db.get(ParkingSession, session_id)
    if session is None or session.customer_id != customer.id:
        raise HTTPException(404, "session_not_found")
    if session.status != "open" or session.bay_id is None:
        raise HTTPException(409, "session_closed")
    bay = _bay_or_404(db, session.bay_id)
    start = max(session.paid_until or _now(), _now())
    end = start + timedelta(minutes=body.minutes)
    _check_max_stay(db, bay, end)
    amount = _price(db, bay, start, end)
    payment = _charge(db, customer, session.site_id, amount, bay.hourly.currency, "app", session_id=session.id)
    session.paid_until = end
    _audit(db, "app.session_extend", "parking_sessions", session.id, customer, {"minutes": body.minutes, "amount_cents": amount})
    return _session_out(db, session, payment)


# --- Reservations ----------------------------------------------------------------------------
def _reservation_out(db: Session, r: Reservation) -> AppReservationOut:
    site_name = db.execute(
        text("SELECT s.name FROM bays b JOIN zones z ON z.id = b.zone_id JOIN sites s ON s.id = z.site_id WHERE b.id = :b"),
        {"b": r.bay_id},
    ).scalar()
    return AppReservationOut(
        id=r.id, bay_id=r.bay_id, bay_label=identification.bay_label(db, r.bay_id) or "", site_name=site_name,
        starts_at=r.starts_at, ends_at=r.ends_at, status=r.status,
        price=Money(amount_cents=r.price_cents, currency=r.currency.strip()),
        free_cancellation_until=r.starts_at - FREE_CANCELLATION,
    )


@router.get("/reservations", response_model=list[AppReservationOut])
def my_reservations(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> list[AppReservationOut]:
    rows = db.scalars(
        select(Reservation).where(Reservation.customer_id == customer.id, Reservation.ends_at > _now() - timedelta(days=30))
        .order_by(Reservation.starts_at.desc()).limit(50)
    ).all()
    return [_reservation_out(db, r) for r in rows]


SQL_FREE_BAY_FOR_PERIOD = """
SELECT b.id FROM bays b JOIN zones z ON z.id = b.zone_id
WHERE z.site_id = :s AND b.admin_state = 'open' AND b.kind <> 'accessible'
  AND NOT EXISTS (SELECT 1 FROM reservations r WHERE r.bay_id = b.id AND r.status IN ('confirmed', 'active')
                  AND tstzrange(r.starts_at, r.ends_at) && tstzrange(:a, :e))
  AND NOT EXISTS (SELECT 1 FROM subscriptions su WHERE su.bay_id = b.id AND su.status = 'active'
                  AND current_date BETWEEN su.starts_on AND su.ends_on)
  AND NOT (b.is_occupied AND CAST(:a AS timestamptz) < now() + interval '30 minutes')
ORDER BY z.level DESC, z.sort_order, b.number
LIMIT 1
"""


@router.post("/reservations", response_model=AppReservationOut, status_code=201)
def book(body: AppReservationIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> AppReservationOut:
    if body.ends_at <= body.starts_at:
        raise HTTPException(422, "ends_before_start")
    if body.ends_at <= _now():
        raise HTTPException(422, "invalid_period")
    bay_id = body.bay_id
    if bay_id is None:
        bay_id = db.execute(text(SQL_FREE_BAY_FOR_PERIOD), {"s": body.site_id, "a": body.starts_at, "e": body.ends_at}).scalar()
        if bay_id is None:
            raise HTTPException(409, "no_free_bay")
    bay = _bay_or_404(db, bay_id)
    if bay.site_id != body.site_id:
        raise HTTPException(422, "bay_not_in_site")
    if bay.status == "blocked":
        raise HTTPException(409, "bay_blocked")
    vehicle = _own_vehicle(db, customer, body.vehicle_id)
    amount = _price(db, bay, body.starts_at, body.ends_at)
    res = Reservation(customer_id=customer.id, vehicle_id=vehicle.id if vehicle else None, bay_id=bay.id,
                      starts_at=body.starts_at, ends_at=body.ends_at, channel="app", price_cents=amount,
                      currency=bay.hourly.currency)
    db.add(res)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:  # reservations_no_overlap exclusion constraint
        raise HTTPException(409, "bay_already_booked") from exc
    _charge(db, customer, bay.site_id, amount, bay.hourly.currency, "card", reservation_id=res.id)
    _audit(db, "app.reservation_create", "reservations", res.id, customer, {"amount_cents": amount})
    return _reservation_out(db, res)


@router.post("/reservations/{reservation_id}/cancel", response_model=AppReservationOut)
def cancel_booking(reservation_id: uuid.UUID, customer: Customer = Depends(get_current_customer),
                   db: Session = Depends(get_db)) -> AppReservationOut:
    res = db.get(Reservation, reservation_id)
    if res is None or res.customer_id != customer.id:
        raise HTTPException(404, "reservation_not_found")
    if res.status != "confirmed":
        raise HTTPException(409, "reservation_closed")
    res.status, res.cancelled_at = "cancelled", _now()
    refunded = _now() <= res.starts_at - FREE_CANCELLATION
    if refunded:
        for payment in db.scalars(select(Payment).where(Payment.reservation_id == res.id, Payment.status == "succeeded")):
            payment.status = "refunded"
    _audit(db, "app.reservation_cancel", "reservations", res.id, customer, {"refunded": refunded})
    return _reservation_out(db, res)


# --- Subscriptions ---------------------------------------------------------------------------
def _plan_name(plan: SubscriptionPlan, locale: str) -> str:
    return plan.name.get(locale) or plan.name.get("fr") or plan.code


@router.get("/subscription-plans", response_model=list[AppPlanOut])
def plans(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> list[AppPlanOut]:
    rows = db.scalars(select(SubscriptionPlan).order_by(SubscriptionPlan.period_months, SubscriptionPlan.price_cents.desc())).all()
    return [AppPlanOut(id=p.id, code=p.code, name=_plan_name(p, customer.locale),
                       price=Money(amount_cents=p.price_cents, currency=p.currency.strip()),
                       period_months=p.period_months, access_hours=p.access_hours) for p in rows]


def _subscription_out(db: Session, s: Subscription, locale: str) -> AppSubscriptionOut:
    return AppSubscriptionOut(
        id=s.id, site_name=s.site.name, plan_name=_plan_name(s.plan, locale),
        price=Money(amount_cents=s.price_cents, currency=s.currency.strip()), period_months=s.plan.period_months,
        starts_on=s.starts_on, ends_on=s.ends_on, auto_renew=s.auto_renew, status=s.status,
        bay_label=identification.bay_label(db, s.bay_id),
    )


@router.get("/subscriptions", response_model=list[AppSubscriptionOut])
def my_subscriptions(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> list[AppSubscriptionOut]:
    rows = db.scalars(select(Subscription).where(Subscription.customer_id == customer.id).order_by(Subscription.ends_on.desc())).all()
    return [_subscription_out(db, s, customer.locale) for s in rows]


@router.post("/subscriptions", response_model=AppSubscriptionOut, status_code=201)
def subscribe(body: AppSubscriptionIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> AppSubscriptionOut:
    """Monthly rental of a parking right on a car park (first month paid now, then every month)."""
    plan = db.get(SubscriptionPlan, body.plan_id)
    if plan is None:
        raise HTTPException(404, "plan_not_found")
    if db.execute(text("SELECT 1 FROM sites WHERE id = :s AND is_active"), {"s": body.site_id}).scalar() is None:
        raise HTTPException(404, "site_not_found")
    if body.starts_on < date.today() or body.starts_on > date.today() + timedelta(days=90):
        raise HTTPException(422, "invalid_period")
    vehicle = _own_vehicle(db, customer, body.vehicle_id)
    sub = Subscription(customer_id=customer.id, vehicle_id=vehicle.id if vehicle else None, site_id=body.site_id,
                       plan_id=plan.id, price_cents=plan.price_cents, currency=plan.currency, starts_on=body.starts_on,
                       ends_on=add_months(body.starts_on, plan.period_months), auto_renew=True)
    db.add(sub)
    db.flush()
    _charge(db, customer, body.site_id, plan.price_cents, plan.currency.strip(), "card", subscription_id=sub.id)
    _audit(db, "app.subscription_create", "subscriptions", sub.id, customer, {"plan": plan.code})
    db.refresh(sub)
    return _subscription_out(db, sub, customer.locale)


# --- Home -----------------------------------------------------------------------------------
@router.get("/home", response_model=HomeOut)
def home(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> HomeOut:
    next_res = db.scalar(
        select(Reservation).where(Reservation.customer_id == customer.id, Reservation.status.in_(["confirmed", "active"]),
                                  Reservation.ends_at > _now()).order_by(Reservation.starts_at).limit(1)
    )
    subs = db.scalars(
        select(Subscription).where(Subscription.customer_id == customer.id, Subscription.status == "active",
                                   Subscription.ends_on >= date.today())
    ).all()
    return HomeOut(
        driver=_driver_out(customer),
        current_session=current_session(customer, db),
        next_reservation=_reservation_out(db, next_res) if next_res else None,
        subscriptions=[_subscription_out(db, s, customer.locale) for s in subs],
    )


# --- Bluetooth identity ------------------------------------------------------------------------
@router.post("/ble/token", response_model=BleTokenOut)
def ble_token(body: BleTokenIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)) -> BleTokenOut:
    """Identity token the phone shows to the cabinets over Bluetooth (verified offline by the Pi)."""
    if not settings_store.get(db, "ble.enabled"):
        raise HTTPException(409, "ble_disabled")
    vehicles = [(v.id, v.plate_norm) for v in customer.vehicles]
    ttl = int(settings_store.get(db, "ble.token_hours")) * 3600
    try:
        token, exp = ble_identity.issue_token(customer.id, vehicles, body.public_key, ttl)
    except ble_identity.TokenError as exc:
        raise HTTPException(422, str(exc)) from exc
    _audit(db, "app.ble_token", "customers", customer.id, customer, {"device": body.device_name})
    return BleTokenOut(token=token, expires_at=datetime.fromtimestamp(exp, UTC), server_public_key=ble_identity.public_key_b64())
