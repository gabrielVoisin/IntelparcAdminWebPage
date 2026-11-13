"""The signed-in person's account (organization): public sign-up, account details, bank details, monthly
statements of the transfers, and the account's users ("Mon compte" on the website)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import ACCESS_COOKIE, get_current_user, require_role, user_organization
from app.models import RefreshToken, StaffUser
from app.schemas import BankIn, OrganizationOut, OrganizationPatch, PayoutOut, SignupIn, StaffOut, TokenOut
from app.security import create_access_token, new_refresh_token
from app.services import account
from app.services.audit import audit

router = APIRouter(prefix="/account", tags=["account"])


@router.post("/signup", response_model=TokenOut, status_code=201)
def signup(body: SignupIn, response: Response, db: Session = Depends(get_db)) -> TokenOut:
    """Anyone may create an account (commune, State, property manager, owner); its creator is its administrator."""
    try:
        user = account.signup(db, organization_name=body.organization_name, kind=body.kind, full_name=body.full_name,
                              email=body.email, password=body.password, phone=body.phone, locale=body.locale)
    except account.AccountError as exc:
        raise HTTPException(409 if str(exc) == "email_already_used" else 422, str(exc)) from exc
    audit(db, "account.signup", "organizations", user.organization_id, actor_id=user.id, data={"kind": body.kind})
    access, expires_in = create_access_token(user.id, user.role, user.locale)
    refresh, refresh_hash, refresh_expires = new_refresh_token()
    db.add(RefreshToken(staff_user_id=user.id, token_hash=refresh_hash, client_name=body.client_name, expires_at=refresh_expires))
    response.set_cookie(ACCESS_COOKIE, access, max_age=expires_in, httponly=True, samesite="strict",
                        secure=get_settings().cookie_secure)
    return TokenOut(access_token=access, refresh_token=refresh, expires_in=expires_in, user=StaffOut.model_validate(user))


@router.get("", response_model=OrganizationOut)
def get_account(user: StaffUser = Depends(get_current_user)) -> dict:
    return account.organization_out(user_organization(user))


@router.patch("", response_model=OrganizationOut)
def update_account(body: OrganizationPatch, user: StaffUser = Depends(require_role("admin")),
                   db: Session = Depends(get_db)) -> dict:
    org = user_organization(user)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(org, field, value.strip() if isinstance(value, str) else value)
    audit(db, "account.update", "organizations", org.id, actor_id=user.id, data=body.model_dump(exclude_unset=True))
    db.flush()
    return account.organization_out(org)


@router.put("/bank", response_model=OrganizationOut)
def set_bank(body: BankIn, user: StaffUser = Depends(require_role("admin")), db: Session = Depends(get_db)) -> dict:
    """IBAN and BIC of the account that receives the monthly transfer of the rentals and bookings."""
    org = user_organization(user)
    try:
        account.set_bank(db, org, holder=body.holder, iban=body.iban, bic=body.bic, address=body.address)
    except account.AccountError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, "account.bank_update", "organizations", org.id, actor_id=user.id,
          data={"iban": account.mask_iban(org.bank_iban), "bic": org.bank_bic})
    db.flush()
    return account.organization_out(org)


@router.get("/payouts", response_model=list[PayoutOut])
def payouts(user: StaffUser = Depends(require_role("admin")), db: Session = Depends(get_db)) -> list[dict]:
    """Monthly statement per parking for the last 12 months (gross, platform fee, net to transfer)."""
    return account.payouts(db, user_organization(user).id)
