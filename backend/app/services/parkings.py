"""The "parking" object (table sites) of the multi-account platform.

A parking belongs to an account (organization) and has a management mode chosen at creation:

* qr_code          places are created by generating QR codes (one sticker per place); no control device;
* presence_sensor  each presence sensor added creates its place and the place's QR code; the sensors hang
                   on a control cabinet (Raspberry Pi) whose camera view and detection are on "Control devices";
* smart_barrier    the places are created with the parking (capacity); each barrier added gets its own QR code
                   (booking a place of the parking from the barrier); the barrier camera reads the plates.

Every place lives in the parking's single default zone (code "A") unless the parking already had zones
(prototype data).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.models import (
    Bay,
    Controller,
    Customer,
    Device,
    Organization,
    ParkingSession,
    Payment,
    PaymentAlarm,
    ReportExport,
    Reservation,
    Site,
    StaffUser,
    Subscription,
    Zone,
)
from app.security import new_controller_key
from app.services import bay_qr, settings_store

MODES = ("qr_code", "presence_sensor", "smart_barrier")
DEFAULT_ZONE = "A"
SENSOR_KINDS = {"front": "front_presence_sensor", "vertical": "vertical_presence_sensor"}
MAX_PLACES = 2000


class ParkingError(ValueError):
    """Business rule refused (the message is a stable error code shown translated to the user)."""


def _slug(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper()
    words = [w for w in re.split(r"[^A-Z0-9]+", plain) if w and w not in {"PARKING", "DE", "DU", "DES", "LA", "LE", "LES", "L", "D"}]
    return (words[-1] if words else "PARK")[:8]


def unique_site_code(db: Session, name: str) -> str:
    """Short code printed in the QR codes ("GARE/A/05"): from the name, made unique on the platform."""
    base = _slug(name)
    code, n = base, 1
    while db.scalar(select(Site.id).where(func.upper(Site.code) == code)) is not None:
        n += 1
        code = f"{base}{n}"
    return code


def default_zone(db: Session, site: Site) -> Zone:
    zone = db.scalar(select(Zone).where(Zone.site_id == site.id).order_by(Zone.sort_order, Zone.code).limit(1))
    if zone is None:
        zone = Zone(site_id=site.id, code=DEFAULT_ZONE, name=site.name, level=0, sort_order=0)
        db.add(zone)
        db.flush()
    return zone


def create_parking(db: Session, org: Organization, *, name: str, capacity: int, mode: str, address_line: str | None,
                   postcode: str | None, city: str | None, country: str = "CH", latitude: float | None = None,
                   longitude: float | None = None) -> Site:
    if mode not in MODES:
        raise ParkingError("invalid_mode")
    if not 1 <= capacity <= MAX_PLACES:
        raise ParkingError("invalid_capacity")
    site = Site(code=unique_site_code(db, name), name=name.strip(), address_line=address_line, postcode=postcode,
                city=city, country=country, latitude=latitude, longitude=longitude, timezone="Europe/Zurich",
                organization_id=org.id, management_mode=mode, capacity=capacity, is_active=True)
    db.add(site)
    db.flush()
    # Currency and time zone of the parking's country (the platform default stays for the others)
    local = {"CH": ("CHF", "Europe/Zurich"), "FR": ("EUR", "Europe/Paris"), "DE": ("EUR", "Europe/Berlin")}.get(country)
    if local:
        site.timezone = local[1]
        settings_store.put_many(db, {"general.currency": local[0], "general.timezone": local[1]}, None, site.id)
    zone = default_zone(db, site)
    if mode == "smart_barrier":
        # The barriers count the cars in and out: the places exist from the start, numbered 001...
        width = max(2, len(str(capacity)))
        for i in range(1, capacity + 1):
            db.add(Bay(zone_id=zone.id, number=str(i).zfill(width), plan_row=(i - 1) // 14 + 1, plan_col=(i - 1) % 14 + 1))
        db.flush()
    return site


def _next_numbers(db: Session, zone: Zone, count: int) -> list[str]:
    existing = db.scalars(select(Bay.number).where(Bay.zone_id == zone.id)).all()
    taken = {n.upper() for n in existing}
    numeric = [int(n) for n in existing if n.isdigit()]
    start = (max(numeric) if numeric else 0) + 1
    width = max(2, max((len(n) for n in existing if n.isdigit()), default=2))
    out, n = [], start
    while len(out) < count:
        candidate = str(n).zfill(width)
        if candidate.upper() not in taken:
            out.append(candidate)
        n += 1
    return out


def _new_bay_reference(db: Session) -> str:
    while True:
        candidate = bay_qr.new_reference()
        if (db.scalar(select(Bay.id).where(Bay.reference == candidate)) is None
                and db.scalar(select(Device.id).where(Device.reference == candidate)) is None):
            return candidate


def _place_count(db: Session, site: Site) -> int:
    return db.scalar(select(func.count(Bay.id)).join(Zone, Zone.id == Bay.zone_id).where(Zone.site_id == site.id)) or 0


def generate_places(db: Session, site: Site, count: int, kind: str = "standard", rental_status: str = "not_rented") -> list[Bay]:
    """QR code mode: each generated QR code creates one place, numbered after the last one."""
    if not 1 <= count <= 500:
        raise ParkingError("invalid_count")
    if _place_count(db, site) + count > MAX_PLACES:
        raise ParkingError("too_many_places")
    zone = default_zone(db, site)
    now = datetime.now(UTC)
    bays = []
    already = _place_count(db, site)
    for i, number in enumerate(_next_numbers(db, zone, count)):
        total = already + i
        bay = Bay(zone_id=zone.id, number=number, kind=kind, rental_status=rental_status, reference=_new_bay_reference(db),
                  qr_generated_at=now, plan_row=total // 14 + 1, plan_col=total % 14 + 1)
        db.add(bay)
        db.flush()
        bays.append(bay)
    site.capacity = max(site.capacity, _place_count(db, site))
    return bays


def ensure_controller(db: Session, site: Site, controller_id: uuid.UUID | None = None) -> tuple[Controller, str | None]:
    """The cabinet a new sensor or barrier hangs on. Creates the parking's first cabinet when there is none:
    its API key is returned once (to be typed into the Raspberry Pi) and never stored in clear."""
    if controller_id is not None:
        controller = db.get(Controller, controller_id)
        if controller is None or controller.site_id != site.id:
            raise ParkingError("controller_not_found")
        return controller, None
    controller = db.scalar(select(Controller).where(Controller.site_id == site.id, Controller.is_active)
                           .order_by(Controller.created_at).limit(1))
    if controller is not None:
        return controller, None
    return new_controller(db, site, None)


def new_controller(db: Session, site: Site, name: str | None) -> tuple[Controller, str]:
    n = (db.scalar(select(func.count(Controller.id)).where(Controller.site_id == site.id)) or 0) + 1
    code = f"{site.code}-CTRL-{n:02d}"
    while db.scalar(select(Controller.id).where(Controller.code == code)) is not None:
        n += 1
        code = f"{site.code}-CTRL-{n:02d}"
    key, prefix, key_hash = new_controller_key()
    controller = Controller(site_id=site.id, code=code, name=name or f"Armoire {n}", api_key_prefix=prefix,
                            api_key_hash=key_hash, hardware="Raspberry Pi")
    db.add(controller)
    db.flush()
    return controller, key


def _next_device_code(db: Session, controller: Controller, prefix: str) -> str:
    codes = db.scalars(select(Device.code).where(Device.controller_id == controller.id, Device.code.like(f"{prefix}-%"))).all()
    nums = [int(c.split("-")[-1]) for c in codes if c.split("-")[-1].isdigit()]
    return f"{prefix}-{(max(nums) if nums else 0) + 1:03d}"


def add_sensor(db: Session, site: Site, *, kind: str = "front", address: str | None = None, live_url: str | None = None,
               controller_id: uuid.UUID | None = None, bay_kind: str = "standard") -> tuple[Device, Bay, str | None]:
    """Presence sensor mode: the sensor creates its place and the place's QR code."""
    if kind not in SENSOR_KINDS:
        raise ParkingError("invalid_sensor_kind")
    controller, key = ensure_controller(db, site, controller_id)
    bay = generate_places(db, site, 1, kind=bay_kind)[0]
    device = Device(controller_id=controller.id, bay_id=bay.id, code=_next_device_code(db, controller, "CP"),
                    kind=SENSOR_KINDS[kind], label=f"{site.code}/{bay.number}", address=address or None,
                    live_url=live_url or None, status="ok")
    db.add(device)
    db.flush()
    return device, bay, key


