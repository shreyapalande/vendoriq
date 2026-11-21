"""Load one tenant-owned row by id, only if it belongs to the current user's org.

Every endpoint that takes a resource id in the URL loads it through one of these.
Another org's row gets the same 404 as an id that does not exist, never 403: a 403
would confirm that the id exists, and that is already a leak about another tenant.

Which function to use depends on where the table gets its org from (D-20):
- get_org_row_or_404:          tables with their own org_id (vendors, assessments)
- get_assessment_child_or_404: tables with assessment_id (documents,
                               assessment_controls, processing_jobs, approvals)
- get_control_child_or_404:    tables with assessment_control_id (ai_findings,
                               review_decisions), two joins away from org_id

The org filter is part of the SQL WHERE clause, not a check in Python after
loading, so a row from another org is never even read into memory.

List endpoints filter with `.where(Model.org_id == user.org_id)` (or the same joins)
directly in their own query.
"""

import uuid

from sqlmodel import Session, SQLModel, col, select

from app.errors import ApiError
from app.models import Assessment, AssessmentControl, User


def not_found() -> ApiError:
    # The same body for "no such id" and "another org's id"; never echoes the id.
    return ApiError(404, "not_found", "Not found")


def get_org_row_or_404[M: SQLModel](
    db: Session, model: type[M], row_id: uuid.UUID, user: User, for_update: bool = False,
) -> M:
    """for_update=True adds FOR UPDATE: the row stays locked until the transaction
    ends, so a check like "status is DRAFT" cannot be invalidated by a concurrent
    transition before this transaction commits.

    populate_existing: if this Session already holds the object, overwrite its
    attributes with the row just read under the lock. Without it SQLAlchemy keeps
    the values it loaded earlier, and the check would run on stale data."""
    query = select(model).where(model.id == row_id, model.org_id == user.org_id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.exec(query).first()
    if row is None:
        raise not_found()
    return row


def get_assessment_child_or_404[M: SQLModel](db: Session, model: type[M], row_id: uuid.UUID, user: User) -> M:
    row = db.exec(
        select(model)
        .join(Assessment, col(Assessment.id) == model.assessment_id)
        .where(model.id == row_id, col(Assessment.org_id) == user.org_id)
    ).first()
    if row is None:
        raise not_found()
    return row


def get_control_child_or_404[M: SQLModel](db: Session, model: type[M], row_id: uuid.UUID, user: User) -> M:
    row = db.exec(
        select(model)
        .join(AssessmentControl, col(AssessmentControl.id) == model.assessment_control_id)
        .join(Assessment, col(Assessment.id) == col(AssessmentControl.assessment_id))
        .where(model.id == row_id, col(Assessment.org_id) == user.org_id)
    ).first()
    if row is None:
        raise not_found()
    return row
