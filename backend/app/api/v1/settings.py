from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_permission
from app.models import NotificationRule, StaffUser
from app.schemas import NotificationRuleIn, SettingsIn, SettingsOut
from app.services import settings_store
from app.services.audit import audit

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsOut)
def get_settings_values(user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> SettingsOut:
    return SettingsOut(values=settings_store.get_all(db))


@router.put("/settings", response_model=SettingsOut)
def put_settings(body: SettingsIn, user: StaffUser = Depends(require_permission("change_settings")), db: Session = Depends(get_db)) -> SettingsOut:
    try:
        settings_store.put_many(db, body.values, user.id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, "settings.update", "settings", None, actor_id=user.id, data={"keys": sorted(body.values)})
    db.flush()
    return SettingsOut(values=settings_store.get_all(db))


@router.get("/notification-rules")
def get_rules(user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [{"event": r.event, "channel": r.channel, "enabled": r.enabled}
            for r in db.scalars(select(NotificationRule).order_by(NotificationRule.event, NotificationRule.channel))]


@router.put("/notification-rules")
def put_rules(body: list[NotificationRuleIn], user: StaffUser = Depends(require_permission("change_settings")), db: Session = Depends(get_db)) -> list[dict]:
    for item in body:
        rule = db.scalar(select(NotificationRule).where(NotificationRule.event == item.event, NotificationRule.channel == item.channel))
        if rule is None:
            db.add(NotificationRule(event=item.event, channel=item.channel, enabled=item.enabled))
        else:
            rule.enabled = item.enabled
    audit(db, "notification_rules.update", "notification_rules", None, actor_id=user.id)
    db.flush()
    return get_rules(user, db)
