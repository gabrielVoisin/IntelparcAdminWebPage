"""Processing of the data uploaded by the Raspberry Pi agents.

Every upload is idempotent: the Pi generates a unique id (uid) per reading / event and
may resend a batch after a network failure without creating duplicates.

Occupancy model: a parking session is one stay on one bay. It opens when the bay's
presence sensor reports "occupied" and closes when it reports "free".

Who is parked (schema v2, see services/identification.py): a phone verified over Bluetooth
by the cabinet comes first; the plate read by a camera is the fallback. Either one links the
session to a subscription or a reservation (which then counts as paid).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Bay, Controller, Device, DeviceEvent, ParkingSession, Vehicle, Zone
from app.schemas import EventsIn, HeartbeatIn, IngestResult, PassagesIn, ReadingsIn
from app.services import identification
from app.services.audit import audit
from app.services.realtime import broadcaster


def _devices_by_code(db: Session, controller: Controller) -> dict[str, Device]:
    return {d.code: d for d in db.scalars(select(Device).where(Device.controller_id == controller.id))}


def apply_heartbeat(db: Session, controller: Controller, body: HeartbeatIn) -> None:
    controller.agent_version = body.agent_version
    devices = _devices_by_code(db, controller)
    now = datetime.now(UTC)
    for item in body.devices:
        device = devices.get(str(item.get("code")))
        if device is None:
            continue
        status = item.get("status")
        if status in ("ok", "fault", "offline"):
            device.status = status
        device.last_seen_at = now


def _link_session(db: Session, session: ParkingSession, site_id) -> None:
    """Camera fallback: attach the vehicle, subscription or reservation that matches the plate.

    Does nothing when the driver was already recognised by Bluetooth or by the app.
    """
    if not session.plate_display or session.customer_id is not None:
        return
    vehicle = db.scalar(select(Vehicle).where(Vehicle.plate_norm == text_norm(session.plate_display)))
    if vehicle is None:
        return
    if vehicle.customer_id is not None:
        identification.link_session(db, session, vehicle.customer_id, vehicle.id, "camera")
        return
    session.vehicle_id = vehicle.id
    session.identified_by, session.identified_at = "camera", datetime.now(UTC)
    sub_id = db.execute(
        text("""SELECT id FROM subscriptions WHERE vehicle_id = :v AND site_id = :s AND status = 'active'
                AND current_date BETWEEN starts_on AND ends_on LIMIT 1"""),
        {"v": vehicle.id, "s": site_id},
    ).scalar()
    if sub_id:
        session.subscription_id = sub_id
        return
    res = db.execute(
        text("""SELECT r.id, r.ends_at FROM reservations r JOIN bays b ON b.id = r.bay_id JOIN zones z ON z.id = b.zone_id
                WHERE r.vehicle_id = :v AND z.site_id = :s AND r.status IN ('confirmed', 'active')
                  AND r.starts_at <= now() + interval '30 minutes' AND r.ends_at > now()
                ORDER BY r.starts_at LIMIT 1"""),
        {"v": vehicle.id, "s": site_id},
    ).first()
    if res:
        session.reservation_id = res.id
        session.paid_until = res.ends_at
        db.execute(text("UPDATE reservations SET status = 'active' WHERE id = :r AND status = 'confirmed'"), {"r": res.id})


def text_norm(plate: str) -> str:
    return "".join(ch for ch in plate.upper() if ch.isalnum())


def apply_readings(db: Session, controller: Controller, body: ReadingsIn) -> IngestResult:
    devices = _devices_by_code(db, controller)
    accepted = duplicates = 0
    rejected: list[dict] = []
    for r in sorted(body.readings, key=lambda x: x.measured_at):
        device = devices.get(r.device_code)
        if device is None or device.bay_id is None:
            rejected.append({"uid": r.uid, "reason": "unknown_device_or_no_bay"})
            continue
        inserted = db.execute(
            text("""INSERT INTO bay_readings (bay_id, device_id, reading_uid, is_occupied, plate_display, confidence, measured_at)
                    VALUES (:b, :d, :u, :o, :p, :c, :m) ON CONFLICT (bay_id, reading_uid) DO NOTHING RETURNING id"""),
            {"b": device.bay_id, "d": device.id, "u": r.uid, "o": r.is_occupied, "p": r.plate, "c": r.confidence, "m": r.measured_at},
        ).scalar()
        if inserted is None:
            duplicates += 1
            continue
        accepted += 1
        device.last_seen_at = r.measured_at
        bay = db.get(Bay, device.bay_id)
        zone = db.get(Zone, bay.zone_id)
        open_session = db.scalar(
            select(ParkingSession).where(ParkingSession.bay_id == bay.id, ParkingSession.status == "open")
        )
        if r.is_occupied and not bay.is_occupied:
            bay.is_occupied = True
            bay.occupied_since = r.measured_at
            if open_session is None:
                open_session = ParkingSession(site_id=zone.site_id, bay_id=bay.id, plate_display=r.plate, entered_at=r.measured_at)
                db.add(open_session)
                db.flush()
                # Bluetooth first (a phone may already have identified itself), then the plate
                identification.match_waiting_identification(db, open_session)
                _link_session(db, open_session, zone.site_id)
        elif not r.is_occupied and bay.is_occupied:
            bay.is_occupied = False
            bay.occupied_since = None
            if open_session is not None:
                open_session.status = "closed"
                open_session.exited_at = r.measured_at
                db.execute(
                    text("UPDATE reservations SET status = 'completed' WHERE id = :r AND status = 'active'"),
                    {"r": open_session.reservation_id},
                )
        if r.plate and open_session is not None and not open_session.plate_display:
            open_session.plate_display = r.plate
            db.flush()
            _link_session(db, open_session, zone.site_id)
        broadcaster.publish(
            "bay.updated",
            {"bay_id": bay.id, "zone_id": bay.zone_id, "is_occupied": bay.is_occupied, "plate": r.plate},
        )
    return IngestResult(accepted=accepted, duplicates=duplicates, rejected=rejected)


def apply_passages(db: Session, controller: Controller, body: PassagesIn) -> list[dict]:
    """Barrier cameras: tell the Pi whether to open the barrier for a known subscriber."""
    decisions = []
    for p in body.passages:
        open_barrier = False
        if p.direction == "in" and p.plate:
            open_barrier = bool(
                db.execute(
                    text("""SELECT 1 FROM subscriptions s JOIN vehicles v ON v.id = s.vehicle_id
                            WHERE v.plate_norm = normalize_plate(:p) AND s.site_id = :site AND s.status = 'active'
                              AND NOT s.payment_overdue AND current_date BETWEEN s.starts_on AND s.ends_on"""),
                    {"p": p.plate, "site": controller.site_id},
                ).scalar()
            )
        audit(db, f"passage.{p.direction}", "controllers", controller.id, actor_kind="controller",
              data={"uid": p.uid, "plate": p.plate, "device": p.device_code, "at": p.at.isoformat(), "open": open_barrier})
        decisions.append({"uid": p.uid, "open_barrier": open_barrier})
    return decisions


def apply_events(db: Session, controller: Controller, body: EventsIn) -> IngestResult:
    devices = _devices_by_code(db, controller)
    accepted = duplicates = 0
    for e in body.events:
        existing = db.scalar(
            select(DeviceEvent).where(DeviceEvent.controller_id == controller.id, DeviceEvent.event_uid == e.uid)
        )
        device = devices.get(e.code)
        if existing is not None:
            if e.cleared_at and existing.cleared_at is None:
                existing.cleared_at = e.cleared_at
                accepted += 1
            else:
                duplicates += 1
        else:
            db.add(
                DeviceEvent(
                    controller_id=controller.id,
                    device_id=device.id if device else None,
                    event_uid=e.uid,
                    code=e.code,
                    priority=e.priority,
                    message_key=e.message_key,
                    raised_at=e.raised_at,
                    cleared_at=e.cleared_at,
                )
            )
            accepted += 1
        if device is not None and e.priority in ("alarm", "warning"):
            device.status = "ok" if e.cleared_at else "fault"
        broadcaster.publish(
            "device_event",
            {"controller_id": controller.id, "code": e.code, "priority": e.priority, "cleared": e.cleared_at is not None},
        )
    return IngestResult(accepted=accepted, duplicates=duplicates)
