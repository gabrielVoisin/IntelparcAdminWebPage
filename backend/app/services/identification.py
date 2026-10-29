"""Who is parked on this bay? Bluetooth first, camera as fallback.

The smart bay recognises the driver in this order:
1. bluetooth - the phone was verified by the cabinet's Raspberry Pi (POST /device/identifications);
2. app       - the driver scanned the bay's QR code and paid in the app (POST /app/sessions);
3. camera    - the plate read by a camera matches a registered vehicle (bay readings with a plate);
4. nobody    - after the grace period the alarm engine raises "no_payment" as before.

An identification may arrive before the presence sensor reports the car (the phone connects while the
driver parks) or after it. Both orders are handled: identifications without a session wait up to
"ble.match_window_min" minutes, and every new session looks for a waiting identification.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Bay, BleIdentification, Controller, ParkingSession, Zone
from app.services import settings_store
from app.services.audit import audit
from app.services.realtime import broadcaster

# Most reliable source first: a later, weaker source never overwrites a stronger one.
METHOD_RANK = {"manual": 4, "bluetooth": 3, "app": 2, "camera": 1}


def bay_label(db: Session, bay_id: uuid.UUID | None) -> str | None:
    if bay_id is None:
        return None
    row = db.execute(
        text("SELECT s.code || ' · ' || z.code || ' · ' || b.number FROM bays b JOIN zones z ON z.id = b.zone_id "
             "JOIN sites s ON s.id = z.site_id WHERE b.id = :b"),
        {"b": bay_id},
    ).scalar()
    return row


def decide(db: Session, customer_id: uuid.UUID, site_id: uuid.UUID, bay_id: uuid.UUID) -> tuple[str, str | None]:
    """Return (status, right kind) for this customer on this bay, now.

    authorised         - subscription on the site, reservation of this bay, or paid app session on this bay
    reserved_by_other  - the bay is booked / assigned to somebody else right now
    wrong_bay          - the customer has a reservation, but for another bay of the site
    payment_required   - nothing paid
    """
    rights = db.execute(
        text("SELECT kind, bay_id FROM v_customer_rights WHERE customer_id = :c AND site_id = :s"),
        {"c": customer_id, "s": site_id},
    ).all()
    # 1. a right on this very bay (its reservation, its assigned subscription bay, a paid session here)
    for kind, right_bay in rights:
        if right_bay == bay_id:
            return "authorised", kind
    # 2. the bay belongs to somebody else right now: even a subscriber may not take it
    taken_by_other = db.execute(
        text("""SELECT 1 FROM v_customer_rights
                WHERE bay_id = :b AND customer_id <> :c AND kind IN ('reservation', 'subscription') LIMIT 1"""),
        {"b": bay_id, "c": customer_id},
    ).scalar()
    if taken_by_other:
        return "reserved_by_other", None
    # 3. a subscription valid on the whole car park
    for kind, _ in rights:
        if kind == "subscription":
            return "authorised", kind
    # 4. booked, but another bay
    if any(kind == "reservation" for kind, _ in rights):
        return "wrong_bay", "reservation"
    return "payment_required", None


def link_session(
    db: Session,
    session: ParkingSession,
    customer_id: uuid.UUID,
    vehicle_id: uuid.UUID | None,
    method: str,
    at: datetime | None = None,
) -> bool:
    """Attach a customer to a parking session; also attach their subscription or reservation.

    Returns False when the session already belongs to someone else or to a more reliable source.
    """
    if session.customer_id is not None and session.customer_id != customer_id:
        return False
    if session.identified_by and METHOD_RANK[session.identified_by] > METHOD_RANK[method] and session.customer_id:
        return False
    session.customer_id = customer_id
    if vehicle_id is not None and session.vehicle_id is None:
        session.vehicle_id = vehicle_id
    if session.identified_by is None or METHOD_RANK[method] >= METHOD_RANK[session.identified_by]:
        session.identified_by = method
        session.identified_at = at or datetime.now(UTC)
    if session.subscription_id is None:
        sub_id = db.execute(
            text("""SELECT id FROM subscriptions WHERE customer_id = :c AND site_id = :s AND status = 'active'
                      AND NOT payment_overdue AND current_date BETWEEN starts_on AND ends_on
                    ORDER BY (bay_id = :b) DESC NULLS LAST LIMIT 1"""),
            {"c": customer_id, "s": session.site_id, "b": session.bay_id},
        ).scalar()
        if sub_id:
            session.subscription_id = sub_id
    if session.subscription_id is None and session.reservation_id is None and session.bay_id is not None:
        res = db.execute(
            text("""SELECT id, ends_at FROM reservations
                    WHERE customer_id = :c AND bay_id = :b AND status IN ('confirmed', 'active')
                      AND starts_at <= now() + interval '30 minutes' AND ends_at > now()
                    ORDER BY starts_at LIMIT 1"""),
            {"c": customer_id, "b": session.bay_id},
        ).first()
        if res:
            session.reservation_id = res.id
            session.paid_until = max(session.paid_until or res.ends_at, res.ends_at)
            db.execute(text("UPDATE reservations SET status = 'active' WHERE id = :r AND status = 'confirmed'"), {"r": res.id})
    return True


def _raise_misuse_alarm(db: Session, session: ParkingSession) -> None:
    row = db.execute(
        text("""INSERT INTO payment_alarms (site_id, bay_id, session_id, plate_display, reason, detected_at)
                VALUES (:s, :b, :p, :plate, 'reserved_bay_misuse', now())
                ON CONFLICT DO NOTHING RETURNING id, site_id, plate_display, reason"""),
        {"s": session.site_id, "b": session.bay_id, "p": session.id, "plate": session.plate_display},
    ).first()
    if row:
        broadcaster.publish("payment_alarm.created", dict(row._mapping))


def _apply_decision(db: Session, ident: BleIdentification, session: ParkingSession | None, site_id: uuid.UUID) -> str:
    if ident.bay_id is None:
        ident.status = "pending"
        return ident.status
    status, _ = decide(db, ident.customer_id, site_id, ident.bay_id)
    ident.status = status
    if session is not None:
        ident.session_id = session.id
        if status == "reserved_by_other":
            _raise_misuse_alarm(db, session)
    return status


def apply_identification(db: Session, controller: Controller, item) -> dict:
    """Store one identification sent by a cabinet and return the decision for the phone.

    item: schemas.IdentificationIn (uid, customer_id, vehicle_id, bay_id, rssi, identified_at).
    Idempotent: the same uid sent twice returns the stored decision.
    """
    existing = db.scalar(
        select(BleIdentification).where(
            BleIdentification.controller_id == controller.id, BleIdentification.identification_uid == item.uid
        )
    )
    if existing is not None:
        return _decision_out(db, existing)

    bay_id = None
    if item.bay_id is not None:
        bay = db.get(Bay, item.bay_id)
        if bay is not None and db.get(Zone, bay.zone_id).site_id == controller.site_id:
            bay_id = bay.id
    ident = BleIdentification(
        controller_id=controller.id, identification_uid=item.uid, customer_id=item.customer_id,
        vehicle_id=item.vehicle_id, bay_id=bay_id, rssi=item.rssi, identified_at=item.identified_at,
    )
    db.add(ident)
    db.flush()

    window = timedelta(minutes=int(settings_store.get(db, "ble.match_window_min", controller.site_id)))
    session = None
    if bay_id is not None:
        session = db.scalar(select(ParkingSession).where(ParkingSession.bay_id == bay_id, ParkingSession.status == "open"))
    else:
        # The phone did not say where it is: take the car that arrived closest in time on this site,
        # still unidentified (one cabinet usually serves one row or one car park).
        session = db.scalar(
            select(ParkingSession)
            .where(
                ParkingSession.site_id == controller.site_id,
                ParkingSession.status == "open",
                ParkingSession.customer_id.is_(None),
                ParkingSession.bay_id.is_not(None),
                ParkingSession.entered_at.between(item.identified_at - window, item.identified_at + window),
            )
            .order_by(text("abs(extract(epoch FROM parking_sessions.entered_at - :at))").bindparams(at=item.identified_at))
            .limit(1)
        )
        if session is not None:
            ident.bay_id = session.bay_id
    if session is not None and not link_session(db, session, item.customer_id, item.vehicle_id, "bluetooth", item.identified_at):
        session = None  # bay already taken by another identified driver: decide on the bay, do not relink
    _apply_decision(db, ident, session, controller.site_id)
    audit(db, "ble.identification", "ble_identifications", ident.id, actor_kind="controller",
          data={"controller": controller.code, "status": ident.status, "bay_id": str(ident.bay_id) if ident.bay_id else None})
    broadcaster.publish("ble.identified", {"bay_id": ident.bay_id, "status": ident.status, "controller_id": controller.id})
    return _decision_out(db, ident)


def match_waiting_identification(db: Session, session: ParkingSession) -> bool:
    """A car was just detected on a bay: is a phone already waiting for it? Called by the ingestion."""
    if session.bay_id is None or session.customer_id is not None:
        return False
    window = timedelta(minutes=int(settings_store.get(db, "ble.match_window_min", session.site_id)))
    ident = db.scalar(
        select(BleIdentification)
        .join(Controller, Controller.id == BleIdentification.controller_id)
        .where(
            Controller.site_id == session.site_id,
            BleIdentification.session_id.is_(None),
            (BleIdentification.bay_id == session.bay_id) | BleIdentification.bay_id.is_(None),
            BleIdentification.identified_at.between(session.entered_at - window, session.entered_at + window),
        )
        .order_by((BleIdentification.bay_id == session.bay_id).desc().nulls_last(), BleIdentification.identified_at.desc())
        .limit(1)
    )
    if ident is None or not link_session(db, session, ident.customer_id, ident.vehicle_id, "bluetooth", ident.identified_at):
        return False
    ident.bay_id = session.bay_id
    _apply_decision(db, ident, session, session.site_id)
    broadcaster.publish("ble.identified", {"bay_id": ident.bay_id, "status": ident.status})
    return True


def _decision_out(db: Session, ident: BleIdentification) -> dict:
    return {
        "uid": ident.identification_uid,
        "status": ident.status,
        "bay_id": ident.bay_id,
        "bay_label": bay_label(db, ident.bay_id),
        "session_id": ident.session_id,
    }
