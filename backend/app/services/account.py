"""Accounts (organizations): sign-up, bank details and monthly transfer statements."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Organization, StaffUser
from app.security import hash_password
from app.services import settings_store

_BIC = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(ValueError):
    pass


def normalize_iban(value: str) -> str:
    return re.sub(r"[\s-]", "", value or "").upper()


def iban_is_valid(value: str) -> bool:
    """ISO 13616: country, check digits, then the mod-97 check over the whole number."""
    iban = normalize_iban(value)
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", iban):
        return False
    moved = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in moved)
    return int(digits) % 97 == 1


def bic_is_valid(value: str) -> bool:
    return bool(_BIC.fullmatch(re.sub(r"\s", "", value or "").upper()))


def mask_iban(iban: str | None) -> str | None:
    if not iban:
        return None
    return f"{iban[:4]} •••• •••• {iban[-4:]}"


def format_iban(iban: str | None) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4)) if iban else ""


def email_is_valid(value: str) -> bool:
    return bool(_EMAIL.fullmatch(value.strip()))


def signup(db: Session, *, organization_name: str, kind: str, full_name: str, email: str, password: str,
           phone: str | None = None, locale: str = "fr") -> StaffUser:
    """Public sign-up: a new account and its first administrator."""
    email = email.strip().lower()
    if not email_is_valid(email):
        raise AccountError("invalid_email")
    if len(password) < 12:
        raise AccountError("password_too_short")
    if db.scalar(select(StaffUser.id).where(func.lower(StaffUser.email) == email)) is not None:
        raise AccountError("email_already_used")
    org = Organization(name=organization_name.strip(), kind=kind, email=email, phone=(phone or "").strip() or None)
    db.add(org)
    db.flush()
    user = StaffUser(email=email, full_name=full_name.strip(), password_hash=hash_password(password), role="admin",
                     locale=locale, organization_id=org.id, last_login_at=datetime.now(UTC))
    db.add(user)
    db.flush()
    return user


def set_bank(db: Session, org: Organization, *, holder: str, iban: str, bic: str, address: str | None) -> None:
    if not iban_is_valid(iban):
        raise AccountError("invalid_iban")
    if not bic_is_valid(bic):
        raise AccountError("invalid_bic")
    org.bank_holder = holder.strip()
    org.bank_iban = normalize_iban(iban)
    org.bank_bic = re.sub(r"\s", "", bic).upper()
    org.bank_address = (address or "").strip() or None
    org.bank_updated_at = datetime.now(UTC)


def organization_out(org: Organization) -> dict:
    return {
        "id": org.id, "name": org.name, "kind": org.kind, "email": org.email, "phone": org.phone, "is_active": org.is_active,
        "bank_holder": org.bank_holder, "bank_iban_masked": mask_iban(org.bank_iban), "bank_bic": org.bank_bic,
        "bank_address": org.bank_address, "bank_updated_at": org.bank_updated_at,
    }


SQL_PAYOUTS = """
SELECT date_trunc('month', p.paid_at)::date AS month, s.id AS site_id, s.name AS site_name,
       count(*) AS payments, coalesce(sum(p.amount_cents), 0)::int AS gross_cents, min(p.currency) AS currency
FROM payments p JOIN sites s ON s.id = p.site_id
WHERE s.organization_id = :org AND p.status = 'succeeded' AND p.paid_at IS NOT NULL
  AND p.paid_at >= date_trunc('month', now()) - interval '11 months'
GROUP BY 1, s.id, s.name
ORDER BY 1 DESC, s.name
"""


def payouts(db: Session, org_id: uuid.UUID) -> list[dict]:
    """Monthly statement per parking of what was paid in the app and at the desk (bookings, stays, rentals),
    minus the platform's service fee (setting payouts.fee_bp). The transfer itself is made by the bank."""
    fee_bp = int(settings_store.get(db, "payouts.fee_bp") or 0)
    this_month = date.today().replace(day=1)
    out = []
    for r in db.execute(text(SQL_PAYOUTS), {"org": org_id}):
        fee = round(r.gross_cents * fee_bp / 10_000)
        out.append({
            "month": r.month, "site_id": r.site_id, "site_name": r.site_name, "payments": r.payments,
            "gross_cents": r.gross_cents, "fee_cents": fee, "net_cents": r.gross_cents - fee, "currency": r.currency or "CHF",
            "status": "in_progress" if r.month >= this_month else "to_transfer",
        })
    return out
