"""Login, logout and "who am I".

    POST /api/v1/auth/login   {"email", "password"} -> sets the session cookie
    POST /api/v1/auth/logout  revokes the session, clears the cookie
    GET  /api/v1/auth/me      the logged-in user, their org and role

CSRF (D-05): the cookie is SameSite=Lax, so browsers do not send it on cross-site
POSTs, and every state-changing endpoint takes a JSON body, which a plain HTML form
on another site cannot produce.
"""

import logging
import uuid

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, func, select

from app.auth.dependencies import current_user
from app.auth.passwords import verify_against_dummy, verify_password
from app.auth.sessions import SESSION_COOKIE, SESSION_TTL, create_session, revoke_session
from app.config import Settings, get_settings
from app.db import get_session
from app.errors import ApiError, request_id_of
from app.models import Organization, User

logger = logging.getLogger("app.auth")
router = APIRouter()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    # Upper bound so nobody can make the server hash a multi-megabyte "password".
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str


class MeResponse(BaseModel):
    user: UserOut
    org: OrgOut


def me_response(db: Session, user: User) -> MeResponse:
    org = db.get(Organization, user.org_id)
    return MeResponse(
        user=UserOut(id=user.id, email=user.email, full_name=user.full_name, role=user.role),
        org=OrgOut(id=org.id, name=org.name),
    )


@router.post("/auth/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MeResponse:
    # lower() on both sides matches the unique index on lower(email).
    user = db.exec(select(User).where(func.lower(User.email) == func.lower(body.email))).first()

    # Exactly one argon2 verify on every path, so the response time is the same
    # whether or not the email exists. is_active is checked only after the verify.
    if user is None:
        verify_against_dummy(body.password)
        reason = "unknown email"
    elif not verify_password(user.password_hash, body.password):
        reason = "wrong password"
    elif not user.is_active:
        reason = "inactive user"
    else:
        reason = None

    if reason is not None:
        # Logged for us; never the email or the password. The client gets one
        # message for all three cases.
        user_id = user.id if user is not None else None
        logger.warning("login failed: %s (user_id=%s, request_id=%s)", reason, user_id, request_id_of(request))
        raise ApiError(401, "invalid_credentials", "Invalid email or password")

    token = create_session(db, user)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
        httponly=True,   # JavaScript cannot read it, so an XSS bug cannot steal it
        samesite="lax",  # not sent on cross-site POSTs (CSRF, D-05)
        secure=settings.session_cookie_secure,
    )
    return me_response(db, user)


@router.post("/auth/logout", status_code=204)
def logout(
    response: Response,
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> None:
    """Always succeeds: logging out without a valid session just clears the cookie."""
    if token:
        revoke_session(db, token)
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=settings.session_cookie_secure,
    )


@router.get("/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_session)) -> MeResponse:
    return me_response(db, user)
