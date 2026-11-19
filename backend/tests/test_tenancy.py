"""M4: the tenant-isolation helpers in app/auth/tenancy.py, on every kind of table.

Called directly (no HTTP), with real rows in two orgs: each helper returns the
user's own row and raises the 404 ApiError for the other org's row.
"""

import pytest

from app.auth.tenancy import get_assessment_child_or_404, get_control_child_or_404, get_org_row_or_404
from app.errors import ApiError
from app.models import (
    AIFinding,
    Approval,
    Assessment,
    AssessmentControl,
    Document,
    ProcessingJob,
    ReviewDecision,
    Vendor,
)
from app.models.enums import ApprovalDecision, ReviewDecisionType
from tests.factories import (
    add,
    make_assessment,
    make_assessment_control,
    make_finding,
    make_job,
    make_org,
    make_user,
)

pytestmark = pytest.mark.db


def make_tenant(session):
    """One org with a user and one row in each kind of table."""
    org = make_org(session)
    assessment = make_assessment(session, org)
    control = make_assessment_control(session, assessment)
    job = make_job(session, assessment)
    finding = make_finding(session, job, control)
    user = make_user(session, org)
    return {
        "user": user,
        Vendor: session.get(Vendor, assessment.vendor_id),
        Assessment: assessment,
        AssessmentControl: control,
        Document: session.get(Document, job.document_id),
        ProcessingJob: job,
        Approval: add(session, Approval(assessment_id=assessment.id, approver_user_id=user.id,
                                        decision=ApprovalDecision.APPROVED)),
        AIFinding: finding,
        ReviewDecision: add(session, ReviewDecision(
            assessment_control_id=control.id, ai_finding_id=finding.id, reviewer_user_id=user.id,
            decision=ReviewDecisionType.ACCEPTED, final_label=finding.label,
        )),
    }


CASES = [
    (get_org_row_or_404, Vendor),
    (get_org_row_or_404, Assessment),
    (get_assessment_child_or_404, AssessmentControl),
    (get_assessment_child_or_404, Document),
    (get_assessment_child_or_404, ProcessingJob),
    (get_assessment_child_or_404, Approval),
    (get_control_child_or_404, AIFinding),
    (get_control_child_or_404, ReviewDecision),
]


@pytest.mark.parametrize(("helper", "model"), CASES, ids=lambda x: getattr(x, "__name__", ""))
def test_helper_returns_own_row_and_404s_for_another_orgs_row(session, helper, model):
    org_a, org_b = make_tenant(session), make_tenant(session)

    assert helper(session, model, org_a[model].id, org_a["user"]).id == org_a[model].id

    with pytest.raises(ApiError) as exc_info:
        helper(session, model, org_a[model].id, org_b["user"])
    assert (exc_info.value.status_code, exc_info.value.code) == (404, "not_found")
