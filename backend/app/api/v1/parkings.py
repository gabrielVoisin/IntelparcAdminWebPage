"""Parkings of the multi-account platform: creation by an account, places, presence sensors and smart
barriers (each with its QR code), cabinets, deletion by the super user."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import (
    allowed_site_ids,
    check_site_access,
    get_current_user,
    get_site_or_404,
    require_role,
    require_superuser,
)
from app.i18n import Translator, negotiate_locale
from app.models import Bay, Controller, Device, Organization, StaffUser, Zone
from app.schemas import (
    BarrierIn,
    ControllerCreatedOut,
    ControllerNameIn,
    DeleteConfirmIn,
    EquipmentCreatedOut,
    GeneratePlacesIn,
    ParkingIn,
    ParkingOut,
    ParkingPatch,
    SensorIn,
)
from app.services import bay_qr, parkings, queries
from app.services.audit import audit
from app.services.realtime import broadcaster

router = APIRouter(tags=["parkings"])


def _out(db: Session, site_id: uuid.UUID) -> dict:
    return next(s for s in queries.sites(db, {site_id}))


def _error(exc: parkings.ParkingError) -> HTTPException:
    code = str(exc)
    return HTTPException(404 if code.endswith("not_found") else 409 if code == "bay_in_use" else 422, code)


@router.get("/parkings", response_model=list[ParkingOut])
def list_parkings(user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    """The parkings of the signed-in person's account (every parking for the super user)."""
    return queries.sites(db, allowed_site_ids(user))


@router.post("/parkings", response_model=ParkingOut, status_code=201)
def create_parking(body: ParkingIn, user: StaffUser = Depends(require_role("manager")), db: Session = Depends(get_db)) -> dict:
    """Name, number of places, entrance address and management mode (QR code, presence sensor, smart barrier)."""
    if user.is_superuser and body.organization_id:
        org = db.get(Organization, body.organization_id)
        if org is None:
            raise HTTPException(404, "organization_not_found")
    else:
        org = user.organization
        if org is None:
            raise HTTPException(403, "no_organization")
    try:
        site = parkings.create_parking(
            db, org, name=body.name, capacity=body.capacity, mode=body.management_mode, address_line=body.address_line,
            postcode=body.postcode, city=body.city, country=body.country.upper(), latitude=body.latitude,
            longitude=body.longitude)
    except parkings.ParkingError as exc:
        raise _error(exc) from exc
    audit(db, "parking.create", "sites", site.id, actor_id=user.id, data=body.model_dump(mode="json"))
    out = _out(db, site.id)
    out["redirect"] = f"/parkings/{site.id}/open?welcome=1"
    return out


@router.patch("/parkings/{site_id}", response_model=ParkingOut)
def update_parking(site_id: uuid.UUID, body: ParkingPatch, user: StaffUser = Depends(require_role("manager")),
                   db: Session = Depends(get_db)) -> dict:
    site = get_site_or_404(db, site_id, user)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(site, field, value.strip() if isinstance(value, str) else value)
    audit(db, "parking.update", "sites", site.id, actor_id=user.id, data=body.model_dump(mode="json", exclude_unset=True))
    db.flush()
    return _out(db, site.id)


@router.delete("/parkings/{site_id}", status_code=204)
def delete_parking(site_id: uuid.UUID, body: DeleteConfirmIn, user: StaffUser = Depends(require_superuser),
                   db: Session = Depends(get_db)) -> Response:
    """Super user only. The name must be typed again: everything recorded in the parking is deleted."""
    site = get_site_or_404(db, site_id, user)
    if body.confirm_name.strip() != site.name:
        raise HTTPException(422, "confirm_name_mismatch")
    audit(db, "parking.delete", "sites", site.id, actor_id=user.id, data={"name": site.name, "code": site.code})
    parkings.delete_parking(db, site)
    return Response(status_code=204)


