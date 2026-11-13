from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import PERMISSIONS, require_role
from app.models import Site, StaffSiteAccess, StaffUser
from app.schemas import InvitationIn, TeamMemberOut, TeamMemberPatch
from app.services import queries
from app.services.audit import audit

router = APIRouter(prefix="/team", tags=["team"])
# The members of the signed-in person's account ("Mon compte > Utilisateurs" on the website).


def _member_or_404(db: Session, user: StaffUser, member_id: uuid.UUID) -> StaffUser:
    member = db.get(StaffUser, member_id)
    if member is None or (member.organization_id != user.organization_id and not user.is_superuser):
        raise HTTPException(404, "member_not_found")
    return member


def _check_sites(db: Session, user: StaffUser, org_id: uuid.UUID | None, site_ids: list[uuid.UUID]) -> None:
    if not site_ids:
        return
    own = set(db.scalars(select(Site.id).where(Site.organization_id == org_id)))
    if not set(site_ids) <= own:
        raise HTTPException(403, "site_not_allowed")


@router.get("", response_model=list[TeamMemberOut])
def list_team(user: StaffUser = Depends(require_role("manager")), db: Session = Depends(get_db)) -> list[dict]:
    return queries.team(db, user.organization_id)


@router.get("/permissions")
def permissions(user: StaffUser = Depends(require_role("viewer"))) -> dict[str, list[str]]:
    return {k: list(v) for k, v in PERMISSIONS.items()}


def _set_sites(db: Session, member: StaffUser, site_ids: list[uuid.UUID]) -> None:
    db.execute(delete(StaffSiteAccess).where(StaffSiteAccess.staff_user_id == member.id))
    for site_id in site_ids:
        db.add(StaffSiteAccess(staff_user_id=member.id, site_id=site_id))


@router.post("/invitations", response_model=TeamMemberOut, status_code=201)
def invite(body: InvitationIn, user: StaffUser = Depends(require_role("admin")), db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(StaffUser).where(func.lower(StaffUser.email) == body.email.lower())):
        raise HTTPException(409, "email_already_used")
    _check_sites(db, user, user.organization_id, body.site_ids)
    member = StaffUser(email=body.email.lower(), full_name=body.full_name, role=body.role, invited_at=datetime.now(UTC),
                       organization_id=user.organization_id)
    db.add(member)
    db.flush()
    _set_sites(db, member, body.site_ids)
    # Sending the e-mail with the password-creation link is delegated to the mail service (not included).
    audit(db, "team.invite", "staff_users", member.id, actor_id=user.id, data={"role": body.role})
    db.flush()
    return next(m for m in queries.team(db, user.organization_id) if m["id"] == member.id)


@router.patch("/{member_id}", response_model=TeamMemberOut)
def update_member(member_id: uuid.UUID, body: TeamMemberPatch, user: StaffUser = Depends(require_role("admin")),
                  db: Session = Depends(get_db)) -> dict:
    member = _member_or_404(db, user, member_id)
    if member.id == user.id and (body.is_active is False or (body.role and body.role != "admin")):
        raise HTTPException(409, "cannot_demote_or_disable_yourself")
    changes = body.model_dump(exclude_unset=True)
    site_ids = changes.pop("site_ids", None)
    for field, value in changes.items():
        setattr(member, field, value)
    if site_ids is not None:
        _check_sites(db, user, member.organization_id, site_ids)
        _set_sites(db, member, site_ids)
    audit(db, "team.update", "staff_users", member.id, actor_id=user.id, data=body.model_dump(mode="json", exclude_unset=True))
    db.flush()
    return next(m for m in queries.team(db, member.organization_id) if m["id"] == member.id)


@router.delete("/{member_id}/invitation", status_code=204)
def cancel_invitation(member_id: uuid.UUID, user: StaffUser = Depends(require_role("admin")), db: Session = Depends(get_db)) -> None:
    member = _member_or_404(db, user, member_id)
    if member.password_hash is not None:
        raise HTTPException(404, "invitation_not_found")
    db.delete(member)
    audit(db, "team.cancel_invitation", "staff_users", member_id, actor_id=user.id)
