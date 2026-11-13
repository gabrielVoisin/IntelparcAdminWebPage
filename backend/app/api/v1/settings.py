from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import check_site_access, get_current_user, require_permission, require_superuser
from app.models import NotificationRule, StaffUser
from app.schemas import NotificationRuleIn, SettingsIn, SettingsOut
from app.services import settings_store
from app.services.audit import audit

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsOut)
def get_settings_values(site_id: uuid.UUID | None = Query(None, description="A parking: its own values over the defaults"),
                        user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> SettingsOut:
    if site_id is not None:
        check_site_access(user, site_id)
    return SettingsOut(values=settings_store.get_all(db, site_id))


@router.put("/settings", response_model=SettingsOut)
def put_settings(body: SettingsIn, site_id: uuid.UUID | None = Query(None, description="The parking to configure"),
                 user: StaffUser = Depends(require_permission("change_settings")), db: Session = Depends(get_db)) -> SettingsOut:
    """Settings of one parking (?site_id=). Without a parking: the platform defaults, super user only."""
    if site_id is None and not user.is_superuser:
        raise HTTPException(403, "superuser_only")
    if site_id is not None:
        check_site_access(user, site_id)
        if not user.is_superuser and set(body.values) & settings_store.PLATFORM_KEYS:
            raise HTTPException(403, "superuser_only")
    try:
        settings_store.put_many(db, body.values, user.id, site_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, "settings.update", "settings", site_id, actor_id=user.id, data={"keys": sorted(body.values)})
    db.flush()
    return SettingsOut(values=settings_store.get_all(db, site_id))


@router.get("/notification-rules")
def get_rules(user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [{"event": r.event, "channel": r.channel, "enabled": r.enabled}
            for r in db.scalars(select(NotificationRule).order_by(NotificationRule.event, NotificationRule.channel))]


@router.put("/notification-rules")
def put_rules(body: list[NotificationRuleIn], user: StaffUser = Depends(require_superuser), db: Session = Depends(get_db)) -> list[dict]:
    for item in body:
        rule = db.scalar(select(NotificationRule).where(NotificationRule.event == item.event, NotificationRule.channel == item.channel))
        if rule is None:
            db.add(NotificationRule(event=item.event, channel=item.channel, enabled=item.enabled))
        else:
            rule.enabled = item.enabled
    audit(db, "notification_rules.update", "notification_rules", None, actor_id=user.id)
    db.flush()
    return get_rules(user, db)
