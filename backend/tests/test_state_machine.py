"""M5: app/domain/state_machine.py, called directly (no endpoint triggers transitions yet).

Most tests use the rolled-back `session` fixture. The two concurrency tests need
COMMITTED rows on separate connections, so they use the `committed_draft` fixture,
which commits its own rows and deletes them afterwards.
"""

import threading
import time
import uuid

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.domain.state_machine import ALLOWED_TRANSITIONS, transition
from app.errors import ApiError
from app.models import Assessment
from app.models.enums import AssessmentStatus as S
from tests.conftest import audit_events_for
from tests.factories import make_assessment, make_org, make_user, make_vendor

pytestmark = pytest.mark.db

ALLOWED = [(a, b) for a, targets in ALLOWED_TRANSITIONS.items() for b in sorted(targets)]
NOT_ALLOWED = [(a, b) for a in S for b in S if b not in ALLOWED_TRANSITIONS[a]]


def test_the_table_matches_the_diagram():
    assert set(ALLOWED_TRANSITIONS) == set(S)  # every status has an entry
    assert set(ALLOWED) == {
        (S.DRAFT, S.PROCESSING),
        (S.PROCESSING, S.IN_REVIEW), (S.PROCESSING, S.PROCESSING_FAILED),
        (S.PROCESSING_FAILED, S.PROCESSING),
        (S.IN_REVIEW, S.PENDING_APPROVAL),
        (S.PENDING_APPROVAL, S.APPROVED), (S.PENDING_APPROVAL, S.REJECTED),
    }
    assert ALLOWED_TRANSITIONS[S.APPROVED] == ALLOWED_TRANSITIONS[S.REJECTED] == set()  # terminal


def status_in_db(session, assessment_id) -> str:
    return session.exec(text("SELECT status FROM assessments WHERE id = :id"), params={"id": assessment_id}).one()[0]


@pytest.mark.parametrize(("from_status", "to_status"), ALLOWED, ids=[f"{a}->{b}" for a, b in ALLOWED])
def test_allowed_transition_updates_the_row_and_writes_an_audit_event(session, from_status, to_status):
    org = make_org(session)
    actor = make_user(session, org)
    assessment = make_assessment(session, org, status=from_status)

    row = transition(session, assessment.id, org.id, from_status, to_status, actor, request_id="req-1")

    assert (row.id, row.status) == (assessment.id, to_status.value)
    assert status_in_db(session, assessment.id) == to_status.value
    session.flush()
    [event] = audit_events_for(session, assessment.id)
    assert (event.action, event.resource_type, event.org_id, event.actor_user_id, event.request_id) == (
        "assessment.status_changed", "assessment", org.id, actor.id, "req-1")
    assert (event.old_values, event.new_values) == ({"status": from_status.value}, {"status": to_status.value})


@pytest.mark.parametrize(("from_status", "to_status"), NOT_ALLOWED, ids=[f"{a}->{b}" for a, b in NOT_ALLOWED])
def test_transition_not_in_the_table_is_rejected_before_touching_the_database(from_status, to_status):
    # db=None: any use of the database would raise AttributeError instead of 409.
    with pytest.raises(ApiError) as exc_info:
        transition(None, uuid.uuid4(), uuid.uuid4(), from_status, to_status, actor=None)
    assert (exc_info.value.status_code, exc_info.value.code) == (409, "invalid_transition")


def test_wrong_current_status_is_409_and_changes_nothing(session):
    org = make_org(session)
    assessment = make_assessment(session, org, status=S.IN_REVIEW)

    # Allowed pair, but the row is not in DRAFT: the conditional UPDATE matches no row.
    with pytest.raises(ApiError) as exc_info:
        transition(session, assessment.id, org.id, S.DRAFT, S.PROCESSING, actor=None)

    assert (exc_info.value.status_code, exc_info.value.code) == (409, "invalid_transition")
    assert status_in_db(session, assessment.id) == "IN_REVIEW"
    session.flush()
    assert audit_events_for(session, assessment.id) == []


def test_another_orgs_assessment_is_not_changed(session):
    org_a, org_b = make_org(session), make_org(session)
    assessment = make_assessment(session, org_a)

    with pytest.raises(ApiError) as exc_info:
        transition(session, assessment.id, org_b.id, S.DRAFT, S.PROCESSING, actor=None)

    assert exc_info.value.status_code == 409
    assert status_in_db(session, assessment.id) == "DRAFT"


def test_system_actor_is_recorded_as_null(session):
    org = make_org(session)
    assessment = make_assessment(session, org)
    transition(session, assessment.id, org.id, S.DRAFT, S.PROCESSING, actor=None)
    session.flush()
    assert audit_events_for(session, assessment.id)[0].actor_user_id is None


def test_transition_and_its_audit_event_roll_back_together(session):
    """The caller's transaction fails after transition(): neither the status change
    nor the audit event survives."""
    org = make_org(session)
    assessment = make_assessment(session, org)

    savepoint = session.begin_nested()
    transition(session, assessment.id, org.id, S.DRAFT, S.PROCESSING, actor=None)
    session.flush()
    assert len(audit_events_for(session, assessment.id)) == 1  # both written inside the transaction
    savepoint.rollback()  # e.g. a later step of the same request failed

    assert status_in_db(session, assessment.id) == "DRAFT"
    assert audit_events_for(session, assessment.id) == []


