"""Admin tab "Generate QR code": QR sticker of a bay (5 cm x 5 cm, PDF), linked to the bay in the main database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import allowed_site_ids, check_site_access, get_current_user, require_role
from app.i18n import Translator, negotiate_locale
from app.models import Bay, StaffUser, Zone
from app.schemas import BayQrIn, BayQrOut
from app.services import bay_qr
from app.services.audit import audit
from app.services.realtime import broadcaster

router = APIRouter(tags=["bay QR codes"])

SQL_QR = """
SELECT b.id AS bay_id, s.code || '/' || z.code || '/' || b.number AS code, b.reference, b.number, s.id AS site_id,
       s.name AS site_name, z.name AS zone_name,
       coalesce(b.street, nullif(concat_ws(', ', s.address_line, nullif(concat_ws(' ', s.postcode, s.city), '')), '')) AS street,
       coalesce(b.latitude, s.latitude)::float AS latitude, coalesce(b.longitude, s.longitude)::float AS longitude,
       b.rental_status::text AS rental_status, b.qr_generated_at
FROM bays b JOIN zones z ON z.id = b.zone_id JOIN sites s ON s.id = z.site_id
WHERE {where}
"""


def qr_rows(db: Session, scope: set[uuid.UUID] | None, bay_id: uuid.UUID | None = None) -> list[dict]:
    where = "b.reference IS NOT NULL"
    params: dict = {}
    if bay_id is not None:
        where, params = "b.id = :b", {"b": bay_id}
    rows = db.execute(text(SQL_QR.format(where=where) + " ORDER BY b.qr_generated_at DESC NULLS LAST LIMIT 500"), params)
    out = []
    for r in rows:
        if scope is not None and r.site_id not in scope:
            continue
        row = dict(r._mapping)
        row.pop("site_id")
        row["qr_content"] = bay_qr.content(row["code"], row["reference"], row["street"], row["latitude"],
                                           row["longitude"]) if row["reference"] else ""
        out.append(row)
    return out


def _qr_or_404(db: Session, user: StaffUser, bay_id: uuid.UUID) -> dict:
    rows = qr_rows(db, allowed_site_ids(user), bay_id)
    if not rows:
        raise HTTPException(404, "bay_not_found")
    if not rows[0]["reference"]:
        raise HTTPException(409, "qr_not_generated")
    return rows[0]


@router.get("/bay-qr-codes", response_model=list[BayQrOut])
def list_qr_codes(user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    """Bays that have a QR code, most recent first."""
    return qr_rows(db, allowed_site_ids(user))


@router.post("/bay-qr-codes", response_model=BayQrOut, status_code=201)
def generate_qr_code(body: BayQrIn, user: StaffUser = Depends(require_role("manager")),
                     db: Session = Depends(get_db)) -> dict:
    """Generate (or regenerate) the QR code of a bay: number, street and GPS position.

    The bay is created in the zone when this number does not exist yet. Its reference is kept when the
    code is generated again, so stickers already on the ground keep working.
    """
    zone = db.get(Zone, body.zone_id)
    if zone is None:
        raise HTTPException(404, "zone_not_found")
    check_site_access(user, zone.site_id)
    number = body.number.strip().upper()
    bay = db.scalar(select(Bay).where(Bay.zone_id == zone.id, func.upper(Bay.number) == number))
    created = bay is None
    if bay is None:
        bay = Bay(zone_id=zone.id, number=number)
        db.add(bay)
    bay.street = body.street.strip()
    bay.latitude, bay.longitude = round(body.latitude, 6), round(body.longitude, 6)
    if body.rental_status:
        bay.rental_status = body.rental_status
    while not bay.reference:
        candidate = bay_qr.new_reference()
        if db.scalar(select(Bay.id).where(Bay.reference == candidate)) is None:
            bay.reference = candidate
    bay.qr_generated_at = datetime.now(UTC)
    db.flush()
    audit(db, "bay.qr_generate", "bays", bay.id, actor_id=user.id,
          data={"reference": bay.reference, "created": created, **body.model_dump(mode="json")})
    broadcaster.publish("bay.updated", {"bay_id": bay.id, "zone_id": bay.zone_id})
    return {**qr_rows(db, None, bay.id)[0], "created": created}


@router.get("/bays/{bay_id}/qr.pdf")
def qr_pdf(bay_id: uuid.UUID, request: Request, user: StaffUser = Depends(get_current_user),
           db: Session = Depends(get_db)) -> Response:
    """The sticker to print: A4 page, QR code of 5 cm x 5 cm with the bay, its street, GPS and reference."""
    row = _qr_or_404(db, user, bay_id)
    t = Translator(negotiate_locale(request, user.locale))
    gps = f"GPS {row['latitude']:.6f}, {row['longitude']:.6f}" if row["latitude"] is not None else None
    data = bay_qr.pdf(
        row["qr_content"], title=f"QR {row['code']}", scan_hint=t("qr.pdf_scan_hint"),
        bay_line=f"{t('qr.pdf_bay')} {row['number']} · {row['code']}", street=row["street"], gps_line=gps,
        reference_line=f"{t('qr.reference')} {row['reference']}",
    )
    filename = f"qr-{row['code'].replace('/', '-')}.pdf"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/bays/{bay_id}/qr.svg")
def qr_svg(bay_id: uuid.UUID, user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    row = _qr_or_404(db, user, bay_id)
    return Response(bay_qr.svg(row["qr_content"]), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})