def add_barrier(db: Session, site: Site, *, name: str, direction: str = "entry", address: str | None = None,
                controller_id: uuid.UUID | None = None) -> tuple[Device, str | None]:
    """Smart barrier mode: each barrier gets a QR code to book a place of the parking."""
    if direction not in ("entry", "exit"):
        raise ParkingError("invalid_direction")
    controller, key = ensure_controller(db, site, controller_id)
    device = Device(controller_id=controller.id, code=_next_device_code(db, controller, "BR"), kind="barrier",
                    label=f"{name.strip()} · {direction}", address=address or None, status="ok",
                    reference=_new_bay_reference(db), qr_generated_at=datetime.now(UTC))
    db.add(device)
    db.flush()
    return device, key


def barrier_name(device: Device) -> str:
    return (device.label or device.code).rsplit(" · ", 1)[0]


def barrier_direction(device: Device) -> str:
    label = device.label or ""
    return label.rsplit(" · ", 1)[1] if " · " in label else "entry"


def bay_in_use(db: Session, bay_id: uuid.UUID) -> bool:
    return bool(db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM reservations WHERE bay_id = :b)
            OR EXISTS (SELECT 1 FROM parking_sessions WHERE bay_id = :b AND status = 'open')
            OR EXISTS (SELECT 1 FROM subscriptions WHERE bay_id = :b AND status = 'active')"""), {"b": bay_id}).scalar())


def delete_place(db: Session, bay: Bay) -> None:
    if bay_in_use(db, bay.id):
        raise ParkingError("bay_in_use")
    db.execute(delete(Device).where(Device.bay_id == bay.id, Device.kind.in_(tuple(SENSOR_KINDS.values()))))
    db.delete(bay)
    db.flush()


def delete_parking(db: Session, site: Site) -> None:
    """Super user only: the parking and everything recorded in it (bookings, stays, payments, alarms, devices)."""
    bay_ids = select(Bay.id).join(Zone, Zone.id == Bay.zone_id).where(Zone.site_id == site.id).scalar_subquery()
    db.execute(delete(PaymentAlarm).where(PaymentAlarm.site_id == site.id))
    db.execute(delete(Payment).where(Payment.site_id == site.id))
    db.execute(delete(ParkingSession).where(ParkingSession.site_id == site.id))
    db.execute(update(Payment).where(Payment.reservation_id.in_(select(Reservation.id).where(Reservation.bay_id.in_(bay_ids))))
               .values(reservation_id=None))
    db.execute(delete(Reservation).where(Reservation.bay_id.in_(bay_ids)))
    db.execute(update(Payment).where(Payment.subscription_id.in_(select(Subscription.id).where(Subscription.site_id == site.id)))
               .values(subscription_id=None))
    db.execute(delete(Subscription).where(Subscription.site_id == site.id))
    db.execute(update(ReportExport).where(ReportExport.site_id == site.id).values(site_id=None))
    site_id = site.id
    db.expunge(site)
    # zones, bays, cabinets, devices, tariffs, settings and access rights are removed by ON DELETE CASCADE
    db.execute(delete(Site).where(Site.id == site_id))
    db.expire_all()


def delete_organization(db: Session, org: Organization) -> None:
    for site in db.scalars(select(Site).where(Site.organization_id == org.id)).all():
        delete_parking(db, site)
    db.execute(update(Customer).where(Customer.organization_id == org.id).values(organization_id=None))
    db.execute(delete(StaffUser).where(StaffUser.organization_id == org.id))
    org_id = org.id
    db.expunge(org)
    db.execute(delete(Organization).where(Organization.id == org_id))
    db.expire_all()
