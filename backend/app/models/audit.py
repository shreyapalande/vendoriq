"""The audit log. Append-only: a database trigger rejects UPDATE, DELETE and TRUNCATE
(migration 0002)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKeyConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.common import created_at_field, id_field


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_events"
    __table_args__ = (
        # The actor must be a user of the same org. NULL actor (the worker/system)
        # is allowed: a composite FK with any NULL column is not checked (MATCH SIMPLE).
        ForeignKeyConstraint(
            ["actor_user_id", "org_id"], ["users.id", "users.org_id"],
            name="fk_audit_events_actor_same_org", ondelete="RESTRICT",
        ),
        # History of one resource, in order.
        Index(
            "ix_audit_events_org_id_resource_type_resource_id_created_at",
            "org_id", "resource_type", "resource_id", "created_at",
        ),
    )

    id: uuid.UUID = id_field()
    org_id: uuid.UUID = Field(foreign_key="organizations.id", ondelete="RESTRICT")
    actor_user_id: uuid.UUID | None = None  # FK via fk_audit_events_actor_same_org
    action: str = Field(max_length=100)         # e.g. "assessment.created"
    resource_type: str = Field(max_length=50)   # e.g. "assessment"
    resource_id: uuid.UUID                      # no FK: may point at any table
    # Changed fields only. Never passwords, tokens, keys or document text (rule 4).
    old_values: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    new_values: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    request_id: str | None = Field(default=None, max_length=64)  # links to the API log line
    created_at: datetime | None = created_at_field()
