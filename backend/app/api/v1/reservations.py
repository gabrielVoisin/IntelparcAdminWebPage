from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import allowed_site_ids, check_site_access, get_current_user, require_permission, require_role
from app.models import Bay, Customer, Reservation, StaffUser
from app.schemas import ExtendIn, Money, ReservationIn, ReservationOut, ReservationPatch
from app.services import queries, settings_store
from app.services.audit import audit
from app.services.pricing import price_for_stay

router = APIRouter(prefix="/reservations", tags=["reservations"])


def _to_out(row: dict) -> ReservationOut:
    kind = "subscriber" if row["is_subscriber"] else ("business" if row["company_name"] else f"booking_{row['channel']}")
    return ReservationOut(
        id=row["id"], customer_id=row["customer_id"], customer_name=row["customer_name"], customer_kind=kind,
        plate=row["plate"], bay_id=row["bay_id"], bay_label=row["bay_label"], starts_at=row["starts_at"],
        ends_at=row["ends_at"], status=row["status"], display_status=row["display_status"], channel=row["channel"],
        price=Money(amount_cents=row["price_cents"], currency=row["currency"]),
    )


def _one(db: Session, reservation_id: uuid.UUID) -> ReservationOut:
    db.flush()
    row = next((r for r in queries.reservations(db, None, tab="all") if r["id"] == reservation_id), None)
    if row is None:
        raise HTTPException(404, "reservation_not_found")
    return _to_out(row)


def _price(db: Session, start: datetime, end: datetime) -> int:
    tariffs = queries.tariffs(db)["current"]
    values = settings_store.get_all(db)
    return price_for_stay(
        start, end, tariffs["hourly"]["amount_cents"], tariffs["daily"]["amount_cents"],
        str(values["payments.billing_mode"]), 0,
    )


@router.get("", response_model=list[ReservationOut])
def list_reservations(
    tab: Literal["ongoing", "upcoming", "completed", "cancelled", "all"] = "ongoing",
    q: str | None = Query(None, description="Name or plate"),
    user: StaffUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReservationOut]:
    return [_to_out(r) for r in queries.reservations(db, allowed_site_ids(user), tab, q)]


@router.get("/stats")
def stats(user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return queries.reservation_stats(db, allowed_site_ids(user))


@router.post("", response_model=ReservationOut, status_code=201)
def create(body: ReservationIn, user: StaffUser = Depends(require_permission("manage_bookings_subscriptions")), db: Session = Depends(get_db)) -> ReservationOut:
    if body.ends_at <= body.starts_at:
        raise HTTPException(422, "ends_before_start")
    bay = db.get(Bay, body.bay_id)
    if bay is None or db.get(Customer, body.customer_id) is None:
        raise HTTPException(404, "bay_or_customer_not_found")
    check_site_access(user, bay.zone.site_id)
    if bay.admin_state == "blocked":
        raise HTTPException(409, "bay_blocked")
    res = Reservation(**body.model_dump(), price_cents=_price(db, body.starts_at, body.ends_at))
    db.add(res)
    try:
        db.flush()
    except IntegrityError as exc:  # reservations_no_overlap exclusion constraint
        raise HTTPException(409, "bay_already_booked") from exc
    audit(db, "reservation.create", "reservations", res.id, actor_id=user.id)
    return _one(db, res.id)


def _get(db: Session, reservation_id: uuid.UUID, user: StaffUser) -> Reservation:
    res = db.get(Reservation, reservation_id)
    if res is None:
        raise HTTPException(404, "reservation_not_found")
    check_site_access(user, res.bay.zone.site_id)
    return res


@router.patch("/{reservation_id}", response_model=ReservationOut)
def update(reservation_id: uuid.UUID, body: ReservationPatch, user: StaffUser = Depends(require_permission("manage_bookings_subscriptions")),
           db: Session = Depends(get_db)) -> ReservationOut:
    res = _get(db, reservation_id, user)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(res, field, value)
    if res.ends_at <= res.starts_at:
        raise HTTPException(422, "ends_before_start")
    res.price_cents = _price(db, res.starts_at, res.ends_at)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "bay_already_booked") from exc
    audit(db, "reservation.update", "reservations", res.id, actor_id=user.id, data=body.model_dump(mode="json", exclude_unset=True))
    return _one(db, res.id)


@router.post("/{reservation_id}/extend", response_model=ReservationOut)
def extend(reservation_id: uuid.UUID, body: ExtendIn, user: StaffUser = Depends(require_permission("manage_bookings_subscriptions")),
           db: Session = Depends(get_db)) -> ReservationOut:
    res = _get(db, reservation_id, user)
    if res.status not in ("confirmed", "active"):
        raise HTTPException(409, "reservation_closed")
    res.ends_at = max(res.ends_at, datetime.now(UTC)) + timedelta(minutes=body.minutes)
    res.price_cents = _price(db, res.starts_at, res.ends_at)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "bay_already_booked") from exc
    audit(db, "reservation.extend", "reservations", res.id, actor_id=user.id, data={"minutes": body.minutes})
    return _one(db, res.id)


@router.post("/{reservation_id}/complete", response_model=ReservationOut)
def complete(reservation_id: uuid.UUID, user: StaffUser = Depends(require_role("agent")), db: Session = Depends(get_db)) -> ReservationOut:
    res = _get(db, reservation_id, user)
    res.status = "completed"
    audit(db, "reservation.complete", "reservations", res.id, actor_id=user.id)
    return _one(db, res.id)


@router.post("/{reservation_id}/cancel", response_model=ReservationOut)
def cancel(reservation_id: uuid.UUID, user: StaffUser = Depends(require_role("agent")), db: Session = Depends(get_db)) -> ReservationOut:
    res = _get(db, reservation_id, user)
    if res.status in ("completed", "cancelled"):
        raise HTTPException(409, "reservation_closed")
    res.status, res.cancelled_at = "cancelled", datetime.now(UTC)
    audit(db, "reservation.cancel", "reservations", res.id, actor_id=user.id)
    return _one(db, res.id)
