"""Super user console (DR Automation): every account of the platform, suspension and deletion."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_superuser
from app.models import Organization, StaffUser
from app.schemas import AdminOrganizationOut, AdminOrganizationPatch, DeleteConfirmIn
from app.services import parkings
from app.services.audit import audit

router = APIRouter(prefix="/admin", tags=["super user"])

SQL_ORGS = """
SELECT o.id, o.name, o.kind::text AS kind, o.email, o.is_active, o.created_at,
       (SELECT count(*) FROM sites s WHERE s.organization_id = o.id) AS parkings,
       (SELECT count(*) FROM staff_users u WHERE u.organization_id = o.id) AS members,
       (o.bank_iban IS NOT NULL) AS bank_ok
FROM organizations o ORDER BY o.kind = 'internal' DESC, o.name
"""


def organizations(db: Session) -> list[dict]:
    return [dict(r._mapping) for r in db.execute(text(SQL_ORGS))]


@router.get("/organizations", response_model=list[AdminOrganizationOut])
def list_organizations(user: StaffUser = Depends(require_superuser), db: Session = Depends(get_db)) -> list[dict]:
    return organizations(db)


@router.patch("/organizations/{org_id}", response_model=AdminOrganizationOut)
def update_organization(org_id: uuid.UUID, body: AdminOrganizationPatch, user: StaffUser = Depends(require_superuser),
                        db: Session = Depends(get_db)) -> dict:
    """Suspend or reactivate an account: a suspended account's members cannot sign in."""
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(404, "organization_not_found")
    if org.kind == "internal" and not body.is_active:
        raise HTTPException(409, "cannot_suspend_internal")
    org.is_active = body.is_active
    audit(db, "admin.organization_active", "organizations", org.id, actor_id=user.id, data={"is_active": body.is_active})
    db.flush()
    return next(o for o in organizations(db) if o["id"] == org.id)


@router.delete("/organizations/{org_id}", status_code=204)
def delete_organization(org_id: uuid.UUID, body: DeleteConfirmIn, user: StaffUser = Depends(require_superuser),
                        db: Session = Depends(get_db)) -> Response:
    """Delete an account with its parkings, places, devices, members and history. The name must be typed again."""
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(404, "organization_not_found")
    if org.kind == "internal" or org.id == user.organization_id:
        raise HTTPException(409, "cannot_delete_internal")
    if body.confirm_name.strip() != org.name:
        raise HTTPException(422, "confirm_name_mismatch")
    audit(db, "admin.organization_delete", "organizations", org.id, actor_id=user.id, data={"name": org.name})
    parkings.delete_organization(db, org)
    return Response(status_code=204)
