"""Assessment status transitions (rule 3).

    DRAFT ──► PROCESSING ──► IN_REVIEW ──► PENDING_APPROVAL ──► APPROVED
                 ▲   │                                    └───► REJECTED
                 │   ▼
          PROCESSING_FAILED

APPROVED and REJECTED are terminal. PROCESSING_FAILED -> PROCESSING is a retry.

Every status change goes through transition(). It is one conditional UPDATE:

    UPDATE assessments SET status = :to
    WHERE id = :id AND org_id = :org_id AND status = :from
    RETURNING id, status, updated_at

If another request already changed the status, the WHERE clause no longer matches,
no row comes back, and the caller gets 409. Why two concurrent calls cannot both
succeed: the second UPDATE waits for the first one's row lock; when the first
commits, Postgres (READ COMMITTED) re-checks the WHERE clause against the new row,
finds status != :from, and updates nothing.

transition() never commits. It writes the audit event in the same transaction, and
the caller commits both together.
"""

import uuid

from sqlalchemy import Row, text
from sqlmodel import Session

from app.domain import audit
from app.errors import ApiError
from app.models import User
from app.models.enums import AssessmentStatus as S

ALLOWED_TRANSITIONS: dict[S, set[S]] = {
    S.DRAFT: {S.PROCESSING},
    S.PROCESSING: {S.IN_REVIEW, S.PROCESSING_FAILED},
    S.PROCESSING_FAILED: {S.PROCESSING},
    S.IN_REVIEW: {S.PENDING_APPROVAL},
    S.PENDING_APPROVAL: {S.APPROVED, S.REJECTED},
    S.APPROVED: set(),
    S.REJECTED: set(),
}


def invalid_transition(from_status: S, to_status: S) -> ApiError:
    return ApiError(409, "invalid_transition", f"Cannot change status from {from_status} to {to_status}")


def transition(
    db: Session,
    assessment_id: uuid.UUID,
    org_id: uuid.UUID,
    from_status: S,
    to_status: S,
    actor: User | None,
    request_id: str | None = None,
) -> Row:
    """Move one assessment from `from_status` to `to_status`, or raise 409.

    Returns the updated row (id, status, updated_at). Note: plain SQL bypasses the
    ORM, so an Assessment object already loaded in this session keeps its old status
    until it is refreshed.
    """
    # Rejected before touching the database: a pair that is not in the table is
    # never valid, whatever the row currently says.
    if to_status not in ALLOWED_TRANSITIONS[from_status]:
        raise invalid_transition(from_status, to_status)

    row = db.exec(
        text(
            "UPDATE assessments SET status = :to_status "
            "WHERE id = :id AND org_id = :org_id AND status = :from_status "
            "RETURNING id, status, updated_at"
        ),
        params={"to_status": to_status.value, "id": assessment_id, "org_id": org_id,
                "from_status": from_status.value},
    ).first()
    if row is None:
        # The assessment is not in this org, does not exist, or is no longer in
        # from_status. One answer for all three: no detail about other tenants.
        raise invalid_transition(from_status, to_status)

    audit.record(
        db, org_id=org_id, actor=actor, action="assessment.status_changed",
        resource_type="assessment", resource_id=assessment_id,
        old={"status": from_status}, new={"status": to_status}, request_id=request_id,
    )
    return row
