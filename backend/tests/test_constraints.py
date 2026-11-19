"""The database enforces the schema's rules on its own, whatever the application does.

Each test tries to break one rule and asserts the exact constraint that stopped it
(not just "some error"), and where useful also shows the valid case is allowed.
Violations run inside a SAVEPOINT (session.begin_nested()), so the test transaction
stays usable after the error and can continue.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel import Session, text

from app.ai.controls import Label
from app.models import AIFinding, Approval, Assessment, AssessmentControl, AuditEvent, ReviewDecision, UserSession
from app.models.enums import ApprovalDecision, ReviewDecisionType
from tests.factories import (
    add,
    make_assessment,
    make_assessment_control,
    make_document,
    make_finding,
    make_job,
    make_org,
    make_user,
    make_vendor,
)

pytestmark = pytest.mark.db


@contextmanager
def violates(session: Session, constraint: str) -> Iterator[None]:
    """Expect the block's writes to fail on exactly `constraint`."""
    with pytest.raises(IntegrityError) as exc_info:
        with session.begin_nested():  # rolled back to here on error
            yield
    assert exc_info.value.orig.diag.constraint_name == constraint


# --- Uniqueness


def test_vendor_name_is_unique_per_org_ignoring_case(session):
    org_a, org_b = make_org(session), make_org(session)
    make_vendor(session, org_a, "Acme")
    make_vendor(session, org_b, "ACME")  # allowed: a different org
    with violates(session, "uq_vendors_org_id_lower_name"):
        make_vendor(session, org_a, "aCmE")


def test_user_email_is_unique_ignoring_case(session):
    org_a, org_b = make_org(session), make_org(session)
    make_user(session, org_a, email="alice@test.example")
    with violates(session, "uq_users_lower_email"):
        make_user(session, org_b, email="Alice@TEST.example")  # unique across all orgs


def test_organization_name_is_unique_ignoring_case(session):
    make_org(session, "Northwind Test")
    with violates(session, "uq_organizations_lower_name"):
        make_org(session, "NORTHWIND TEST")


def test_only_one_queued_or_running_job_per_assessment(session):
    assessment = make_assessment(session, make_org(session))
    first = make_job(session, assessment)
    with violates(session, "uq_processing_jobs_one_active_per_assessment"):
        make_job(session, assessment)

    # Once the first job has finished, a new one may be queued (finished jobs are
    # outside the partial index).
    session.exec(text("UPDATE processing_jobs SET status = 'SUCCEEDED' WHERE id = :id"), params={"id": first.id})
    make_job(session, assessment)


def test_a_control_appears_once_per_assessment(session):
    assessment = make_assessment(session, make_org(session))
    ac = make_assessment_control(session, assessment)
    with violates(session, "uq_assessment_controls_assessment_id_control_id"):
        add(session, AssessmentControl(assessment_id=assessment.id, control_id=ac.control_id))


def test_one_finding_per_job_and_control(session):
    assessment = make_assessment(session, make_org(session))
    ac = make_assessment_control(session, assessment)
    job = make_job(session, assessment)
    make_finding(session, job, ac)
    with violates(session, "uq_ai_findings_job_id_assessment_control_id"):
        make_finding(session, job, ac)


def test_one_review_decision_per_control(session):
    assessment = make_assessment(session, make_org(session))
    ac = make_assessment_control(session, assessment)
    finding = make_finding(session, make_job(session, assessment), ac)

    def decision():
        return ReviewDecision(
            assessment_control_id=ac.id, ai_finding_id=finding.id,
            reviewer_user_id=assessment.created_by_user_id,
            decision=ReviewDecisionType.ACCEPTED, final_label=Label.SUPPORTED,
        )

    add(session, decision())
    with violates(session, "uq_review_decisions_assessment_control_id"):
        add(session, decision())


def test_one_approval_per_assessment(session):
    assessment = make_assessment(session, make_org(session))

    def approval():
        return Approval(assessment_id=assessment.id, approver_user_id=assessment.created_by_user_id,
                        decision=ApprovalDecision.APPROVED)

    add(session, approval())
    with violates(session, "uq_approvals_assessment_id"):
        add(session, approval())


