"""Assessments. Any logged-in role may read its own org's assessments; only a
Requester may create or edit one, and only while it is a DRAFT.

    GET   /api/v1/assessments                    page of this org's assessments, newest first
                                                 (?status=, ?limit=, ?cursor=)
    GET   /api/v1/assessments/summary            count per status for this org
    POST  /api/v1/assessments                    {"vendor_id", "title", "control_ids"} -> 201
    GET   /api/v1/assessments/{id}               one assessment with its controls
    PATCH /api/v1/assessments/{id}               {"title"?, "control_ids"?}; DRAFT only, else 409
    GET   /api/v1/assessments/{id}/audit-events  its audit history, oldest first (?limit=, ?cursor=)

No endpoint changes the status yet: that starts with "analyze" in M6, through
app/domain/state_machine.py.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text, tuple_
from sqlmodel import Session, col, select

from app.api.pagination import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, split_page
from app.auth.dependencies import current_user, require_role
from app.auth.tenancy import get_org_row_or_404
from app.db import get_session
from app.domain import audit
from app.errors import ApiError, request_id_of
from app.models import Assessment, AssessmentControl, AuditEvent, SecurityControl, User, Vendor
from app.models.enums import AssessmentStatus, Role

router = APIRouter()

MAX_CONTROLS = 50  # well above the 12 controls that exist; bounds the request size


# --- Request and response models


def no_duplicates(ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if len(set(ids)) != len(ids):
        raise ValueError("control ids must not repeat")
    return ids


# 1 to MAX_CONTROLS security_controls ids, no repeats (otherwise 422).
ControlIds = Annotated[list[uuid.UUID], Field(min_length=1, max_length=MAX_CONTROLS), AfterValidator(no_duplicates)]
Title = Annotated[str, Field(min_length=1, max_length=200)]


class AssessmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    vendor_id: uuid.UUID
    title: Title
    control_ids: ControlIds


class AssessmentUpdate(BaseModel):
    """Both fields optional; a field left out (or null) is not changed."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: Title | None = None
    control_ids: ControlIds | None = None

    @model_validator(mode="after")
    def something_to_change(self) -> "AssessmentUpdate":
        if self.title is None and self.control_ids is None:
            raise ValueError("give title and/or control_ids")
        return self


class ControlOut(BaseModel):
    id: uuid.UUID  # security_controls.id: the id POST/PATCH take in control_ids
    code: str
    title: str


class AssessmentOut(BaseModel):
    id: uuid.UUID
    vendor_id: uuid.UUID
    title: str
    status: AssessmentStatus
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class AssessmentDetail(AssessmentOut):
    controls: list[ControlOut]


class AssessmentPage(BaseModel):
    items: list[AssessmentOut]
    next_cursor: str | None  # pass as ?cursor= for the next page; null on the last page


class StatusSummary(BaseModel):
    counts: dict[AssessmentStatus, int]  # every status, 0 where there are none
    total: int


class AuditEventOut(BaseModel):
    id: uuid.UUID
    action: str
    actor_user_id: uuid.UUID | None
    old_values: dict[str, Any] | None
    new_values: dict[str, Any] | None
    created_at: datetime


class AuditEventPage(BaseModel):
    items: list[AuditEventOut]
    next_cursor: str | None


# --- Helpers


def assessment_out(a: Assessment) -> AssessmentOut:
    return AssessmentOut(
        id=a.id, vendor_id=a.vendor_id, title=a.title, status=a.status,
        created_by_user_id=a.created_by_user_id, created_at=a.created_at, updated_at=a.updated_at,
    )


def controls_of(db: Session, assessment_id: uuid.UUID) -> list[SecurityControl]:
    """The controls in scope for an assessment, by code. The caller has already
    checked that the assessment belongs to the user's org."""
    return list(db.exec(
        select(SecurityControl)
        .join(AssessmentControl, col(AssessmentControl.control_id) == col(SecurityControl.id))
        .where(AssessmentControl.assessment_id == assessment_id)
        .order_by(SecurityControl.code)
    ).all())


