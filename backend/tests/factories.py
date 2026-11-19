"""Small builders for test rows: each adds one row, flushes, and returns it.

Shared by the constraint tests (M3) and the API tests (M4+). Flushing (not
committing) sends the INSERT so the row gets its server defaults and is visible to
later queries in the same test transaction, which the fixture rolls back.
"""

import uuid

from sqlmodel import Session

from app.ai.controls import Label
from app.auth.passwords import hash_password
from app.models import (
    AIFinding,
    Assessment,
    AssessmentControl,
    Document,
    Organization,
    ProcessingJob,
    SecurityControl,
    User,
    Vendor,
)
from app.models.enums import Role

# Password of every user made with make_user(..., password=TEST_PASSWORD).
TEST_PASSWORD = "correct horse battery staple"


def add(session: Session, row):
    session.add(row)
    session.flush()
    return row


def make_org(session: Session, name: str | None = None) -> Organization:
    return add(session, Organization(name=name or f"Org {uuid.uuid4().hex[:8]}"))


def make_user(
    session: Session,
    org: Organization,
    role: Role = Role.REQUESTER,
    email: str | None = None,
    password: str | None = None,
    is_active: bool = True,
) -> User:
    """A user. Pass `password` for a user who can log in; without it the hash is a
    placeholder (argon2 is deliberately slow, and most tests never log in)."""
    return add(session, User(
        org_id=org.id, email=email or f"{uuid.uuid4().hex[:8]}@test.example",
        full_name="Test User", role=role, is_active=is_active,
        password_hash=hash_password(password) if password else "not-a-real-hash",
    ))


def make_vendor(session: Session, org: Organization, name: str | None = None) -> Vendor:
    return add(session, Vendor(org_id=org.id, name=name or f"Vendor {uuid.uuid4().hex[:8]}"))


def make_assessment(session: Session, org: Organization) -> Assessment:
    """An assessment with its own vendor and creator, all in `org`."""
    vendor = make_vendor(session, org)
    creator = make_user(session, org)
    return add(session, Assessment(org_id=org.id, vendor_id=vendor.id, created_by_user_id=creator.id, title="Review"))


def make_assessment_control(session: Session, assessment: Assessment) -> AssessmentControl:
    control = add(session, SecurityControl(code=f"TEST_{uuid.uuid4().hex[:8]}", title="T", description="D"))
    return add(session, AssessmentControl(assessment_id=assessment.id, control_id=control.id))


def make_document(session: Session, assessment: Assessment) -> Document:
    return add(session, Document(
        assessment_id=assessment.id, uploaded_by_user_id=assessment.created_by_user_id,
        original_filename="q.pdf", size_bytes=1000, page_count=3, sha256="0" * 64,
        storage_path="test/q.pdf",
    ))


def make_job(session: Session, assessment: Assessment, document: Document | None = None) -> ProcessingJob:
    document = document or make_document(session, assessment)
    return add(session, ProcessingJob(assessment_id=assessment.id, document_id=document.id))


def make_finding(session: Session, job: ProcessingJob, assessment_control: AssessmentControl) -> AIFinding:
    return add(session, AIFinding(
        job_id=job.id, assessment_control_id=assessment_control.id,
        label=Label.SUPPORTED, reason="Because.", citations=[],
    ))