# --- Places (QR code mode) ---------------------------------------------------------------------------
@router.post("/parkings/{site_id}/places", status_code=201)
def generate_places(site_id: uuid.UUID, body: GeneratePlacesIn, user: StaffUser = Depends(require_role("manager")),
                    db: Session = Depends(get_db)) -> dict:
    """Generate N QR codes: each one creates a place, shown at once on the overview and bookable in the app."""
    site = get_site_or_404(db, site_id, user)
    try:
        bays = parkings.generate_places(db, site, body.count, body.kind, body.rental_status)
    except parkings.ParkingError as exc:
        raise _error(exc) from exc
    audit(db, "parking.places_generate", "sites", site.id, actor_id=user.id,
          data={"count": body.count, "numbers": [b.number for b in bays]})
    broadcaster.publish("bay.updated", {"site_id": site.id})
    return {"created": [{"id": b.id, "number": b.number, "reference": b.reference} for b in bays],
            "first": bays[0].number, "last": bays[-1].number}


@router.delete("/bays/{bay_id}", status_code=204)
def delete_place(bay_id: uuid.UUID, user: StaffUser = Depends(require_role("manager")), db: Session = Depends(get_db)) -> Response:
    """Remove a place (and its presence sensor). Refused while it has bookings, a car parked or a subscriber."""
    bay = db.get(Bay, bay_id)
    if bay is None:
        raise HTTPException(404, "bay_not_found")
    check_site_access(user, bay.zone.site_id)
    try:
        parkings.delete_place(db, bay)
    except parkings.ParkingError as exc:
        raise _error(exc) from exc
    audit(db, "bay.delete", "bays", bay_id, actor_id=user.id, data={"number": bay.number})
    broadcaster.publish("bay.updated", {"bay_id": bay_id})
    return Response(status_code=204)


# --- Presence sensors and smart barriers ----------------------------------------------------------------
@router.post("/parkings/{site_id}/sensors", response_model=EquipmentCreatedOut, status_code=201)
def add_sensor(site_id: uuid.UUID, body: SensorIn, user: StaffUser = Depends(require_role("manager")),
               db: Session = Depends(get_db)) -> EquipmentCreatedOut:
    """The sensor creates its place and the place's QR code. The parking's first cabinet is created if needed."""
    site = get_site_or_404(db, site_id, user)
    try:
        device, bay, key = parkings.add_sensor(db, site, kind=body.kind, address=body.address, live_url=body.live_url,
                                               controller_id=body.controller_id, bay_kind=body.bay_kind)
    except parkings.ParkingError as exc:
        raise _error(exc) from exc
    controller = db.get(Controller, device.controller_id)
    audit(db, "parking.sensor_add", "devices", device.id, actor_id=user.id, data={"bay": bay.number, "code": device.code})
    broadcaster.publish("bay.updated", {"site_id": site.id})
    return EquipmentCreatedOut(id=device.id, code=device.code, bay_id=bay.id, bay_number=bay.number, reference=bay.reference,
                               controller_id=controller.id, controller_code=controller.code, api_key=key)