# --- Composite foreign keys (consistency across tenants and parents)


def test_assessment_cannot_point_at_another_orgs_vendor(session):
    org_a, org_b = make_org(session), make_org(session)
    creator_a = make_user(session, org_a)
    vendor_b = make_vendor(session, org_b)
    with violates(session, "fk_assessments_vendor_same_org"):
        add(session, Assessment(org_id=org_a.id, vendor_id=vendor_b.id, created_by_user_id=creator_a.id, title="X"))


def test_assessment_creator_must_be_in_the_same_org(session):
    org_a, org_b = make_org(session), make_org(session)
    vendor_a = make_vendor(session, org_a)
    user_b = make_user(session, org_b)
    with violates(session, "fk_assessments_creator_same_org"):
        add(session, Assessment(org_id=org_a.id, vendor_id=vendor_a.id, created_by_user_id=user_b.id, title="X"))


def test_audit_actor_must_be_in_the_same_org_or_null(session):
    org_a, org_b = make_org(session), make_org(session)
    user_b = make_user(session, org_b)

    def event(actor_id):
        return AuditEvent(org_id=org_a.id, actor_user_id=actor_id, action="test.event",
                          resource_type="organization", resource_id=org_a.id)

    add(session, event(None))  # allowed: system/worker event with no user
    with violates(session, "fk_audit_events_actor_same_org"):
        add(session, event(user_b.id))


def test_job_document_must_belong_to_the_jobs_assessment(session):
    org = make_org(session)
    assessment_1, assessment_2 = make_assessment(session, org), make_assessment(session, org)
    document_of_2 = make_document(session, assessment_2)
    with violates(session, "fk_processing_jobs_document_same_assessment"):
        make_job(session, assessment_1, document_of_2)


def test_review_decision_finding_must_be_for_the_same_control(session):
    assessment = make_assessment(session, make_org(session))
    ac_1, ac_2 = make_assessment_control(session, assessment), make_assessment_control(session, assessment)
    finding_for_2 = make_finding(session, make_job(session, assessment), ac_2)
    with violates(session, "fk_review_decisions_finding_same_control"):
        add(session, ReviewDecision(
            assessment_control_id=ac_1.id, ai_finding_id=finding_for_2.id,
            reviewer_user_id=assessment.created_by_user_id,
            decision=ReviewDecisionType.ACCEPTED, final_label=Label.SUPPORTED,
        ))


# --- CHECK constraints


def test_invalid_assessment_status_is_rejected(session):
    assessment = make_assessment(session, make_org(session))
    with violates(session, "ck_assessments_status"):
        session.exec(text("UPDATE assessments SET status = 'DONE' WHERE id = :id"), params={"id": assessment.id})


def test_invalid_label_is_rejected(session):
    assessment = make_assessment(session, make_org(session))
    ac = make_assessment_control(session, assessment)
    with violates(session, "ck_ai_findings_label"):
        add(session, AIFinding(job_id=make_job(session, assessment).id, assessment_control_id=ac.id,
                               label="PROBABLY_FINE", reason="r"))


def test_running_job_must_have_a_lock(session):
    job = make_job(session, make_assessment(session, make_org(session)))
    with violates(session, "ck_processing_jobs_running_iff_locked"):
        session.exec(text("UPDATE processing_jobs SET status = 'RUNNING' WHERE id = :id"), params={"id": job.id})
    # With a lock owner and lease it is allowed.
    session.exec(text(
        "UPDATE processing_jobs SET status = 'RUNNING', locked_by = 'worker-1', "
        "locked_until = now() + interval '5 minutes' WHERE id = :id"
    ), params={"id": job.id})


