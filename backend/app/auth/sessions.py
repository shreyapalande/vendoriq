"""Login sessions: an opaque random token in a cookie, its SHA-256 in user_sessions.

The browser holds the token; the database holds only its hash. Someone who reads
the user_sessions table (a backup, a SQL injection) gets hashes that cannot be
turned back into cookies. A plain SHA-256 is enough here (unlike passwords): the
token is 32 random bytes, so there is nothing to guess or brute-force.

Times come from Python's clock, both when a session is created and when it is
checked. Postgres now() would be the start time of the current transaction, which
stays fixed for a whole test (each test runs in one rolled-back transaction).
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, update

from app.models import User, UserSession

SESSION_COOKIE = "vendoriq_session"
SESSION_TTL = timedelta(hours=8)  # fixed from login; not extended by activity


def utc_now() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    """Store a new session for the user and return the raw token (for the cookie only)."""
    token = secrets.token_urlsafe(32)  # 32 random bytes from the OS CSPRNG
    db.add(UserSession(user_id=user.id, token_hash=hash_token(token), expires_at=utc_now() + SESSION_TTL))
    db.commit()
    return token


def revoke_session(db: Session, token: str) -> None:
    """Mark the session revoked. Does nothing if the token is unknown or already revoked."""
    db.exec(
        update(UserSession)
        .where(col(UserSession.token_hash) == hash_token(token), col(UserSession.revoked_at).is_(None))
        .values(revoked_at=utc_now())
    )
    db.commit()
