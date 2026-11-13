from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import check_site_access, get_current_user, require_permission
from app.models import StaffUser, Tariff
from app.schemas import TariffIn, TariffOut, TariffsOut
from app.services import queries
from app.services.audit import audit

router = APIRouter(prefix="/tariffs", tags=["tariffs"])


@router.get("", response_model=TariffsOut)
def get_tariffs(site_id: uuid.UUID | None = Query(None, description="A parking: its prices, else the default ones"),
                user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if site_id is None:
        return queries.tariffs(db)
    check_site_access(user, site_id)
    return queries.effective_tariffs(db, site_id)


@router.put("/{kind}", response_model=TariffOut)
def set_tariff(kind: Literal["hourly", "daily", "monthly"], body: TariffIn,
               user: StaffUser = Depends(require_permission("change_prices")), db: Session = Depends(get_db)) -> Tariff:
    """Close the current price and open a new one (the table keeps the full history).

    With site_id: the price of that parking. Without: the platform's default price (super user only)."""
    if body.site_id is None and not user.is_superuser:
        raise HTTPException(403, "superuser_only")
    if body.site_id is not None:
        check_site_access(user, body.site_id)
    now = datetime.now(UTC)
    current = db.scalar(
        select(Tariff).where(Tariff.kind == kind, Tariff.valid_to.is_(None), Tariff.site_id.is_(None) if body.site_id is None
                             else Tariff.site_id == body.site_id)
    )
    if current is not None:
        if current.amount_cents == body.amount_cents:
            return current
        current.valid_to = now
        db.flush()
    values = (queries.effective_tariffs(db, body.site_id) if body.site_id else queries.tariffs(db))["current"]
    hourly = body.amount_cents if kind == "hourly" else values.get("hourly", {}).get("amount_cents", 0)
    daily = body.amount_cents if kind == "daily" else values.get("daily", {}).get("amount_cents", 0)
    if hourly and daily and daily <= hourly:
        raise HTTPException(422, "daily_pass_cheaper_than_one_hour")
    tariff = Tariff(site_id=body.site_id, kind=kind, amount_cents=body.amount_cents, valid_from=now, created_by=user.id)
    db.add(tariff)
    db.flush()
    audit(db, "tariff.update", "tariffs", tariff.id, actor_id=user.id,
          data={"kind": kind, "old": current.amount_cents if current else None, "new": body.amount_cents})
    db.refresh(tariff)
    return tariff