@router.post("/parkings/{site_id}/barriers", response_model=EquipmentCreatedOut, status_code=201)
def add_barrier(site_id: uuid.UUID, body: BarrierIn, user: StaffUser = Depends(require_role("manager")),
                db: Session = Depends(get_db)) -> EquipmentCreatedOut:
    """Each barrier gets its QR code: scanning it books a free place of the parking."""
    site = get_site_or_404(db, site_id, user)
    try:
        device, key = parkings.add_barrier(db, site, name=body.name, direction=body.direction, address=body.address,
                                           controller_id=body.controller_id)
    except parkings.ParkingError as exc:
        raise _error(exc) from exc
    controller = db.get(Controller, device.controller_id)
    audit(db, "parking.barrier_add", "devices", device.id, actor_id=user.id, data={"name": body.name, "code": device.code})
    return EquipmentCreatedOut(id=device.id, code=device.code, reference=device.reference, controller_id=controller.id,
                               controller_code=controller.code, api_key=key)


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(device_id: uuid.UUID, user: StaffUser = Depends(require_role("manager")), db: Session = Depends(get_db)) -> Response:
    """Remove a sensor or a barrier. A sensor's place goes with it when it was never used."""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "device_not_found")
    controller = db.get(Controller, device.controller_id)
    check_site_access(user, controller.site_id)
    bay_id = device.bay_id if device.kind in parkings.SENSOR_KINDS.values() else None
    audit(db, "device.delete", "devices", device.id, actor_id=user.id, data={"code": device.code, "kind": device.kind})
    db.delete(device)
    db.flush()
    if bay_id is not None and not parkings.bay_in_use(db, bay_id):
        bay = db.get(Bay, bay_id)
        if bay is not None:
            db.delete(bay)
    broadcaster.publish("bay.updated", {"site_id": controller.site_id})
    return Response(status_code=204)


@router.post("/parkings/{site_id}/controllers", response_model=ControllerCreatedOut, status_code=201)
def add_controller(site_id: uuid.UUID, body: ControllerNameIn, user: StaffUser = Depends(require_role("admin")),
                   db: Session = Depends(get_db)) -> ControllerCreatedOut:
    """A new cabinet (Raspberry Pi) for this parking. Its API key is shown once."""
    site = get_site_or_404(db, site_id, user)
    if site.management_mode == "qr_code":
        raise HTTPException(409, "no_devices_in_qr_mode")
    controller, key = parkings.new_controller(db, site, body.name)
    audit(db, "controller.register", "controllers", controller.id, actor_id=user.id)
    return ControllerCreatedOut(id=controller.id, code=controller.code, api_key=key)


# --- QR code of a barrier ------------------------------------------------------------------------------
def _barrier_qr(db: Session, user: StaffUser, device_id: uuid.UUID) -> tuple[Device, dict]:
    device = db.get(Device, device_id)
    if device is None or device.kind != "barrier" or not device.reference:
        raise HTTPException(404, "device_not_found")
    controller = db.get(Controller, device.controller_id)
    check_site_access(user, controller.site_id)
    site = controller.site
    zone = db.scalar(select(Zone).where(Zone.site_id == site.id).order_by(Zone.sort_order).limit(1))
    street = ", ".join(filter(None, [site.address_line, " ".join(filter(None, [site.postcode, site.city]))])) or None
    code = f"{site.code}/{zone.code if zone else parkings.DEFAULT_ZONE}/{device.code}"
    content = bay_qr.content(code, device.reference, street, float(site.latitude) if site.latitude is not None else None,
                             float(site.longitude) if site.longitude is not None else None)
    return device, {"code": code, "content": content, "street": street, "site": site}


@router.get("/devices/{device_id}/qr.svg")
def barrier_qr_svg(device_id: uuid.UUID, user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    _, row = _barrier_qr(db, user, device_id)
    return Response(bay_qr.svg(row["content"]), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.get("/devices/{device_id}/qr.pdf")
def barrier_qr_pdf(device_id: uuid.UUID, request: Request, user: StaffUser = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> Response:
    device, row = _barrier_qr(db, user, device_id)
    t = Translator(negotiate_locale(request, user.locale))
    site = row["site"]
    gps = f"GPS {float(site.latitude):.6f}, {float(site.longitude):.6f}" if site.latitude is not None else None
    data = bay_qr.pdf(row["content"], title=f"QR {row['code']}", scan_hint=t("parking.barrier_scan_hint"),
                      bay_line=f"{parkings.barrier_name(device)} · {site.name}", street=row["street"], gps_line=gps,
                      reference_line=f"{t('qr.reference')} {device.reference}")
    filename = f"qr-{row['code'].replace('/', '-')}.pdf"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
