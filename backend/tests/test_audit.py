"""M5: app/domain/audit.py. The API and state-machine tests check which events are
written; these check record() itself."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain import audit
from app.models.enums import AssessmentStatus
from tests.conftest import audit_events_for
from tests.factories import make_org, make_user

pytestmark = pytest.mark.db


def test_record_stores_plain_json_and_does_not_commit(session):
    org = make_org(session)
    actor = make_user(session, org)
    resource_id, vendor_id = uuid.uuid4(), uuid.uuid4()

    event = audit.record(
        session, org_id=org.id, actor=actor, action="assessment.updated", resource_type="assessment",
        resource_id=resource_id, old={"status": AssessmentStatus.DRAFT}, new={"vendor_id": vendor_id},
    )
    assert event in session.new  # added to the caller's transaction, not yet written
    session.flush()

    [stored] = audit_events_for(session, resource_id)
    # The enum and the UUID were stored as plain JSON strings.
    assert stored.old_values == {"status": "DRAFT"}
    assert stored.new_values == {"vendor_id": str(vendor_id)}
    assert stored.actor_user_id == actor.id


def test_audit_events_cannot_be_edited_afterwards(session):
    """D-21, through the same table record() writes to: the append-only trigger."""
    org = make_org(session)
    resource_id = uuid.uuid4()
    audit.record(session, org_id=org.id, actor=None, action="vendor.created", resource_type="vendor",
                 resource_id=resource_id, old=None, new={"name": "X"})
    session.flush()

    with pytest.raises(DBAPIError, match="append-only"), session.begin_nested():
        session.exec(text("UPDATE audit_events SET action = 'tampered' WHERE resource_id = :id"),
                     params={"id": resource_id})
