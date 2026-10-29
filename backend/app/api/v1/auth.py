"""Authentication for the website and the Android app.

Android: POST /auth/login -> keep access_token in memory and refresh_token in
EncryptedSharedPreferences; call POST /auth/refresh when a request returns 401
"token_expired". Refresh tokens rotate: the old one stops working after each refresh.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import ACCESS_COOKIE, get_current_user
from app.models import PushDevice, RefreshToken, StaffUser
from app.schemas import LoginIn, PushTokenIn, RefreshIn, StaffOut, TokenOut
from app.security import create_access_token, new_refresh_token, sha256_hex, verify_password
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue(db: Session, user: StaffUser, client_name: str | None, response: Response | None) -> TokenOut:
    access, expires_in = create_access_token(user.id, user.role, user.locale)
    refresh, refresh_hash, refresh_expires = new_refresh_token()
    db.add(RefreshToken(staff_user_id=user.id, token_hash=refresh_hash, client_name=client_name, expires_at=refresh_expires))
    if response is not None:
        response.set_cookie(
            ACCESS_COOKIE, access, max_age=expires_in, httponly=True, samesite="strict", secure=get_settings().cookie_secure
        )
    return TokenOut(access_token=access, refresh_token=refresh, expires_in=expires_in, user=StaffOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)) -> TokenOut:
    user = db.scalar(select(StaffUser).where(func.lower(StaffUser.email) == body.email.strip().lower()))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
    user.last_login_at = datetime.now(UTC)
    audit(db, "auth.login", "staff_users", user.id, actor_id=user.id, data={"client": body.client_name})
    return _issue(db, user, body.client_name, response)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, response: Response, db: Session = Depends(get_db)) -> TokenOut:
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(body.refresh_token)))
    now = datetime.now(UTC)
    if token is None or token.revoked_at is not None or token.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh_token_invalid")
    user = db.get(StaffUser, token.staff_user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="user_inactive")
    token.revoked_at = now  # rotation
    return _issue(db, user, token.client_name, response)


@router.post("/logout", status_code=204)
def logout(body: RefreshIn, response: Response, db: Session = Depends(get_db)) -> Response:
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(body.refresh_token)))
    if token is not None and token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
    response.delete_cookie(ACCESS_COOKIE)
    response.status_code = 204
    return response


@router.get("/me", response_model=StaffOut)
def me(user: StaffUser = Depends(get_current_user)) -> StaffUser:
    return user


@router.post("/push-token", status_code=204)
def register_push_token(body: PushTokenIn, user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    """Register the Firebase Cloud Messaging token of the Android app."""
    row = db.scalar(select(PushDevice).where(PushDevice.token == body.token))
    if row is None:
        db.add(PushDevice(staff_user_id=user.id, platform=body.platform, token=body.token, last_used_at=datetime.now(UTC)))
    else:
        row.staff_user_id = user.id
        row.last_used_at = datetime.now(UTC)
    return Response(status_code=204)
