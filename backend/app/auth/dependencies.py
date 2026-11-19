"""FastAPI dependencies for authentication (who are you?) and roles (may you?).

Authorization is enforced here, on the backend only; the frontend may hide buttons
but is never trusted.
"""

from collections.abc import Callable

from fastapi import Cookie, Depends
from sqlmodel import Session, col, select

from app.auth.sessions import SESSION_COOKIE, hash_token, utc_now
from app.db import get_session
from app.errors import ApiError
from app.models import User, UserSession
from app.models.enums import Role


def current_user(
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_session),
) -> User:
    """The logged-in user, or 401.

    One query checks everything: the token's hash matches a session that is not
    revoked and not expired, and its user is still active. Every failure gives the
    same 401, so a client cannot tell an expired session from a revoked one.
    """
    if not token:
        raise ApiError(401, "unauthorized", "Not authenticated")
    user = db.exec(
        select(User)
        .join(UserSession, col(UserSession.user_id) == col(User.id))
        .where(
            col(UserSession.token_hash) == hash_token(token),
            col(UserSession.revoked_at).is_(None),
            col(UserSession.expires_at) > utc_now(),
            col(User.is_active).is_(True),
        )
    ).first()
    if user is None:
        raise ApiError(401, "unauthorized", "Not authenticated")
    return user


def require_role(*roles: Role) -> Callable[..., User]:
    """A dependency that allows only the given roles; others get 403.

    Usage: user: User = Depends(require_role(Role.REVIEWER))
    Not logged in at all is still 401 (from current_user), checked first.
    """

    def check_role(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise ApiError(403, "forbidden", "Your role does not allow this action")
        return user

    return check_role
