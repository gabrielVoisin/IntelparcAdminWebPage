"""API used by the Raspberry Pi agent in each control cabinet.

Authentication: header "X-Controller-Key: <prefix>.<secret>" over HTTPS.
All write endpoints are idempotent (uid per reading / event / passage), so the agent
can safely resend its offline queue.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_controller
from app.models import Bay, Controller, Device, Site, Zone
from app.schemas import (
    BleConfig,
    DeviceConfigItem,
    DeviceConfigOut,
    EventsIn,
    HeartbeatIn,
    IdentificationDecision,
    IdentificationsIn,
    IngestResult,
    PassagesIn,
    ReadingsIn,
    WatchdogIn,
)
from app.services import ble_identity, identification, ingest, settings_store, watchdog

router = APIRouter(prefix="/device", tags=["raspberry pi"])


@router.get("/config", response_model=DeviceConfigOut)
def config(simulated: bool = False, controller: Controller = Depends(get_current_controller),
           db: Session = Depends(get_db)) -> DeviceConfigOut:
    """Everything the Pi needs at start-up: its devices, the bay each sensor watches, intervals.

    Sensors with a "sim:" address are driven by the test simulator: only ?simulated=true lists them.
    """
    simulated_only = Device.address.like("sim:%")
    rows = db.execute(
        select(Device, Bay.number, Zone.code)
        .outerjoin(Bay, Bay.id == Device.bay_id)
        .outerjoin(Zone, Zone.id == Bay.zone_id)
        .where(Device.controller_id == controller.id, Device.address.is_not(None),  # not configured: no wiring yet
               simulated_only if simulated else ~simulated_only)
        .order_by(Device.code)
    ).all()
    values = settings_store.get_all(db, controller.site_id)
    site = db.get(Site, controller.site_id)
    return DeviceConfigOut(
        controller_id=controller.id,
        controller_code=controller.code,
        site_code=site.code,
        heartbeat_interval_s=int(values["device.heartbeat_interval_s"]),
        upload_interval_s=int(values["device.upload_interval_s"]),
        devices=[
            DeviceConfigItem(code=d.code, kind=d.kind, address=d.address, bay_id=d.bay_id, bay_number=number, zone_code=zone)
            for d, number, zone in rows
        ],
        server_time=datetime.now(UTC),
        ble=BleConfig(enabled=bool(values["ble.enabled"]), public_key=ble_identity.public_key_b64(), site_name=site.name),
    )


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, controller: Controller = Depends(get_current_controller), db: Session = Depends(get_db)) -> dict:
    ingest.apply_heartbeat(db, controller, body)
    return {"ok": True, "server_time": datetime.now(UTC)}


@router.post("/watchdog")
def watchdog_signal(body: WatchdogIn, controller: Controller = Depends(get_current_controller),
                    db: Session = Depends(get_db)) -> dict:
    """Communication watchdog, every 2 s (0.5 Hz). 3 missing signals raise device.cabinet_communication_lost."""
    watchdog.beat(db, controller, body.source, body.seq)
    return {"ok": True, "period_s": watchdog.PERIOD_S, "server_time": datetime.now(UTC)}


@router.post("/readings", response_model=IngestResult)
def readings(body: ReadingsIn, controller: Controller = Depends(get_current_controller), db: Session = Depends(get_db)) -> IngestResult:
    """Presence sensor readings (occupied / free, optional plate read by the front camera)."""
    return ingest.apply_readings(db, controller, body)


@router.post("/passages")
def passages(body: PassagesIn, controller: Controller = Depends(get_current_controller), db: Session = Depends(get_db)) -> dict:
    """Barrier cameras: returns, for each passage, whether the Pi should open the barrier."""
    return {"decisions": ingest.apply_passages(db, controller, body)}


@router.post("/events", response_model=IngestResult)
def events(body: EventsIn, controller: Controller = Depends(get_current_controller), db: Session = Depends(get_db)) -> IngestResult:
    """Alarms and warnings raised or cleared by the cabinet (faulty camera, sensor, lost connection...)."""
    return ingest.apply_events(db, controller, body)


@router.post("/identifications")
def identifications(body: IdentificationsIn, controller: Controller = Depends(get_current_controller),
                    db: Session = Depends(get_db)) -> dict[str, list[IdentificationDecision]]:
    """Phones verified over Bluetooth by the cabinet. For each one: is this the right driver for the bay?

    The Pi already checked the server signature of the phone's token and its answer to the challenge;
    the server decides with the subscriptions, bookings and payments (docs/BLUETOOTH.md).
    """
    return {"decisions": [IdentificationDecision(**identification.apply_identification(db, controller, item))
                          for item in body.identifications]}
