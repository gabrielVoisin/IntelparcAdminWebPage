from __future__ import annotations

import calendar
import uuid
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import allowed_site_ids, check_site_access, get_current_user, require_permission
from app.models import StaffUser, Subscription, SubscriptionPlan
from app.schemas import Money, PlanOut, RenewIn, SubscriptionIn, SubscriptionOut, SubscriptionPatch
from app.services import queries
from app.services.audit import audit

router = APIRouter(tags=["subscriptions"])


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _to_out(row: dict) -> SubscriptionOut:
    return SubscriptionOut(
        id=row["id"], customer_id=row["customer_id"], customer_name=row["customer_name"], plate=row["plate"],
        plan_code=row["plan_code"], plan_name=row["plan_name"], price=Money(amount_cents=row["price_cents"], currency=row["currency"]),
        site_id=row["site_id"], bay_label=row["bay_label"], starts_on=row["starts_on"], ends_on=row["ends_on"],
        auto_renew=row["auto_renew"], status=row["status"], display_status=row["display_status"], days_left=row["days_left"],
    )


def _one(db: Session, sub_id: uuid.UUID) -> SubscriptionOut:
    db.flush()
    row = next((r for r in queries.subscriptions(db, None, tab="all") if r["id"] == sub_id), None)
    if row is None:
        raise HTTPException(404, "subscription_not_found")
    return _to_out(row)


def _get(db: Session, sub_id: uuid.UUID, user: StaffUser) -> Subscription:
    sub = db.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(404, "subscription_not_found")
    check_site_access(user, sub.site_id)
    return sub


@router.get("/subscription-plans", response_model=list[PlanOut])
def plans(user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[SubscriptionPlan]:
    return list(db.scalars(select(SubscriptionPlan).order_by(SubscriptionPlan.price_cents.desc())))


@router.get("/subscriptions", response_model=list[SubscriptionOut])
def list_subscriptions(
    tab: Literal["active", "expiring_soon", "unpaid", "suspended", "ended", "all"] = "active",
    q: str | None = Query(None, description="Name or plate"),
    user: StaffUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SubscriptionOut]:
    return [_to_out(r) for r in queries.subscriptions(db, allowed_site_ids(user), tab, q)]


@router.get("/subscriptions/stats")
def stats(user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return queries.subscription_stats(db, allowed_site_ids(user))


@router.post("/subscriptions", response_model=SubscriptionOut, status_code=201)
def create(body: SubscriptionIn, user: StaffUser = Depends(require_permission("manage_bookings_subscriptions")), db: Session = Depends(get_db)) -> SubscriptionOut:
    check_site_access(user, body.site_id)
    plan = db.get(SubscriptionPlan, body.plan_id)
    if plan is None:
        raise HTTPException(404, "plan_not_found")
    sub = Subscription(
        **body.model_dump(), price_cents=plan.price_cents, currency=plan.currency,
        ends_on=add_months(body.starts_on, plan.period_months),
    )
    db.add(sub)
    db.flush()
    audit(db, "subscription.create", "subscriptions", sub.id, actor_id=user.id)
    return _one(db, sub.id)


@router.patch("/subscriptions/{sub_id}", response_model=SubscriptionOut)
def update(sub_id: uuid.UUID, body: SubscriptionPatch, user: StaffUser = Depends(require_permission("manage_bookings_subscriptions")),
           db: Session = Depends(get_db)) -> SubscriptionOut:
    sub = _get(db, sub_id, user)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(sub, field, value)
    audit(db, "subscription.update", "subscriptions", sub.id, actor_id=user.id, data=body.model_dump(mode="json", exclude_unset=True))
    return _one(db, sub.id)


@router.post("/subscriptions/{sub_id}/renew", response_model=SubscriptionOut)
def renew(sub_id: uuid.UUID, body: RenewIn, user: StaffUser = Depends(require_permission("manage_bookings_subscriptions")), db: Session = Depends(get_db)) -> SubscriptionOut:
    sub = _get(db, sub_id, user)
    if sub.status == "terminated":
        raise HTTPException(409, "subscription_terminated")
    sub.ends_on = add_months(max(sub.ends_on, date.today()), body.months)
    sub.status, sub.payment_overdue = "active", False
    audit(db, "subscription.renew", "subscriptions", sub.id, actor_id=user.id, data={"months": body.months})
    return _one(db, sub.id)


@router.post("/subscriptions/{sub_id}/{action}", response_model=SubscriptionOut)
def change_status(
    sub_id: uuid.UUID, action: Literal["suspend", "reactivate", "terminate"],
    user: StaffUser = Depends(require_permission("manage_bookings_subscriptions")), db: Session = Depends(get_db),
) -> SubscriptionOut:
    sub = _get(db, sub_id, user)
    if sub.status == "terminated" and action != "reactivate":
        raise HTTPException(409, "subscription_terminated")
    if action == "suspend":
        sub.status = "suspended"
    elif action == "reactivate":
        sub.status, sub.terminated_at = "active", None
    else:
        sub.status, sub.terminated_at, sub.auto_renew = "terminated", datetime.now(UTC), False
    audit(db, f"subscription.{action}", "subscriptions", sub.id, actor_id=user.id)
    return _one(db, sub.id)
