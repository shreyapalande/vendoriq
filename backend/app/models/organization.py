"""Organizations (tenants), their users, and login sessions."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from app.models.common import created_at_field, enum_check, id_field, updated_at_field
from app.models.enums import Role


class Organization(SQLModel, table=True):
    __tablename__ = "organizations"
    __table_args__ = (
        # Case-insensitive unique name; also the natural key the seed looks orgs up by.
        Index("uq_organizations_lower_name", text("lower(name)"), unique=True),
    )

    id: uuid.UUID = id_field()
    name: str = Field(max_length=200)
    created_at: datetime | None = created_at_field()


class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        enum_check("role", Role),
        # Email is the login and is unique across all orgs, ignoring case.
        Index("uq_users_lower_email", text("lower(email)"), unique=True),
        # Target of the composite FKs from assessments and audit_events: lets those
        # tables prove that the user they point at is in the same org as the row.
        UniqueConstraint("id", "org_id"),
    )

    id: uuid.UUID = id_field()
    org_id: uuid.UUID = Field(foreign_key="organizations.id", ondelete="RESTRICT")
    email: str = Field(max_length=320)
    password_hash: str = Field(max_length=255)  # argon2id; never the password itself
    full_name: str = Field(max_length=200)
    role: str = Field(max_length=32)  # Role
    # Users are deactivated, never deleted: audit events and decisions point at them.
    is_active: bool = Field(default=True, sa_column_kwargs={"server_default": text("true")})
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()


class UserSession(SQLModel, table=True):
    """A login session. Only the SHA-256 of the session token is stored, so a leaked
    table does not leak usable tokens (used from M4)."""

    __tablename__ = "user_sessions"

    id: uuid.UUID = id_field()
    # CASCADE: a session is not a business record and is meaningless without its user.
    user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    token_hash: str = Field(max_length=64, unique=True)  # looked up on every request
    created_at: datetime | None = created_at_field()
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
