"""Key/value settings stored in the "settings" table (JSONB values)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting

DEFAULTS: dict[str, object] = {
    "general.site_name": "[NOM DU PARKING]",
    "general.open_24h": True,
    "general.opens_at": "06:00",
    "general.closes_at": "23:00",
    "general.currency": "EUR",
    "general.timezone": "Europe/Paris",
    "control.grace_alarm_min": 15,
    "control.grace_exit_min": 15,
    "control.overstay_min": 10,
    "control.fine_cents": 3500,
    "control.occupancy_alert_pct": 90,
    "control.anpr_enabled": True,
    "control.auto_open_subscribers": True,
    "control.accessible_bay_check": True,
    "control.auto_fine": False,
    "payments.billing_mode": "started_hour",
    "payments.free_minutes": 15,
    "payments.vat_bp": 2000,
    "payments.lost_ticket_cents": 2500,
    "payments.methods": ["card", "contactless", "app", "invoice"],
    "security.session_minutes": 30,
    "security.require_2fa": True,
    "gdpr.plate_retention_days": 30,
    "gdpr.video_retention_days": 7,
    "gdpr.anonymise_exports": True,
    "device.heartbeat_interval_s": 30,
    "device.upload_interval_s": 5,
    # Driver app and Bluetooth identification (schema v2)
    "payments.provider": "demo",          # demo = payments accepted without a payment provider (prototype only)
    "payments.max_stay_min": 0,           # 0 = no limit; otherwise longest stay the app may sell at once
    "ble.enabled": True,
    "ble.token_hours": 24,                # validity of the identity token carried by the phone
    "ble.match_window_min": 10,           # an identification and a car detection this close belong together
}


def get_all(db: Session, site_id: uuid.UUID | None = None) -> dict[str, object]:
    values = dict(DEFAULTS)
    rows = db.scalars(select(Setting).where(Setting.site_id.is_(None))).all()
    values.update({r.key: r.value for r in rows})
    if site_id is not None:
        rows = db.scalars(select(Setting).where(Setting.site_id == site_id)).all()
        values.update({r.key: r.value for r in rows})
    return values


def get(db: Session, key: str, site_id: uuid.UUID | None = None) -> object:
    return get_all(db, site_id).get(key, DEFAULTS.get(key))


def put_many(db: Session, values: dict[str, object], user_id: uuid.UUID | None, site_id: uuid.UUID | None = None) -> None:
    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown setting keys: {sorted(unknown)}")
    for key, value in values.items():
        row = db.scalar(
            select(Setting).where(Setting.key == key, Setting.site_id.is_(None) if site_id is None else Setting.site_id == site_id)
        )
        if row is None:
            db.add(Setting(site_id=site_id, key=key, value=value, updated_by=user_id))
        else:
            row.value = value
            row.updated_by = user_id
