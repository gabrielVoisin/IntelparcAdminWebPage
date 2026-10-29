"""SQLAlchemy 2.0 ORM models.

They mirror app/db/sql/0001_schema.sql, which is the source of truth applied by Alembic.
Names, types and enum values are the same ones the Android app and the Raspberry Pi
agent see in the JSON API.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM, INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP


def pg_enum(name: str, *values: str) -> ENUM:
    return ENUM(*values, name=name, create_type=False)


StaffRole = pg_enum("staff_role", "admin", "manager", "agent", "viewer")
BayKind = pg_enum("bay_kind", "standard", "accessible", "ev")
BayAdminState = pg_enum("bay_admin_state", "open", "blocked")
TariffKind = pg_enum("tariff_kind", "hourly", "daily", "monthly")
ReservationStatus = pg_enum("reservation_status", "confirmed", "active", "completed", "cancelled", "no_show")
BookingChannel = pg_enum("booking_channel", "web", "app", "phone", "desk")
SubscriptionStatus = pg_enum("subscription_status", "active", "suspended", "terminated", "expired")
SessionStatus = pg_enum("session_status", "open", "closed")
PaymentMethod = pg_enum("payment_method", "card", "contactless", "app", "invoice", "cash")
PaymentStatus = pg_enum("payment_status", "pending", "succeeded", "failed", "refunded")
AlarmReason = pg_enum(
    "alarm_reason", "no_payment", "overstay", "plate_not_recognised", "lost_ticket", "accessible_bay_misuse",
    "reserved_bay_misuse",
)
IdentificationMethod = pg_enum("identification_method", "bluetooth", "camera", "app", "manual")
IdentificationStatus = pg_enum(
    "identification_status", "authorised", "payment_required", "reserved_by_other", "wrong_bay", "pending"
)
AlarmStatus = pg_enum("alarm_status", "open", "acknowledged", "fined")
FineStatus = pg_enum("fine_status", "issued", "paid", "cancelled")
DeviceKind = pg_enum(
    "device_kind", "camera", "front_presence_sensor", "vertical_presence_sensor", "barrier", "pay_terminal", "display"
)
DeviceStatus = pg_enum("device_status", "ok", "fault", "offline")
EventPriority = pg_enum("event_priority", "alarm", "warning", "info")
ReportFormat = pg_enum("report_format", "pdf", "xlsx")

TS = TIMESTAMP(timezone=True)


class Base(DeclarativeBase):
    pass


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


# --- Sites, zones, bays --------------------------------------------------
class Site(UUIDPk, Timestamps, Base):
    __tablename__ = "sites"
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    address_line: Mapped[str | None] = mapped_column(Text)
    postcode: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(2), default="FR")
    timezone: Mapped[str] = mapped_column(Text, default="Europe/Paris")
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    zones: Mapped[list[Zone]] = relationship(back_populates="site", order_by="Zone.sort_order")


class Zone(UUIDPk, Timestamps, Base):
    __tablename__ = "zones"
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    level: Mapped[int] = mapped_column(SmallInteger, default=0)
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0)

    site: Mapped[Site] = relationship(back_populates="zones")
    bays: Mapped[list[Bay]] = relationship(back_populates="zone", order_by="Bay.number")


class Bay(UUIDPk, Timestamps, Base):
    __tablename__ = "bays"
    zone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("zones.id", ondelete="CASCADE"))
    number: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(BayKind, default="standard")
    admin_state: Mapped[str] = mapped_column(BayAdminState, default="open")
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    is_occupied: Mapped[bool] = mapped_column(Boolean, default=False)
    occupied_since: Mapped[datetime | None] = mapped_column(TS)
    plan_row: Mapped[int | None] = mapped_column(SmallInteger)
    plan_col: Mapped[int | None] = mapped_column(SmallInteger)
    photo_x: Mapped[float | None] = mapped_column(Numeric(5, 2))
    photo_y: Mapped[float | None] = mapped_column(Numeric(5, 2))

    zone: Mapped[Zone] = relationship(back_populates="bays")


# --- Staff ---------------------------------------------------------------
class StaffUser(UUIDPk, Timestamps, Base):
    __tablename__ = "staff_users"
    email: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(StaffRole, default="viewer")
    locale: Mapped[str] = mapped_column(Text, default="fr")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret: Mapped[str | None] = mapped_column(Text)
    invited_at: Mapped[datetime | None] = mapped_column(TS)
    last_login_at: Mapped[datetime | None] = mapped_column(TS)

    sites: Mapped[list[Site]] = relationship(secondary="staff_site_access")


class StaffSiteAccess(Base):
    __tablename__ = "staff_site_access"
    staff_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_users.id", ondelete="CASCADE"), primary_key=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True)


class RefreshToken(UUIDPk, Timestamps, Base):
    __tablename__ = "refresh_tokens"
    staff_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    client_name: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


# --- Customers -----------------------------------------------------------
class Customer(UUIDPk, Timestamps, Base):
    __tablename__ = "customers"
    reference: Mapped[str] = mapped_column(Text, unique=True)
    first_name: Mapped[str] = mapped_column(Text)
    last_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    address_line: Mapped[str | None] = mapped_column(Text)
    postcode: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(2), default="FR")
    locale: Mapped[str] = mapped_column(Text, default="fr")
    company_name: Mapped[str | None] = mapped_column(Text)
    anonymised_at: Mapped[datetime | None] = mapped_column(TS)
    # Driver app account (schema v2)
    password_hash: Mapped[str | None] = mapped_column(Text)
    phone_verified_at: Mapped[datetime | None] = mapped_column(TS)
    last_login_at: Mapped[datetime | None] = mapped_column(TS)

    vehicles: Mapped[list[Vehicle]] = relationship(back_populates="customer")


class CustomerRefreshToken(UUIDPk, Timestamps, Base):
    __tablename__ = "customer_refresh_tokens"
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    client_name: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class CustomerPhoneCode(UUIDPk, Timestamps, Base):
    __tablename__ = "customer_phone_codes"
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    phone: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0)
    expires_at: Mapped[datetime] = mapped_column(TS)
    used_at: Mapped[datetime | None] = mapped_column(TS)


class Vehicle(UUIDPk, Timestamps, Base):
    __tablename__ = "vehicles"
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    plate_display: Mapped[str] = mapped_column(Text)
    plate_norm: Mapped[str] = mapped_column(Text, Computed("normalize_plate(plate_display)", persisted=True))
    country: Mapped[str | None] = mapped_column(String(2))
    make: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(Text)

    customer: Mapped[Customer | None] = relationship(back_populates="vehicles")


# --- Pricing, subscriptions, reservations -------------------------------
class Tariff(UUIDPk, Timestamps, Base):
    __tablename__ = "tariffs"
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(TariffKind)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    valid_from: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id", ondelete="SET NULL"))


class SubscriptionPlan(UUIDPk, Timestamps, Base):
    __tablename__ = "subscription_plans"
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[dict] = mapped_column(JSONB)
    price_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    period_months: Mapped[int] = mapped_column(SmallInteger, default=1)
    access_hours: Mapped[str | None] = mapped_column(Text)


class Subscription(UUIDPk, Timestamps, Base):
    __tablename__ = "subscriptions"
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"))
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"))
    bay_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bays.id", ondelete="SET NULL"))
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscription_plans.id"))
    price_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(SubscriptionStatus, default="active")
    payment_overdue: Mapped[bool] = mapped_column(Boolean, default=False)
    terminated_at: Mapped[datetime | None] = mapped_column(TS)

    customer: Mapped[Customer] = relationship()
    vehicle: Mapped[Vehicle | None] = relationship()
    plan: Mapped[SubscriptionPlan] = relationship()
    bay: Mapped[Bay | None] = relationship()
    site: Mapped[Site] = relationship()


class Reservation(UUIDPk, Timestamps, Base):
    __tablename__ = "reservations"
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"))
    bay_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bays.id"))
    starts_at: Mapped[datetime] = mapped_column(TS)
    ends_at: Mapped[datetime] = mapped_column(TS)
    status: Mapped[str] = mapped_column(ReservationStatus, default="confirmed")
    channel: Mapped[str] = mapped_column(BookingChannel, default="web")
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    cancelled_at: Mapped[datetime | None] = mapped_column(TS)

    customer: Mapped[Customer] = relationship()
    vehicle: Mapped[Vehicle | None] = relationship()
    bay: Mapped[Bay] = relationship()


# --- Sessions, payments, alarms, fines ----------------------------------
class ParkingSession(UUIDPk, Timestamps, Base):
    __tablename__ = "parking_sessions"
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"))
    bay_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bays.id", ondelete="SET NULL"))
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"))
    plate_display: Mapped[str | None] = mapped_column(Text)
    plate_norm: Mapped[str | None] = mapped_column(Text, Computed("normalize_plate(plate_display)", persisted=True))
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reservations.id", ondelete="SET NULL"))
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    entered_at: Mapped[datetime] = mapped_column(TS)
    exited_at: Mapped[datetime | None] = mapped_column(TS)
    paid_until: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(SessionStatus, default="open")
    # Who is parked and how they were recognised (schema v2)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    identified_by: Mapped[str | None] = mapped_column(IdentificationMethod)
    identified_at: Mapped[datetime | None] = mapped_column(TS)

    bay: Mapped[Bay | None] = relationship()


class Payment(UUIDPk, Timestamps, Base):
    __tablename__ = "payments"
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id"))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parking_sessions.id", ondelete="SET NULL"))
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reservations.id", ondelete="SET NULL"))
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    vat_rate_bp: Mapped[int] = mapped_column(Integer, default=2000)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    method: Mapped[str] = mapped_column(PaymentMethod)
    status: Mapped[str] = mapped_column(PaymentStatus, default="pending")
    provider_ref: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(TS)


class PaymentAlarm(UUIDPk, Timestamps, Base):
    __tablename__ = "payment_alarms"
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"))
    bay_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bays.id", ondelete="SET NULL"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parking_sessions.id", ondelete="SET NULL"))
    plate_display: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(AlarmReason)
    detail: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    status: Mapped[str] = mapped_column(AlarmStatus, default="open")
    handled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id", ondelete="SET NULL"))
    handled_at: Mapped[datetime | None] = mapped_column(TS)

    bay: Mapped[Bay | None] = relationship()


class Fine(UUIDPk, Timestamps, Base):
    __tablename__ = "fines"
    alarm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_alarms.id", ondelete="CASCADE"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    status: Mapped[str] = mapped_column(FineStatus, default="issued")
    issued_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id", ondelete="SET NULL"))
    issued_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(TS)


# --- Control cabinets (Raspberry Pi) ------------------------------------
class Controller(UUIDPk, Timestamps, Base):
    __tablename__ = "controllers"
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    api_key_prefix: Mapped[str] = mapped_column(Text, unique=True)
    api_key_hash: Mapped[str] = mapped_column(Text)
    hardware: Mapped[str | None] = mapped_column(Text)
    agent_version: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)
    last_seen_at: Mapped[datetime | None] = mapped_column(TS)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    site: Mapped[Site] = relationship()
    devices: Mapped[list[Device]] = relationship(back_populates="controller")


class Device(UUIDPk, Timestamps, Base):
    __tablename__ = "devices"
    controller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("controllers.id", ondelete="CASCADE"))
    bay_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bays.id", ondelete="SET NULL"))
    code: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(DeviceKind)
    label: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    live_url: Mapped[str | None] = mapped_column(Text)            # sensor's own HTTP service (live view)
    status: Mapped[str] = mapped_column(DeviceStatus, default="ok")
    last_seen_at: Mapped[datetime | None] = mapped_column(TS)

    controller: Mapped[Controller] = relationship(back_populates="devices")


class DeviceEvent(UUIDPk, Timestamps, Base):
    __tablename__ = "device_events"
    controller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("controllers.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    event_uid: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(EventPriority)
    message_key: Mapped[str] = mapped_column(Text)
    raised_at: Mapped[datetime] = mapped_column(TS)
    cleared_at: Mapped[datetime | None] = mapped_column(TS)
    acknowledged_at: Mapped[datetime | None] = mapped_column(TS)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id", ondelete="SET NULL"))


class BleIdentification(UUIDPk, Timestamps, Base):
    """A phone verified over Bluetooth by a cabinet (see docs/BLUETOOTH.md)."""

    __tablename__ = "ble_identifications"
    controller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("controllers.id", ondelete="CASCADE"))
    identification_uid: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"))
    bay_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bays.id", ondelete="SET NULL"))
    rssi: Mapped[int | None] = mapped_column(SmallInteger)
    identified_at: Mapped[datetime] = mapped_column(TS)
    received_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parking_sessions.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(IdentificationStatus, default="pending")


class AlarmEvidence(UUIDPk, Timestamps, Base):
    """Photo of the plate taken by an agent before a fine."""

    __tablename__ = "alarm_evidence"
    alarm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_alarms.id", ondelete="CASCADE"))
    file_name: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text, default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text)
    plate_read: Mapped[str | None] = mapped_column(Text)
    taken_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id", ondelete="SET NULL"))
    taken_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class BayReading(Base):
    __tablename__ = "bay_readings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    bay_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bays.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    reading_uid: Mapped[str] = mapped_column(Text)
    is_occupied: Mapped[bool] = mapped_column(Boolean)
    plate_display: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    measured_at: Mapped[datetime] = mapped_column(TS)
    received_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


# --- Settings, notifications, reports, audit ----------------------------
class Setting(UUIDPk, Timestamps, Base):
    __tablename__ = "settings"
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(Text)
    value: Mapped[object] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id", ondelete="SET NULL"))


class NotificationRule(UUIDPk, Timestamps, Base):
    __tablename__ = "notification_rules"
    event: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class PushDevice(UUIDPk, Timestamps, Base):
    __tablename__ = "push_devices"
    staff_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_users.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(Text, default="android")
    token: Mapped[str] = mapped_column(Text, unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(TS)


class ReportExport(UUIDPk, Timestamps, Base):
    __tablename__ = "report_exports"
    report_type: Mapped[str] = mapped_column(Text)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    period_from: Mapped[date] = mapped_column(Date)
    period_to: Mapped[date] = mapped_column(Date)
    format: Mapped[str] = mapped_column(ReportFormat)
    file_size: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id", ondelete="SET NULL"))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id", ondelete="SET NULL"))
    actor_kind: Mapped[str] = mapped_column(Text, default="staff")
    action: Mapped[str] = mapped_column(Text)
    entity: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
