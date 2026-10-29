"""FastAPI dependencies: database, current staff user, roles, site scope, controller."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Controller, Customer, Site, StaffUser
from app.security import controller_key_matches, decode_access_token, decode_customer_access_token, split_controller_key

ACCESS_COOKIE = "pk_access"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Role hierarchy used by require_role
ROLE_RANK = {"viewer": 0, "agent": 1, "manager": 2, "admin": 3}


def _unauthorized(detail: str = "not_authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(request: Request, db: Session = Depends(get_db)) -> StaffUser:
    """Accept a Bearer token (Android app, scripts) or the website cookie.

    Cookie-authenticated writes must carry "X-Requested-With: fetch": a cross-site
    form cannot set that header, which protects the website against CSRF.
    """
    token = None
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    elif ACCESS_COOKIE in request.cookies:
        token = request.cookies[ACCESS_COOKIE]
        if request.method in UNSAFE_METHODS and request.headers.get("X-Requested-With") != "fetch":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="csrf_header_missing")
    if not token:
        raise _unauthorized()
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("token_invalid") from exc
    user = db.get(StaffUser, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise _unauthorized("user_inactive")
    request.state.user = user
    return user


def require_role(minimum: str) -> Callable[[StaffUser], StaffUser]:
    def checker(user: StaffUser = Depends(get_current_user)) -> StaffUser:
        if ROLE_RANK[user.role] < ROLE_RANK[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="insufficient_role")
        return user

    return checker


# What each role may do (shown on the "Management team" screen and used by require_permission)
PERMISSIONS: dict[str, tuple[str, ...]] = {
    "view_dashboard": ("admin", "manager", "agent", "viewer"),
    "acknowledge_alarm": ("admin", "manager", "agent"),
    "issue_fine": ("admin", "manager", "agent"),
    "manage_bookings_subscriptions": ("admin", "manager"),
    "edit_customers": ("admin", "manager"),
    "change_prices": ("admin", "manager"),
    "download_reports": ("admin", "manager", "viewer"),
    "change_settings": ("admin",),
    "manage_team": ("admin",),
}


def require_permission(permission: str) -> Callable[[StaffUser], StaffUser]:
    allowed = PERMISSIONS[permission]

    def checker(user: StaffUser = Depends(get_current_user)) -> StaffUser:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="permission_denied")
        return user

    return checker


def allowed_site_ids(user: StaffUser) -> set[uuid.UUID] | None:
    """None means every site (administrators, or staff without restrictions)."""
    if user.role == "admin" or not user.sites:
        return None
    return {s.id for s in user.sites}


def check_site_access(user: StaffUser, site_id: uuid.UUID) -> None:
    allowed = allowed_site_ids(user)
    if allowed is not None and site_id not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="site_not_allowed")


def get_site_or_404(db: Session, site_id: uuid.UUID, user: StaffUser) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="site_not_found")
    check_site_access(user, site.id)
    return site


def get_current_controller(
    request: Request,
    x_controller_key: str = Header(..., alias="X-Controller-Key"),
    db: Session = Depends(get_db),
) -> Controller:
    """Authenticate a Raspberry Pi control cabinet by its API key."""
    prefix = split_controller_key(x_controller_key)
    if prefix is None:
        raise _unauthorized("controller_key_invalid")
    controller = db.scalar(select(Controller).where(Controller.api_key_prefix == prefix))
    if controller is None or not controller.is_active or not controller_key_matches(x_controller_key, controller.api_key_hash):
        raise _unauthorized("controller_key_invalid")
    controller.last_seen_at = datetime.now(UTC)
    if request.client:
        controller.ip_address = request.client.host
    return controller


def get_current_customer(request: Request, db: Session = Depends(get_db)) -> Customer:
    """Driver of the Android app: "Authorization: Bearer <token>" with a token of type "customer"."""
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise _unauthorized()
    try:
        payload = decode_customer_access_token(auth[7:].strip())
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("token_invalid") from exc
    customer = db.get(Customer, uuid.UUID(payload["sub"]))
    if customer is None or customer.password_hash is None or customer.anonymised_at is not None:
        raise _unauthorized("user_inactive")
    request.state.customer = customer
    return customer
