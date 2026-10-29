from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import allowed_site_ids, check_site_access, get_current_user, get_site_or_404, require_permission, require_role
from app.models import Bay, StaffUser, Zone
from app.schemas import BayDetailOut, BayOut, BayUpdateIn, DashboardOut, Money, SiteOut, SitePatch, ZoneOut
from app.services import queries
from app.services.audit import audit
from app.services.realtime import broadcaster

router = APIRouter(tags=["sites"])


@router.get("/sites", response_model=list[SiteOut])
def list_sites(user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    return queries.sites(db, allowed_site_ids(user))


@router.patch("/sites/{site_id}", response_model=SiteOut)
def update_site(site_id: uuid.UUID, body: SitePatch, user: StaffUser = Depends(require_permission("change_settings")),
                db: Session = Depends(get_db)) -> dict:
    site = get_site_or_404(db, site_id, user)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(site, field, value)
    audit(db, "site.update", "sites", site.id, actor_id=user.id, data=body.model_dump(mode="json", exclude_unset=True))
    db.flush()
    return next(s for s in queries.sites(db, None) if s["id"] == site.id)


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    site_id: uuid.UUID | None = Query(None), user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> DashboardOut:
    if site_id:
        check_site_access(user, site_id)
    d = queries.dashboard(db, site_id, allowed_site_ids(user))
    return DashboardOut(
        site_id=site_id,
        free_bays=d["free_bays"],
        total_bays=d["total_bays"],
        occupancy_pct=d["occupancy_pct"],
        entries_today=d["entries_today"],
        exits_today=d["exits_today"],
        revenue_today=Money(amount_cents=d["revenue_today"]),
        revenue_subscriptions_today=Money(amount_cents=d["revenue_subscriptions_today"]),
        open_alarms=d["open_alarms"],
    )


@router.get("/sites/{site_id}/zones", response_model=list[ZoneOut])
def list_zones(site_id: uuid.UUID, user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    get_site_or_404(db, site_id, user)
    return queries.zones(db, site_id)


@router.get("/zones/{zone_id}/bays", response_model=list[BayOut])
def list_bays(zone_id: uuid.UUID, user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(404, "zone_not_found")
    check_site_access(user, zone.site_id)
    return queries.bays(db, zone_id)


@router.get("/bays/{bay_id}", response_model=BayDetailOut)
def get_bay(bay_id: uuid.UUID, user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    detail = queries.bay_detail(db, bay_id)
    if detail is None:
        raise HTTPException(404, "bay_not_found")
    check_site_access(user, detail["site_id"])
    return detail


@router.patch("/bays/{bay_id}", response_model=BayDetailOut)
def update_bay(
    bay_id: uuid.UUID, body: BayUpdateIn, user: StaffUser = Depends(require_role("manager")), db: Session = Depends(get_db)
) -> dict:
    bay = db.get(Bay, bay_id)
    if bay is None:
        raise HTTPException(404, "bay_not_found")
    check_site_access(user, bay.zone.site_id)
    bay.admin_state = body.admin_state
    bay.blocked_reason = body.blocked_reason if body.admin_state == "blocked" else None
    audit(db, f"bay.{body.admin_state}", "bays", bay.id, actor_id=user.id, data={"reason": body.blocked_reason})
    db.flush()
    broadcaster.publish("bay.updated", {"bay_id": bay.id, "zone_id": bay.zone_id, "admin_state": bay.admin_state})
    return queries.bay_detail(db, bay_id)