def assessment_detail(db: Session, a: Assessment) -> AssessmentDetail:
    controls = [ControlOut(id=c.id, code=c.code, title=c.title) for c in controls_of(db, a.id)]
    return AssessmentDetail(**assessment_out(a).model_dump(), controls=controls)


def load_controls_or_422(db: Session, control_ids: list[uuid.UUID]) -> list[SecurityControl]:
    """security_controls is global reference data, so no org filter here."""
    controls = list(db.exec(select(SecurityControl).where(col(SecurityControl.id).in_(control_ids))).all())
    if len(controls) != len(control_ids):  # ids are unique (validated), so a shortfall = unknown ids
        raise ApiError(422, "unknown_control", "One or more control ids do not exist")
    return controls


def codes(controls: list[SecurityControl]) -> list[str]:
    # Audit events store control codes ("MFA"), readable without a lookup.
    return sorted(c.code for c in controls)


# --- Endpoints. /assessments/summary is declared BEFORE /assessments/{assessment_id}:
# FastAPI tries routes in order, and the {id} route would otherwise try to parse
# "summary" as a UUID and return 422.


@router.get("/assessments")
def list_assessments(
    status: AssessmentStatus | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
) -> AssessmentPage:
    after = decode_cursor(cursor) if cursor is not None else None
    query = assessment_page_query(user.org_id, status, after, limit)
    rows, next_cursor = split_page(list(db.exec(query).all()), limit)
    return AssessmentPage(items=[assessment_out(a) for a in rows], next_cursor=next_cursor)


def assessment_page_query(
    org_id: uuid.UUID,
    status: AssessmentStatus | None,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
):
    """The page query of GET /assessments (keyset: see app/api/pagination.py).

    A separate function so scripts/explain_keyset.py can EXPLAIN exactly this query.
    Served by ix_assessments_org_id_created_at_id, or by
    ix_assessments_org_id_status_created_at_id when status is given (docs/query-plans.md).
    """
    query = select(Assessment).where(Assessment.org_id == org_id)
    if status is not None:
        query = query.where(Assessment.status == status)
    if after is not None:
        # (created_at, id) < (:cursor_created_at, :cursor_id): rows after the cursor, newest first
        query = query.where(tuple_(col(Assessment.created_at), col(Assessment.id)) < tuple_(*after))
    # LIMIT limit + 1: the extra row only tells split_page() whether there is a next page.
    return query.order_by(col(Assessment.created_at).desc(), col(Assessment.id).desc()).limit(limit + 1)


@router.get("/assessments/summary")
def assessment_summary(user: User = Depends(current_user), db: Session = Depends(get_session)) -> StatusSummary:
    rows = db.exec(
        text(
            "SELECT status, count(*) AS n FROM assessments "
            "WHERE org_id = :org_id "
            "GROUP BY status"
        ),
        params={"org_id": user.org_id},
    ).all()
    # GROUP BY only returns statuses that have rows; fill in the rest with 0.
    counts = {s: 0 for s in AssessmentStatus}
    for status, n in rows:
        counts[AssessmentStatus(status)] = n
    return StatusSummary(counts=counts, total=sum(counts.values()))


@router.post("/assessments", status_code=201)
def create_assessment(
    body: AssessmentCreate,
    request: Request,
    user: User = Depends(require_role(Role.REQUESTER)),
    db: Session = Depends(get_session),
) -> AssessmentDetail:
    # Another org's vendor: the same 404 as an unknown id (rule 1).
    vendor = get_org_row_or_404(db, Vendor, body.vendor_id, user)
    controls = load_controls_or_422(db, body.control_ids)

    assessment = Assessment(org_id=user.org_id, vendor_id=vendor.id, created_by_user_id=user.id, title=body.title)
    db.add(assessment)
    db.flush()  # INSERT now: the controls reference it, and created_at comes back
    for control in controls:
        db.add(AssessmentControl(assessment_id=assessment.id, control_id=control.id))

    audit.record(
        db, org_id=user.org_id, actor=user, action="assessment.created", resource_type="assessment",
        resource_id=assessment.id, old=None,
        new={"vendor_id": vendor.id, "title": assessment.title, "status": assessment.status,
             "controls": codes(controls)},
        request_id=request_id_of(request),
    )
    db.commit()  # assessment, its controls and the audit event together
    return assessment_detail(db, assessment)


