"""JSON contract of the driver app (Intel Park, Android): /api/v1/app/*.

Same conventions as app/schemas.py: snake_case, UUID strings, ISO 8601 UTC datetimes,
money as integer cents + ISO 4217 currency, error bodies {"detail": "<code>"}.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas import Locale, Money, VehicleIn, VehicleOut, normalize_phone


# --- Account -----------------------------------------------------------------------
class RegisterIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    locale: Locale = "fr"
    client_name: str | None = Field(None, max_length=80, description="e.g. 'Android · Pixel 8'")

    @field_validator("email")
    @classmethod
    def email_shape(cls, v: str) -> str:
        v = v.strip().lower()
        local, at, domain = v.partition("@")
        if not at or not local or "." not in domain:
            raise ValueError("invalid email")
        return v


class AppLoginIn(BaseModel):
    email: str
    password: str
    client_name: str | None = None


class DriverOut(BaseModel):
    id: uuid.UUID
    reference: str
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
    phone_verified: bool
    address_line: str | None
    postcode: str | None
    city: str | None
    country: str
    locale: str
    vehicles: list[VehicleOut]
    profile_complete: bool = Field(description="Vehicle, address and verified phone: the 4 sign-up steps are done")


class DriverTokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    driver: DriverOut


class DriverPatch(BaseModel):
    first_name: str | None = Field(None, min_length=1, max_length=80)
    last_name: str | None = Field(None, min_length=1, max_length=80)
    address_line: str | None = Field(None, max_length=200)
    postcode: str | None = Field(None, max_length=20)
    city: str | None = Field(None, max_length=100)
    country: str | None = Field(None, min_length=2, max_length=2)
    locale: Locale | None = None


class PhoneIn(BaseModel):
    phone: str = Field(description="E.164, e.g. +41791234567")

    @field_validator("phone")
    @classmethod
    def phone_e164(cls, v: str) -> str:
        cleaned = normalize_phone(v)
        if cleaned is None:
            raise ValueError("phone required")
        return cleaned


class PhoneCodeOut(BaseModel):
    sent_to: str
    expires_in: int
    debug_code: str | None = Field(None, description="Only when SMS_DEBUG is on (prototype without SMS gateway)")


class PhoneVerifyIn(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class AppVehicleIn(VehicleIn):
    pass


# --- Sites, bays, prices --------------------------------------------------------------
class AppSiteOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    address_line: str | None
    city: str | None
    latitude: float | None
    longitude: float | None
    free_bays: int
    total_bays: int
    hourly: Money | None
    distance_m: int | None = None


class AppBayOut(BaseModel):
    id: uuid.UUID
    code: str = Field(description="Printed under the QR code: '<site>/<zone>/<number>', e.g. P1/EXT/05")
    site_id: uuid.UUID
    site_code: str
    site_name: str
    zone_code: str
    zone_name: str
    number: str
    kind: Literal["standard", "accessible", "ev"]
    status: Literal["free", "occupied", "alarm", "reserved", "blocked"]
    hourly: Money
    daily: Money
    billing_mode: str
    free_minutes: int
    max_stay_min: int = Field(description="0 = no limit")
    latitude: float | None
    longitude: float | None


class NearestOut(BaseModel):
    site: AppSiteOut
    bay: AppBayOut | None


class QuoteIn(BaseModel):
    bay_id: uuid.UUID
    minutes: int = Field(gt=0, le=7 * 24 * 60)


class QuoteOut(BaseModel):
    bay_id: uuid.UUID
    minutes: int
    starts_at: datetime
    ends_at: datetime
    price: Money


# --- Parking sessions (pay a bay found with its QR code) -------------------------------
class SessionIn(BaseModel):
    bay_id: uuid.UUID
    vehicle_id: uuid.UUID | None = None
    minutes: int = Field(gt=0, le=7 * 24 * 60)
    payment_method: Literal["card", "app"] = "card"


class ExtendSessionIn(BaseModel):
    minutes: int = Field(gt=0, le=7 * 24 * 60)


class AppSessionOut(BaseModel):
    id: uuid.UUID
    bay_id: uuid.UUID | None
    bay_label: str | None
    site_name: str
    plate: str | None
    entered_at: datetime
    paid_until: datetime | None
    status: Literal["open", "closed"]
    identified_by: Literal["bluetooth", "camera", "app", "manual"] | None
    right: Literal["subscription", "reservation", "paid_session"] | None = None
    last_payment: Money | None = None


# --- Reservations and subscriptions ----------------------------------------------------
class AppReservationIn(BaseModel):
    site_id: uuid.UUID
    bay_id: uuid.UUID | None = Field(None, description="Empty: the server picks a free bay")
    vehicle_id: uuid.UUID | None = None
    starts_at: datetime
    ends_at: datetime


class AppReservationOut(BaseModel):
    id: uuid.UUID
    bay_id: uuid.UUID
    bay_label: str
    site_name: str
    starts_at: datetime
    ends_at: datetime
    status: Literal["confirmed", "active", "completed", "cancelled", "no_show"]
    price: Money
    free_cancellation_until: datetime


class AppPlanOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    price: Money
    period_months: int
    access_hours: str | None


class AppSubscriptionIn(BaseModel):
    site_id: uuid.UUID
    plan_id: uuid.UUID
    vehicle_id: uuid.UUID | None = None
    starts_on: date


class AppSubscriptionOut(BaseModel):
    id: uuid.UUID
    site_name: str
    plan_name: str
    price: Money
    period_months: int
    starts_on: date
    ends_on: date
    auto_renew: bool
    status: Literal["active", "suspended", "terminated", "expired"]
    bay_label: str | None


# --- Home screen ---------------------------------------------------------------------
class HomeOut(BaseModel):
    driver: DriverOut
    current_session: AppSessionOut | None
    next_reservation: AppReservationOut | None
    subscriptions: list[AppSubscriptionOut]


# --- Bluetooth identity ----------------------------------------------------------------
class BleTokenIn(BaseModel):
    public_key: str = Field(description="Phone EC P-256 public key: X.509 SubjectPublicKeyInfo, base64 (Android KeyPair.public.encoded)")
    device_name: str | None = Field(None, max_length=80)


class BleTokenOut(BaseModel):
    token: str
    expires_at: datetime
    server_public_key: str = Field(description="Ed25519 key that signed the token (base64), for information")
