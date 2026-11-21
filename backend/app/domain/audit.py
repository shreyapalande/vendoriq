"""Write one audit event, in the caller's transaction (rule 4).

record() only adds the row to the session; it never commits. The caller commits the
change and its audit row together, so either both are saved or neither is.

What goes into old/new: only the fields that changed, built explicitly by the
caller (e.g. {"name": "Acme"}). Never passwords, session tokens, API keys or
document text. The audit_events table is append-only (trigger, D-21).
"""

import uuid
from typing import Any

from pydantic_core import to_jsonable_python
from sqlmodel import Session

from app.models import AuditEvent, User


def record(
    db: Session,
    *,
    org_id: uuid.UUID,
    actor: User | None,          # None = the system (the worker), not a user
    action: str,                 # "<resource_type>.<verb>", e.g. "vendor.created"
    resource_type: str,          # e.g. "vendor", "assessment"
    resource_id: uuid.UUID,
    old: dict[str, Any] | None,  # None for a create
    new: dict[str, Any] | None,
    request_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        org_id=org_id,
        actor_user_id=actor.id if actor is not None else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        # JSONB needs plain JSON values: UUIDs, datetimes and enums become strings.
        old_values=to_jsonable_python(old) if old is not None else None,
        new_values=to_jsonable_python(new) if new is not None else None,
        request_id=request_id,
    )
    db.add(event)
    return event
