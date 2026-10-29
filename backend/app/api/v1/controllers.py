"""Control cabinets as seen by staff (website "Control devices" screen and Android app)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import allowed_site_ids, check_site_access, get_current_user, require_role
from app.i18n import negotiate_locale, translate
from app.models import Controller, Device, DeviceEvent, StaffUser
from app.schemas import ControllerCreatedOut, ControllerCreateIn, ControllerOut
from app.security import new_controller_key
from app.services import queries
from app.services.audit import audit
from app.services.realtime import broadcaster

router = APIRouter(prefix="/controllers", tags=["control cabinets"])


def _localise(items: list[dict], locale: str) -> list[dict]:
    for c in items:
        for e in c["events"]:
            e["message"] = translate(e["message_key"], locale)
    return items


@router.get("", response_model=list[ControllerOut])
def list_controllers(request: Request, user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    return _localise(queries.controllers(db, allowed_site_ids(user)), negotiate_locale(request, user.locale))


@router.post("/{controller_id}/acknowledge", response_model=ControllerOut)
def acknowledge_all(controller_id: uuid.UUID, request: Request, user: StaffUser = Depends(require_role("agent")),
                    db: Session = Depends(get_db)) -> dict:
    """Acknowledge every active alarm and warning of one cabinet (single button on the screen)."""
    controller = db.get(Controller, controller_id)
    if controller is None:
        raise HTTPException(404, "controller_not_found")
    check_site_access(user, controller.site_id)
    result = db.execute(
        update(DeviceEvent)
        .where(DeviceEvent.controller_id == controller.id, DeviceEvent.cleared_at.is_(None), DeviceEvent.acknowledged_at.is_(None))
        .values(acknowledged_at=datetime.now(UTC), acknowledged_by=user.id)
    )
    audit(db, "controller.acknowledge", "controllers", controller.id, actor_id=user.id, data={"events": result.rowcount})
    broadcaster.publish("controller.acknowledged", {"controller_id": controller.id})
    db.flush()
    items = _localise(queries.controllers(db, {controller.site_id}), negotiate_locale(request, user.locale))
    return next(c for c in items if c["id"] == controller.id)


@router.get("/{controller_id}/devices")
def list_devices(controller_id: uuid.UUID, user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    controller = db.get(Controller, controller_id)
    if controller is None:
        raise HTTPException(404, "controller_not_found")
    check_site_access(user, controller.site_id)
    devices = db.scalars(select(Device).where(Device.controller_id == controller_id).order_by(Device.kind, Device.code))
    return [
        {"id": d.id, "code": d.code, "kind": d.kind, "label": d.label, "bay_id": d.bay_id, "status": d.status,
         "last_seen_at": d.last_seen_at}
        for d in devices
    ]


@router.post("", response_model=ControllerCreatedOut, status_code=201)
def register_controller(body: ControllerCreateIn, user: StaffUser = Depends(require_role("admin")),
                        db: Session = Depends(get_db)) -> ControllerCreatedOut:
    """Register a new Raspberry Pi. The API key is returned once and never stored in clear."""
    key, prefix, key_hash = new_controller_key()
    controller = Controller(site_id=body.site_id, code=body.code, name=body.name, hardware=body.hardware,
                            api_key_prefix=prefix, api_key_hash=key_hash)
    db.add(controller)
    db.flush()
    audit(db, "controller.register", "controllers", controller.id, actor_id=user.id)
    return ControllerCreatedOut(id=controller.id, code=controller.code, api_key=key)


@router.post("/{controller_id}/rotate-key", response_model=ControllerCreatedOut)
def rotate_key(controller_id: uuid.UUID, user: StaffUser = Depends(require_role("admin")), db: Session = Depends(get_db)) -> ControllerCreatedOut:
    controller = db.get(Controller, controller_id)
    if controller is None:
        raise HTTPException(404, "controller_not_found")
    key, prefix, key_hash = new_controller_key()
    controller.api_key_prefix, controller.api_key_hash = prefix, key_hash
    audit(db, "controller.rotate_key", "controllers", controller.id, actor_id=user.id)
    return ControllerCreatedOut(id=controller.id, code=controller.code, api_key=key)
