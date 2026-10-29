from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

import hashlib

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings

from app.db import get_db
from app.deps import allowed_site_ids, check_site_access, get_current_user, require_permission, require_role
from app.models import AlarmEvidence, Fine, PaymentAlarm, StaffUser
from app.schemas import FineIn, PaymentAlarmOut
from app.services import queries, settings_store
from app.services.audit import audit
from app.services.realtime import broadcaster

router = APIRouter(prefix="/payment-alarms", tags=["payment alarms"])


@router.get("", response_model=list[PaymentAlarmOut])
def list_alarms(
    status: Literal["open", "acknowledged", "fined", "all"] = "open",
    site_id: uuid.UUID | None = Query(None),
    user: StaffUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    scope = allowed_site_ids(user)
    if site_id:
        check_site_access(user, site_id)
        scope = {site_id}
    return queries.payment_alarms(db, scope, None if status == "all" else status)


def _get(db: Session, alarm_id: uuid.UUID, user: StaffUser) -> PaymentAlarm:
    alarm = db.get(PaymentAlarm, alarm_id)
    if alarm is None:
        raise HTTPException(404, "alarm_not_found")
    check_site_access(user, alarm.site_id)
    return alarm


def _out(db: Session, alarm: PaymentAlarm) -> dict:
    db.flush()
    return next(a for a in queries.payment_alarms(db, {alarm.site_id}, None, limit=500) if a["id"] == alarm.id)


@router.post("/{alarm_id}/acknowledge", response_model=PaymentAlarmOut)
def acknowledge(alarm_id: uuid.UUID, user: StaffUser = Depends(require_permission("acknowledge_alarm")), db: Session = Depends(get_db)) -> dict:
    alarm = _get(db, alarm_id, user)
    if alarm.status != "open":
        raise HTTPException(409, "alarm_not_open")
    alarm.status, alarm.handled_by, alarm.handled_at = "acknowledged", user.id, datetime.now(UTC)
    audit(db, "alarm.acknowledge", "payment_alarms", alarm.id, actor_id=user.id)
    broadcaster.publish("payment_alarm.updated", {"id": alarm.id, "status": alarm.status})
    return _out(db, alarm)


@router.post("/{alarm_id}/fine", response_model=PaymentAlarmOut)
def issue_fine(
    alarm_id: uuid.UUID, body: FineIn, user: StaffUser = Depends(require_permission("issue_fine")), db: Session = Depends(get_db)
) -> dict:
    alarm = _get(db, alarm_id, user)
    if alarm.status != "open":
        raise HTTPException(409, "alarm_not_open")
    amount = body.amount_cents if body.amount_cents is not None else int(settings_store.get(db, "control.fine_cents"))
    db.add(Fine(alarm_id=alarm.id, amount_cents=amount, issued_by=user.id))
    alarm.status, alarm.handled_by, alarm.handled_at = "fined", user.id, datetime.now(UTC)
    audit(db, "alarm.fine", "payment_alarms", alarm.id, actor_id=user.id, data={"amount_cents": amount})
    broadcaster.publish("payment_alarm.updated", {"id": alarm.id, "status": alarm.status})
    return _out(db, alarm)


@router.post("/{alarm_id}/reopen", response_model=PaymentAlarmOut)
def reopen(alarm_id: uuid.UUID, user: StaffUser = Depends(require_role("manager")), db: Session = Depends(get_db)) -> dict:
    """Undo an acknowledgement or cancel a fine issued by mistake."""
    alarm = _get(db, alarm_id, user)
    if alarm.status == "fined":
        for fine in db.query(Fine).filter(Fine.alarm_id == alarm.id, Fine.status == "issued"):
            fine.status = "cancelled"
    alarm.status, alarm.handled_by, alarm.handled_at = "open", None, None
    audit(db, "alarm.reopen", "payment_alarms", alarm.id, actor_id=user.id)
    broadcaster.publish("payment_alarm.updated", {"id": alarm.id, "status": alarm.status})
    return _out(db, alarm)


# --- Evidence photos (Android: photo of the plate before a fine) -----------------------
@router.post("/{alarm_id}/evidence", status_code=201)
async def add_evidence(
    alarm_id: uuid.UUID,
    photo: UploadFile = File(..., description="JPEG of the plate, 3 MB max"),
    plate_read: str | None = Form(None, max_length=20, description="Plate read on the phone (OCR or typed)"),
    user: StaffUser = Depends(require_permission("issue_fine")),
    db: Session = Depends(get_db),
) -> dict:
    alarm = _get(db, alarm_id, user)
    settings = get_settings()
    data = await photo.read(settings.evidence_max_bytes + 1)
    if len(data) > settings.evidence_max_bytes:
        raise HTTPException(413, "photo_too_large")
    if not data.startswith(b"\xff\xd8"):
        raise HTTPException(415, "photo_must_be_jpeg")
    digest = hashlib.sha256(data).hexdigest()
    folder = settings.evidence_dir / str(alarm.id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{digest[:12]}.jpg"
    (folder / name).write_bytes(data)
    row = AlarmEvidence(alarm_id=alarm.id, file_name=f"{alarm.id}/{name}", size_bytes=len(data), sha256=digest,
                        plate_read=plate_read.strip().upper() if plate_read else None, taken_by=user.id)
    db.add(row)
    db.flush()
    audit(db, "alarm.evidence", "payment_alarms", alarm.id, actor_id=user.id, data={"sha256": digest, "plate_read": row.plate_read})
    return {"id": row.id, "sha256": digest, "size_bytes": len(data), "plate_read": row.plate_read}


@router.get("/{alarm_id}/evidence")
def list_evidence(alarm_id: uuid.UUID, user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    alarm = _get(db, alarm_id, user)
    rows = db.scalars(select(AlarmEvidence).where(AlarmEvidence.alarm_id == alarm.id).order_by(AlarmEvidence.taken_at)).all()
    return [{"id": r.id, "plate_read": r.plate_read, "taken_at": r.taken_at, "size_bytes": r.size_bytes,
             "url": f"/api/v1/payment-alarms/{alarm.id}/evidence/{r.id}"} for r in rows]


@router.get("/{alarm_id}/evidence/{evidence_id}")
def get_evidence(alarm_id: uuid.UUID, evidence_id: uuid.UUID, user: StaffUser = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> FileResponse:
    alarm = _get(db, alarm_id, user)
    row = db.get(AlarmEvidence, evidence_id)
    if row is None or row.alarm_id != alarm.id:
        raise HTTPException(404, "not_found")
    path = (get_settings().evidence_dir / row.file_name).resolve()
    if not path.is_relative_to(get_settings().evidence_dir.resolve()) or not path.exists():
        raise HTTPException(404, "not_found")
    return FileResponse(path, media_type=row.content_type)