def test_citations_must_be_a_json_array(session):
    assessment = make_assessment(session, make_org(session))
    ac = make_assessment_control(session, assessment)
    job = make_job(session, assessment)
    with violates(session, "ck_ai_findings_citations_is_array"):
        add(session, AIFinding(job_id=job.id, assessment_control_id=ac.id, label=Label.SUPPORTED,
                               reason="r", citations={"page": 1}))


@pytest.mark.parametrize("comment", [None, "", "   "])
def test_override_needs_a_comment(session, comment):
    assessment = make_assessment(session, make_org(session))
    ac = make_assessment_control(session, assessment)
    finding = make_finding(session, make_job(session, assessment), ac)
    with violates(session, "ck_review_decisions_override_needs_comment"):
        add(session, ReviewDecision(
            assessment_control_id=ac.id, ai_finding_id=finding.id,
            reviewer_user_id=assessment.created_by_user_id,
            decision=ReviewDecisionType.OVERRIDDEN, final_label=Label.MISSING_EVIDENCE, comment=comment,
        ))


# --- ON DELETE behaviour


def test_business_records_block_deleting_their_parent(session):
    assessment = make_assessment(session, make_org(session))
    with violates(session, "fk_assessments_vendor_same_org"):
        session.exec(text("DELETE FROM vendors WHERE id = :id"), params={"id": assessment.vendor_id})


def test_deleting_a_user_deletes_their_sessions(session):
    user = make_user(session, make_org(session))
    add(session, UserSession(user_id=user.id, token_hash="a" * 64,
                             expires_at=session.exec(text("SELECT now() + interval '1 day'")).one()[0]))
    session.exec(text("DELETE FROM users WHERE id = :id"), params={"id": user.id})
    remaining = session.exec(text("SELECT count(*) FROM user_sessions WHERE user_id = :id"), params={"id": user.id})
    assert remaining.one()[0] == 0


# --- Triggers


@pytest.mark.parametrize("statement", [
    "UPDATE audit_events SET action = 'tampered' WHERE id = :id",
    "DELETE FROM audit_events WHERE id = :id",
    "TRUNCATE audit_events",
])
def test_audit_events_are_append_only(session, statement):
    org = make_org(session)
    event = add(session, AuditEvent(org_id=org.id, action="test.event",
                                    resource_type="organization", resource_id=org.id))
    with pytest.raises(DBAPIError) as exc_info:
        with session.begin_nested():
            session.exec(text(statement), params={"id": event.id})
    assert exc_info.value.orig.sqlstate == "P0001"  # raised by the trigger function
    assert "audit_events is append-only" in str(exc_info.value.orig)
    assert session.exec(text("SELECT action FROM audit_events WHERE id = :id"), params={"id": event.id}).one()[0] == "test.event"


def test_plain_sql_update_sets_updated_at(session):
    """The state machine will use text() UPDATEs, which an ORM onupdate never sees.

    Insert with an old updated_at (a BEFORE UPDATE trigger does not fire on INSERT),
    then UPDATE another column with plain SQL. Also try to set updated_at back by
    hand: the trigger overrides it. now() is the transaction's start time.
    """
    org = make_org(session)
    creator = make_user(session, org)
    vendor = make_vendor(session, org)
    assessment_id = session.exec(text(
        "INSERT INTO assessments (org_id, vendor_id, created_by_user_id, title, status, updated_at) "
        "VALUES (:org, :vendor, :creator, 'T', 'DRAFT', '2000-01-01T00:00:00Z') RETURNING id"
    ), params={"org": org.id, "vendor": vendor.id, "creator": creator.id}).one()[0]

    def updated_at_is_now() -> bool:
        return session.exec(text("SELECT updated_at = now() FROM assessments WHERE id = :id"),
                            params={"id": assessment_id}).one()[0]

    assert not updated_at_is_now()
    session.exec(text("UPDATE assessments SET status = 'PROCESSING' WHERE id = :id"), params={"id": assessment_id})
    assert updated_at_is_now()

    session.exec(text("UPDATE assessments SET updated_at = '2000-01-01T00:00:00Z' WHERE id = :id"),
                 params={"id": assessment_id})
    assert updated_at_is_now()