@router.get("/assessments/{assessment_id}")
def get_assessment(
    assessment_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_session),
) -> AssessmentDetail:
    return assessment_detail(db, get_org_row_or_404(db, Assessment, assessment_id, user))


@router.patch("/assessments/{assessment_id}")
def update_assessment(
    assessment_id: uuid.UUID,
    body: AssessmentUpdate,
    request: Request,
    user: User = Depends(require_role(Role.REQUESTER)),
    db: Session = Depends(get_session),
) -> AssessmentDetail:
    # FOR UPDATE: lock the row before checking the status, so a concurrent
    # DRAFT -> PROCESSING transition waits until this edit has committed (and then
    # analyzes the edited version) instead of slipping in between check and write.
    assessment = get_org_row_or_404(db, Assessment, assessment_id, user, for_update=True)
    if assessment.status != AssessmentStatus.DRAFT:
        raise ApiError(409, "not_editable", "Only a DRAFT assessment can be edited")

    old: dict[str, Any] = {}
    new: dict[str, Any] = {}

    if body.title is not None and body.title != assessment.title:
        old["title"], new["title"] = assessment.title, body.title
        assessment.title = body.title
        db.add(assessment)

    if body.control_ids is not None:
        wanted = load_controls_or_422(db, body.control_ids)
        current = controls_of(db, assessment.id)
        wanted_ids = {c.id for c in wanted}
        current_ids = {c.id for c in current}
        if wanted_ids != current_ids:
            # Replace by difference: delete the removed controls, insert the added ones.
            # (A DRAFT has no AI findings yet, so nothing references these rows.)
            for row in db.exec(
                select(AssessmentControl).where(
                    AssessmentControl.assessment_id == assessment.id,
                    col(AssessmentControl.control_id).in_(current_ids - wanted_ids),
                )
            ).all():
                db.delete(row)
            for control_id in wanted_ids - current_ids:
                db.add(AssessmentControl(assessment_id=assessment.id, control_id=control_id))
            old["controls"], new["controls"] = codes(current), codes(wanted)

    if new:  # something actually changed
        audit.record(
            db, org_id=user.org_id, actor=user, action="assessment.updated", resource_type="assessment",
            resource_id=assessment.id, old=old, new=new, request_id=request_id_of(request),
        )
    db.commit()  # also ends the transaction, releasing the FOR UPDATE lock
    return assessment_detail(db, assessment)


@router.get("/assessments/{assessment_id}/audit-events")
def list_audit_events(
    assessment_id: uuid.UUID,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
) -> AuditEventPage:
    assessment = get_org_row_or_404(db, Assessment, assessment_id, user)  # 404 for another org's id

    # Oldest first: the same keyset as the assessments list, in ascending order.
    query = select(AuditEvent).where(
        AuditEvent.org_id == user.org_id,
        AuditEvent.resource_type == "assessment",
        AuditEvent.resource_id == assessment.id,
    )
    if cursor is not None:
        cursor_created_at, cursor_id = decode_cursor(cursor)
        query = query.where(
            tuple_(col(AuditEvent.created_at), col(AuditEvent.id)) > tuple_(cursor_created_at, cursor_id)
        )
    query = query.order_by(col(AuditEvent.created_at), col(AuditEvent.id)).limit(limit + 1)

    rows, next_cursor = split_page(list(db.exec(query).all()), limit)
    items = [
        AuditEventOut(id=e.id, action=e.action, actor_user_id=e.actor_user_id, old_values=e.old_values,
                      new_values=e.new_values, created_at=e.created_at)
        for e in rows
    ]
    return AuditEventPage(items=items, next_cursor=next_cursor)
