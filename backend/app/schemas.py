"""Pydantic models: the JSON contract shared by the website, the Android app and the Pi agent.

Rules: snake_case fields, UUID strings, ISO 8601 UTC datetimes, money as integer cents
plus ISO 4217 currency, enum values in English lower case.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")

Role = Literal["admin", "manager", "agent", "viewer"]
Locale = Literal["fr", "en", "de"]
BayStatus = Literal["free", "occupied", "alarm", "reserved", "blocked"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class Money(BaseModel):
    amount_cents: int
    currency: str = "EUR"


# --- Auth --------------------------------------------------------------------
class LoginIn(BaseModel):
    email: str
    password: str
    client_name: str | None = Field(None, description="Shown in the session list, e.g. 'Android · Pixel 8'")


class RefreshIn(BaseModel):
    refresh_token: str


class StaffOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    locale: Locale
    is_active: bool
    last_login_at: datetime | None = None


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: StaffOut


class PushTokenIn(BaseModel):
    token: str
    platform: Literal["android", "ios", "web"] = "android"


# --- Sites / bays ---------------------------------------------------------------
class SiteOut(ORM):
    id: uuid.UUID
    code: str
    name: str
    address_line: str | None
    postcode: str | None
    city: str | None
    country: str
    timezone: str
    latitude: float | None
    longitude: float | None
    is_active: bool
    total_bays: int = 0
    free_bays: int = 0
    occupied_bays: int = 0


class SitePatch(BaseModel):
    name: str | None = None
    address_line: str | None = None
    postcode: str | None = None
    city: str | None = None
    is_active: bool | None = None


class ZoneOut(ORM):
    id: uuid.UUID
    site_id: uuid.UUID
    code: str
    name: str
    level: int
    sort_order: int


class BayOut(BaseModel):
    id: uuid.UUID
    zone_id: uuid.UUID
    number: str
    kind: Literal["standard", "accessible", "ev"]
    status: BayStatus
    is_occupied: bool
    occupied_since: datetime | None
    plan_row: int | None
    plan_col: int | None
    photo_x: float | None
    photo_y: float | None
    plate: str | None = None


class BayDetailOut(BayOut):
    zone_name: str
    site_id: uuid.UUID
    admin_state: Literal["open", "blocked"]
    blocked_reason: str | None
    session: dict | None = None
    alarm: dict | None = None
    reservation: dict | None = None
    history: list[dict] = []


class BayUpdateIn(BaseModel):
    admin_state: Literal["open", "blocked"]
    blocked_reason: str | None = None


class DashboardOut(BaseModel):
    site_id: uuid.UUID | None
    free_bays: int
    total_bays: int
    occupancy_pct: int
    entries_today: int
    exits_today: int
    revenue_today: Money
    revenue_subscriptions_today: Money
    open_alarms: int


# --- Payment alarms ---------------------------------------------------------
class PaymentAlarmOut(BaseModel):
    id: uuid.UUID
    site_id: uuid.UUID
    bay_id: uuid.UUID | None
    bay_label: str | None
    plate: str | None
    reason: Literal[
        "no_payment", "overstay", "plate_not_recognised", "lost_ticket", "accessible_bay_misuse", "reserved_bay_misuse"
    ]
    detail: str | None
    detected_at: datetime
    status: Literal["open", "acknowledged", "fined"]
    handled_at: datetime | None
    # schema v2: who was recognised on the bay and how (bluetooth, app, camera), when known
    identified_by: Literal["bluetooth", "camera", "app", "manual"] | None = None
    customer_name: str | None = None
    evidence_count: int = 0


class FineIn(BaseModel):
    amount_cents: int | None = Field(None, ge=0, description="Defaults to the 'control.fine_cents' setting")


# --- Customers / vehicles -----------------------------------------------------
class VehicleIn(BaseModel):
    plate_display: str = Field(min_length=2, max_length=20)
    country: str | None = Field(None, min_length=2, max_length=2)
    make: str | None = None
    model: str | None = None
    color: str | None = None


class VehicleOut(ORM):
    id: uuid.UUID
    plate_display: str
    plate_norm: str
    country: str | None
    make: str | None
    model: str | None
    color: str | None


def normalize_phone(v: str | None) -> str | None:
    """Keep digits and the leading +, require E.164 (e.g. +33600000412)."""
    if v is None or v == "":
        return None
    cleaned = "".join(ch for ch in v if ch.isdigit() or ch == "+")
    if not cleaned.startswith("+") or not 8 <= len(cleaned) <= 16:
        raise ValueError("phone must be in E.164 format, e.g. +33600000412")
    return cleaned


class CustomerIn(BaseModel):
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    email: str | None = None
    phone: str | None = Field(None, description="E.164, e.g. +33600000412")
    address_line: str | None = None
    postcode: str | None = None
    city: str | None = None
    country: str = Field("FR", min_length=2, max_length=2)
    locale: Locale = "fr"
    company_name: str | None = None

    @field_validator("phone")
    @classmethod
    def phone_e164(cls, v: str | None) -> str | None:
        return normalize_phone(v)


class CustomerPatch(BaseModel):
    @field_validator("phone")
    @classmethod
    def phone_e164(cls, v: str | None) -> str | None:
        return normalize_phone(v)

    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address_line: str | None = None
    postcode: str | None = None
    city: str | None = None
    country: str | None = None
    locale: Locale | None = None
    company_name: str | None = None


class CustomerOut(ORM):
    id: uuid.UUID
    reference: str
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
    address_line: str | None
    postcode: str | None
    city: str | None
    country: str
    locale: str
    company_name: str | None
    created_at: datetime
    vehicles: list[VehicleOut] = []


class CustomerProfileOut(CustomerOut):
    location: dict | None
    subscription: dict | None
    reservation: dict | None
    payment_state: Literal["up_to_date", "unpaid", "overdue", "surcharge_due"]
    activity: list[dict]


# --- Reservations ---------------------------------------------------------------
class ReservationIn(BaseModel):
    customer_id: uuid.UUID
    vehicle_id: uuid.UUID | None = None
    bay_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    channel: Literal["web", "app", "phone", "desk"] = "web"


class ReservationPatch(BaseModel):
    bay_id: uuid.UUID | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class ExtendIn(BaseModel):
    minutes: int = Field(60, gt=0, le=24 * 60)


class ReservationOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    customer_kind: str
    plate: str | None
    bay_id: uuid.UUID
    bay_label: str
    starts_at: datetime
    ends_at: datetime
    status: Literal["confirmed", "active", "completed", "cancelled", "no_show"]
    display_status: Literal["ongoing", "upcoming", "completed", "overdue", "cancelled"]
    channel: str
    price: Money


# --- Subscriptions ------------------------------------------------------------
class PlanOut(ORM):
    id: uuid.UUID
    code: str
    name: dict
    price_cents: int
    currency: str
    period_months: int
    access_hours: str | None


class SubscriptionIn(BaseModel):
    customer_id: uuid.UUID
    vehicle_id: uuid.UUID | None = None
    site_id: uuid.UUID
    bay_id: uuid.UUID | None = None
    plan_id: uuid.UUID
    starts_on: date
    auto_renew: bool = True


class SubscriptionPatch(BaseModel):
    bay_id: uuid.UUID | None = None
    plan_id: uuid.UUID | None = None
    vehicle_id: uuid.UUID | None = None
    auto_renew: bool | None = None


class RenewIn(BaseModel):
    months: int = Field(1, ge=1, le=24)


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    plate: str | None
    plan_code: str
    plan_name: dict
    price: Money
    site_id: uuid.UUID
    bay_label: str | None
    starts_on: date
    ends_on: date
    auto_renew: bool
    status: Literal["active", "suspended", "terminated", "expired"]
    display_status: Literal["active", "expiring_soon", "unpaid", "suspended", "ended"]
    days_left: int


# --- Tariffs ----------------------------------------------------------------------
class TariffOut(ORM):
    id: uuid.UUID
    site_id: uuid.UUID | None
    kind: Literal["hourly", "daily", "monthly"]
    amount_cents: int
    currency: str
    valid_from: datetime
    valid_to: datetime | None


class TariffIn(BaseModel):
    amount_cents: int = Field(ge=0, le=10_000_000)
    site_id: uuid.UUID | None = None


class TariffsOut(BaseModel):
    current: dict[str, TariffOut]
    history: list[TariffOut]


# --- Reports, settings, team ---------------------------------------------------
ReportType = Literal[
    "revenue", "transactions", "occupancy", "entries_exits", "parking_duration",
    "peak_hours", "subscriptions", "payments", "anomalies", "equipment",
]


class ReportExportOut(ORM):
    id: uuid.UUID
    report_type: str
    site_id: uuid.UUID | None
    period_from: date
    period_to: date
    format: Literal["pdf", "xlsx"]
    file_size: int | None
    created_at: datetime


class SettingsOut(BaseModel):
    values: dict[str, object]


class SettingsIn(BaseModel):
    values: dict[str, object]


class NotificationRuleIn(BaseModel):
    event: str
    channel: Literal["email", "sms", "app"]
    enabled: bool


class InvitationIn(BaseModel):
    full_name: str = Field(min_length=1)
    email: str
    role: Role = "agent"
    site_ids: list[uuid.UUID] = []


class TeamMemberPatch(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    site_ids: list[uuid.UUID] | None = None
    locale: Locale | None = None


class TeamMemberOut(StaffOut):
    site_codes: list[str]
    pending_invitation: bool


# --- Control cabinets (staff side) ---------------------------------------------
class DeviceEventOut(BaseModel):
    id: uuid.UUID
    code: str
    priority: Literal["alarm", "warning", "info"]
    message_key: str
    message: str
    raised_at: datetime
    cleared_at: datetime | None
    acknowledged_at: datetime | None
    state: Literal["active_unacknowledged", "active_acknowledged", "cleared"]


class ControllerOut(BaseModel):
    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    code: str
    name: str
    online: bool
    last_seen_at: datetime | None
    agent_version: str | None
    active_alarms: int
    active_warnings: int
    events: list[DeviceEventOut]


class ControllerCreateIn(BaseModel):
    site_id: uuid.UUID
    code: str
    name: str
    hardware: str | None = "Raspberry Pi 5"


class ControllerCreatedOut(BaseModel):
    id: uuid.UUID
    code: str
    api_key: str = Field(description="Shown only once: copy it into /etc/parking-agent/config.yaml on the Pi")


# --- Device API (Raspberry Pi agent) --------------------------------------------
class DeviceConfigItem(BaseModel):
    code: str
    kind: str
    address: str | None
    bay_id: uuid.UUID | None
    bay_number: str | None
    zone_code: str | None


class BleConfig(BaseModel):
    """What the cabinet needs to identify phones over Bluetooth (docs/BLUETOOTH.md)."""

    enabled: bool
    public_key: str = Field(description="Server Ed25519 public key (base64, 32 bytes): verifies the phones' tokens offline")
    site_name: str


class DeviceConfigOut(BaseModel):
    controller_id: uuid.UUID
    controller_code: str
    site_code: str
    heartbeat_interval_s: int
    upload_interval_s: int
    devices: list[DeviceConfigItem]
    server_time: datetime
    ble: BleConfig | None = None


class HeartbeatIn(BaseModel):
    agent_version: str
    uptime_s: int = 0
    cpu_temp_c: float | None = None
    devices: list[dict] = Field(default_factory=list, description="[{code, status: ok|fault|offline}]")


class WatchdogIn(BaseModel):
    source: Literal["agent", "simulator"] = Field(description="Program of the cabinet sending the signal")
    seq: int = Field(0, ge=0, description="Signal counter of the sender (shows lost signals)")


class ReadingIn(BaseModel):
    uid: str = Field(min_length=1, max_length=80, description="Unique id generated on the Pi (idempotency)")
    device_code: str
    is_occupied: bool
    plate: str | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    measured_at: datetime


class ReadingsIn(BaseModel):
    readings: list[ReadingIn] = Field(max_length=500)


class PassageIn(BaseModel):
    """Entry or exit read by a barrier camera (ANPR)."""

    uid: str
    device_code: str
    direction: Literal["in", "out"]
    plate: str | None
    confidence: float | None = None
    at: datetime


class PassagesIn(BaseModel):
    passages: list[PassageIn] = Field(max_length=200)


class EventIn(BaseModel):
    uid: str
    code: str
    priority: Literal["alarm", "warning", "info"]
    message_key: str
    raised_at: datetime
    cleared_at: datetime | None = None


class EventsIn(BaseModel):
    events: list[EventIn] = Field(max_length=200)


class IdentificationIn(BaseModel):
    """A phone verified by the cabinet over Bluetooth (token signature + challenge checked on the Pi)."""

    uid: str = Field(min_length=1, max_length=80, description="Unique id generated on the Pi (idempotency)")
    customer_id: uuid.UUID
    vehicle_id: uuid.UUID | None = None
    bay_id: uuid.UUID | None = Field(None, description="Bay given by the phone (QR code scanned, booking...), if any")
    rssi: int | None = Field(None, ge=-127, le=20)
    identified_at: datetime


class IdentificationsIn(BaseModel):
    identifications: list[IdentificationIn] = Field(max_length=200)


class IdentificationDecision(BaseModel):
    uid: str
    status: Literal["authorised", "payment_required", "reserved_by_other", "wrong_bay", "pending"]
    bay_id: uuid.UUID | None
    bay_label: str | None
    session_id: uuid.UUID | None


class IngestResult(BaseModel):
    accepted: int
    duplicates: int
    rejected: list[dict] = []