# --- Concurrency: real connections, real commits


@pytest.fixture
def committed_draft(engine):
    """A DRAFT assessment that is really COMMITTED, so other connections can see it.

    Cleanup deletes everything this fixture's org owns. audit_events rejects DELETE
    (D-21), so the cleanup disables that one trigger, deletes, and re-enables it,
    all in ONE transaction. ALTER TABLE is transactional and takes an exclusive
    lock on audit_events, so no other session ever sees the trigger disabled.
    lock_timeout: fail instead of hanging if a test left a transaction open.
    """
    with Session(engine, expire_on_commit=False) as s:
        org = make_org(s)
        actor = make_user(s, org)
        vendor = make_vendor(s, org)
        assessment = make_assessment(s, org, vendor=vendor, creator=actor)
        s.commit()

    yield {"org_id": org.id, "actor": actor, "assessment_id": assessment.id}

    with engine.begin() as conn:
        params = {"org_id": org.id}
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        conn.execute(text("ALTER TABLE audit_events DISABLE TRIGGER trg_audit_events_no_update_delete"))
        conn.execute(text("DELETE FROM audit_events WHERE org_id = :org_id"), params)
        conn.execute(text("ALTER TABLE audit_events ENABLE TRIGGER trg_audit_events_no_update_delete"))
        conn.execute(text("DELETE FROM assessments WHERE org_id = :org_id"), params)
        conn.execute(text("DELETE FROM vendors WHERE org_id = :org_id"), params)
        conn.execute(text("DELETE FROM users WHERE org_id = :org_id"), params)
        conn.execute(text("DELETE FROM organizations WHERE id = :org_id"), params)


def attempt_transition(engine, draft, results, before=None) -> None:
    """One attempt on its own connection: DRAFT -> PROCESSING, then commit.
    Appends "ok" or the error's status code to results."""
    with Session(engine) as s:
        if before is not None:
            before(s)
        try:
            transition(s, draft["assessment_id"], draft["org_id"], S.DRAFT, S.PROCESSING, draft["actor"])
            s.commit()
            results.append("ok")
        except ApiError as exc:
            s.rollback()
            results.append(exc.status_code)


def committed_state(engine, draft):
    with engine.connect() as conn:
        status = conn.execute(text("SELECT status FROM assessments WHERE id = :id"),
                              {"id": draft["assessment_id"]}).one()[0]
        events = conn.execute(text("SELECT count(*) FROM audit_events WHERE resource_id = :id"),
                              {"id": draft["assessment_id"]}).one()[0]
    return status, events


def test_two_connections_race_for_the_same_transition(engine, committed_draft):
    """Two threads, each with its own connection, start together. Whichever order
    Postgres runs them in, exactly one succeeds and the other gets 409."""
    barrier = threading.Barrier(2)
    results: list = []
    threads = [
        threading.Thread(target=attempt_transition,
                         args=(engine, committed_draft, results, lambda s: barrier.wait(timeout=5)))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert sorted(results, key=str) == [409, "ok"]
    assert committed_state(engine, committed_draft) == ("PROCESSING", 1)  # one change, one audit event


def test_second_update_waits_for_the_lock_then_finds_no_matching_row(engine, committed_draft):
    """The same race, made deterministic, to show HOW it is resolved:
    1. connection 1 runs the UPDATE and does not commit yet (it holds the row lock);
    2. connection 2 runs the same UPDATE and blocks on that lock (seen in pg_stat_activity);
    3. connection 1 commits; connection 2 wakes up, re-checks WHERE status = 'DRAFT'
       against the committed row, matches nothing, and gets 409.
    """
    results: list = []
    second_pid: dict = {}

    def remember_pid(s):
        second_pid["pid"] = s.exec(text("SELECT pg_backend_pid()")).one()[0]

    first = Session(engine)
    second = None
    try:
        transition(first, committed_draft["assessment_id"], committed_draft["org_id"], S.DRAFT, S.PROCESSING,
                   committed_draft["actor"])
        second = threading.Thread(target=attempt_transition, args=(engine, committed_draft, results, remember_pid))
        second.start()

        deadline = time.monotonic() + 5
        while not is_waiting_for_a_lock(engine, second_pid.get("pid")):
            assert time.monotonic() < deadline, "connection 2 never blocked on the row lock"
            time.sleep(0.05)
        assert results == []  # still blocked

        first.commit()
    finally:
        first.close()
        if second is not None:
            second.join(timeout=10)

    assert results == [409]
    assert committed_state(engine, committed_draft) == ("PROCESSING", 1)


def is_waiting_for_a_lock(engine, pid) -> bool:
    if pid is None:
        return False
    with engine.connect() as conn:  # a new connection each time: fresh pg_stat_activity snapshot
        wait_type = conn.execute(text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                                 {"pid": pid}).scalar()
    return wait_type == "Lock"


def test_committed_draft_fixture_rows_are_visible_to_other_connections(engine, committed_draft):
    # Guard for the two tests above: if the rows were not committed, they would
    # prove nothing about separate connections.
    with Session(engine) as s:
        assert s.get(Assessment, committed_draft["assessment_id"]) is not None
