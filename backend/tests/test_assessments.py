"""M5: the assessments API. Tenant isolation, roles, DRAFT-only editing, audit events,
keyset pagination and the status summary."""

import uuid

import pytest
from sqlmodel import col, select

from app.domain import audit
from app.domain.state_machine import transition
from app.main import create_app
from app.models import Assessment, AssessmentControl, AuditEvent
from app.models.enums import AssessmentStatus, Role
from tests.conftest import audit_events_for, client_for, login
from tests.factories import TEST_PASSWORD, make_assessment, make_control, make_org, make_user, make_vendor
from tests.tenancy import assert_hidden_from_other_org

pytestmark = pytest.mark.db

API = "/api/v1/assessments"


@pytest.fixture
def world(session):
    """Org A with one user per role, a vendor and a DRAFT assessment with two controls.
    Org B with a requester, a vendor and its own assessment. Three global controls."""
    org_a, org_b = make_org(session), make_org(session)
    controls = [make_control(session, f"TEST_{name}_{uuid.uuid4().hex[:6]}") for name in ("AAA", "BBB", "CCC")]
    users = {role: make_user(session, org_a, role, password=TEST_PASSWORD) for role in Role}
    vendor_a = make_vendor(session, org_a)
    draft_a = make_assessment(session, org_a, vendor=vendor_a, creator=users[Role.REQUESTER], title="A draft")
    for control in controls[:2]:
        session.add(AssessmentControl(assessment_id=draft_a.id, control_id=control.id))
    session.flush()

    requester_b = make_user(session, org_b, Role.REQUESTER, password=TEST_PASSWORD)
    vendor_b = make_vendor(session, org_b)
    return {
        "org_a": org_a, "org_b": org_b, "controls": controls,
        "requester": users[Role.REQUESTER], "reviewer": users[Role.REVIEWER], "approver": users[Role.APPROVER],
        "vendor_a": vendor_a, "draft_a": draft_a,
        "requester_b": requester_b, "vendor_b": vendor_b,
        "assessment_b": make_assessment(session, org_b, vendor=vendor_b, creator=requester_b),
    }


def create_body(world, **overrides):
    return {"vendor_id": str(world["vendor_a"].id), "title": "Q4 review",
            "control_ids": [str(c.id) for c in world["controls"]], **overrides}


# --- POST /assessments


def test_create_assessment(client, session, world):
    login(client, world["requester"])
    response = client.post(API, json=create_body(world))

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["title"], body["status"], body["vendor_id"]) == ("Q4 review", "DRAFT", str(world["vendor_a"].id))
    assert body["created_by_user_id"] == str(world["requester"].id)
    assert {c["id"] for c in body["controls"]} == {str(c.id) for c in world["controls"]}
    assert session.get(Assessment, uuid.UUID(body["id"])).org_id == world["org_a"].id

    [event] = audit_events_for(session, uuid.UUID(body["id"]))
    assert (event.action, event.actor_user_id, event.old_values) == ("assessment.created", world["requester"].id, None)
    assert event.new_values == {
        "vendor_id": str(world["vendor_a"].id), "title": "Q4 review", "status": "DRAFT",
        "controls": sorted(c.code for c in world["controls"]),
    }


def assessment_count(session):
    return len(session.exec(select(Assessment.id)).all())


def test_unknown_control_id_is_422_and_creates_nothing(client, session, world):
    login(client, world["requester"])
    before = assessment_count(session)
    response = client.post(API, json=create_body(world, control_ids=[str(world["controls"][0].id), str(uuid.uuid4())]))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_control"
    assert assessment_count(session) == before


@pytest.mark.parametrize("overrides", [
    {"control_ids": []},
    {"title": ""},
    {"title": "x" * 201},
    {"vendor_id": "not-a-uuid"},
    {"status": "APPROVED"},             # not a field: extra="forbid"
    {"org_id": str(uuid.uuid4())},      # the org always comes from the session
], ids=["no-controls", "empty-title", "long-title", "bad-vendor-id", "extra-status", "extra-org"])
def test_invalid_create_body_is_422(client, world, overrides):
    login(client, world["requester"])
    response = client.post(API, json=create_body(world, **overrides))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_repeated_control_id_is_422(client, world):
    login(client, world["requester"])
    control_id = str(world["controls"][0].id)
    assert client.post(API, json=create_body(world, control_ids=[control_id, control_id])).status_code == 422


def test_another_orgs_vendor_is_404_like_an_unknown_vendor(client, session, world):
    login(client, world["requester"])
    before = assessment_count(session)
    foreign = client.post(API, json=create_body(world, vendor_id=str(world["vendor_b"].id)))
    missing = client.post(API, json=create_body(world, vendor_id=str(uuid.uuid4())))

    assert (foreign.status_code, missing.status_code) == (404, 404)
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"] == "not_found"
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]
    assert assessment_count(session) == before


def test_assessment_is_not_created_when_its_audit_event_fails(session, world, monkeypatch):
    """Same idea as the vendor test: record() fails after the INSERTs; after the
    rollback that get_session would do, neither the assessment nor its controls exist."""
    def broken_record(*args, **kwargs):
        raise RuntimeError("audit write failed")

    with client_for(create_app(), session, raise_server_exceptions=False) as client:
        login(client, world["requester"])
        before = assessment_count(session)
        monkeypatch.setattr(audit, "record", broken_record)
        response = client.post(API, json=create_body(world, title="Half saved"))

    assert response.status_code == 500
    session.rollback()
    assert assessment_count(session) == before
    assert session.exec(select(Assessment).where(Assessment.title == "Half saved")).all() == []


# --- GET /assessments/{id}


def test_get_assessment_with_its_controls(client, world):
    login(client, world["reviewer"])  # any role may read
    response = client.get(f"{API}/{world['draft_a'].id}")

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "A draft"
    # Sorted by code; only the two controls in scope.
    assert [c["code"] for c in body["controls"]] == sorted(c.code for c in world["controls"][:2])


# --- PATCH /assessments/{id}


def test_patch_title_and_controls(client, session, world):
    draft, (aaa, bbb, ccc) = world["draft_a"], world["controls"]
    login(client, world["requester"])
    # Was {AAA, BBB}; now {BBB, CCC}: AAA removed, CCC added, BBB kept.
    response = client.patch(f"{API}/{draft.id}", json={"title": "Renamed", "control_ids": [str(bbb.id), str(ccc.id)]})

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Renamed"
    assert {c["id"] for c in response.json()["controls"]} == {str(bbb.id), str(ccc.id)}

    [event] = audit_events_for(session, draft.id)
    assert event.action == "assessment.updated"
    assert event.old_values == {"title": "A draft", "controls": sorted([aaa.code, bbb.code])}
    assert event.new_values == {"title": "Renamed", "controls": sorted([bbb.code, ccc.code])}


def test_patch_only_records_fields_that_changed(client, session, world):
    draft, controls = world["draft_a"], world["controls"]
    login(client, world["requester"])
    # Same title, same controls in a different order: nothing changed.
    same = client.patch(f"{API}/{draft.id}", json={"title": "A draft",
                                                   "control_ids": [str(controls[1].id), str(controls[0].id)]})
    assert same.status_code == 200
    assert audit_events_for(session, draft.id) == []

    client.patch(f"{API}/{draft.id}", json={"title": "New title"})
    [event] = audit_events_for(session, draft.id)
    assert (event.old_values, event.new_values) == ({"title": "A draft"}, {"title": "New title"})


@pytest.mark.parametrize("status", [s for s in AssessmentStatus if s != AssessmentStatus.DRAFT])
def test_patch_a_non_draft_assessment_is_409(client, session, world, status):
    assessment = make_assessment(session, world["org_a"], vendor=world["vendor_a"], status=status, title="Locked")
    login(client, world["requester"])
    response = client.patch(f"{API}/{assessment.id}", json={"title": "Changed"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_editable"
    session.refresh(assessment)
    assert assessment.title == "Locked"
    assert audit_events_for(session, assessment.id) == []


def test_patch_after_a_transition_is_409(client, session, world):
    """The real path: DRAFT -> PROCESSING through the state machine, then an edit."""
    draft = world["draft_a"]
    transition(session, draft.id, world["org_a"].id, AssessmentStatus.DRAFT, AssessmentStatus.PROCESSING,
               actor=world["requester"])
    login(client, world["requester"])
    assert client.patch(f"{API}/{draft.id}", json={"title": "Too late"}).status_code == 409


@pytest.mark.parametrize("body", [{}, {"title": None, "control_ids": None}, {"title": ""}, {"control_ids": []},
                                  {"status": "APPROVED"}, {"vendor_id": str(uuid.uuid4())}])
def test_invalid_patch_body_is_422(client, world, body):
    login(client, world["requester"])
    assert client.patch(f"{API}/{world['draft_a'].id}", json=body).status_code == 422


def test_patch_with_unknown_control_is_422_and_changes_nothing(client, session, world):
    draft = world["draft_a"]
    login(client, world["requester"])
    response = client.patch(f"{API}/{draft.id}", json={"control_ids": [str(uuid.uuid4())]})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_control"
    kept = session.exec(select(AssessmentControl.control_id).where(AssessmentControl.assessment_id == draft.id)).all()
    assert set(kept) == {c.id for c in world["controls"][:2]}


# --- Roles and login


WRITES = [("POST", API, "create"), ("PATCH", API + "/{id}", "patch")]


@pytest.mark.parametrize("role", [Role.REVIEWER, Role.APPROVER])
@pytest.mark.parametrize(("method", "path", "kind"), WRITES, ids=[w[2] for w in WRITES])
def test_only_a_requester_may_create_or_edit(client, session, world, role, method, path, kind):
    login(client, world[role.value.lower()])
    body = create_body(world) if kind == "create" else {"title": "Nope"}
    response = client.request(method, path.format(id=world["draft_a"].id), json=body)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert session.exec(select(Assessment).where(col(Assessment.title).in_(["Q4 review", "Nope"]))).all() == []


READS = ["", "/summary", "/{id}", "/{id}/audit-events"]


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("suffix", READS)
def test_every_role_may_read(client, world, role, suffix):
    login(client, world[role.value.lower()])
    assert client.get(API + suffix.format(id=world["draft_a"].id)).status_code == 200


@pytest.mark.parametrize(("method", "suffix"), [("GET", s) for s in READS] + [("POST", ""), ("PATCH", "/{id}")])
def test_every_endpoint_needs_login(client, world, method, suffix):
    response = client.request(method, API + suffix.format(id=world["draft_a"].id), json={})
    assert response.status_code == 401


# --- Tenant isolation: every route that takes an id


@pytest.mark.parametrize(("method", "suffix", "kwargs"), [
    ("GET", "/{id}", {}),
    ("GET", "/{id}/audit-events", {}),
    ("PATCH", "/{id}", {"json": {"title": "Hacked"}}),
])
def test_another_orgs_assessment_is_hidden(client, session, world, method, suffix, kwargs):
    login(client, world["requester_b"])
    assert_hidden_from_other_org(client, method, API + suffix, world["draft_a"].id, **kwargs)
    session.refresh(world["draft_a"])
    assert world["draft_a"].title == "A draft"  # the PATCH changed nothing


def test_list_shows_only_the_users_org(client, world):
    login(client, world["requester_b"])
    ids = [item["id"] for item in client.get(API).json()["items"]]
    assert ids == [str(world["assessment_b"].id)]


# --- GET /assessments: filter, limits, keyset pagination


def walk_pages(client, params):
    """Follow next_cursor to the end; returns (all ids in order, page sizes)."""
    ids, sizes, cursor = [], [], None
    while True:
        response = client.get(API, params={**params, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        page = response.json()
        ids += [item["id"] for item in page["items"]]
        sizes.append(len(page["items"]))
        cursor = page["next_cursor"]
        if cursor is None:
            return ids, sizes


def test_pagination_has_stable_order_and_no_duplicates_or_gaps(client, session, world):
    """45 assessments created in ONE transaction, so they all share the same
    created_at (now() is the transaction start time). Only the id tie-break can
    order them, which is exactly the case that breaks a created_at-only cursor."""
    org, vendor, creator = world["org_b"], world["vendor_b"], world["requester_b"]
    for i in range(44):
        make_assessment(session, org, vendor=vendor, creator=creator, title=f"#{i}")
    rows = session.exec(select(Assessment).where(Assessment.org_id == org.id)).all()
    assert len(rows) == 45
    assert len({r.created_at for r in rows}) == 1  # the tie the test is about

    expected = [str(r.id) for r in sorted(rows, key=lambda r: (r.created_at, r.id), reverse=True)]
    login(client, creator)
    ids, sizes = walk_pages(client, {"limit": 20})

    assert sizes == [20, 20, 5]
    assert ids == expected            # stable, documented order: newest first, then id desc
    assert len(set(ids)) == len(ids)  # no duplicates
    assert set(ids) == set(expected)  # no gaps
    assert walk_pages(client, {"limit": 20})[0] == ids  # same order on a second walk


def test_status_filter_with_pagination(client, session, world):
    org, vendor, creator = world["org_b"], world["vendor_b"], world["requester_b"]
    in_review = [make_assessment(session, org, vendor=vendor, creator=creator, status=AssessmentStatus.IN_REVIEW)
                 for _ in range(7)]
    login(client, creator)
    ids, sizes = walk_pages(client, {"status": "IN_REVIEW", "limit": 3})

    assert sizes == [3, 3, 1]
    assert ids == [str(a.id) for a in sorted(in_review, key=lambda a: (a.created_at, a.id), reverse=True)]
    # The DRAFT assessment_b is not in the filtered list.
    assert str(world["assessment_b"].id) not in ids


def test_default_limit_is_20(client, session, world):
    org, vendor, creator = world["org_b"], world["vendor_b"], world["requester_b"]
    for _ in range(24):
        make_assessment(session, org, vendor=vendor, creator=creator)
    login(client, creator)
    page = client.get(API).json()
    assert len(page["items"]) == 20
    assert page["next_cursor"] is not None


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"status": "NOT_A_STATUS"}],
                         ids=["limit-0", "limit-101", "bad-status"])
def test_invalid_list_params_are_422(client, world, params):
    login(client, world["requester"])
    response = client.get(API, params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("cursor", ["not base64!", "bm90LWEtY3Vyc29y", "MjAyNi0wMS0wMVQwMDowMDowMHxub3QtYS11dWlk"],
                         ids=["not-base64", "no-separator", "bad-uuid"])
def test_malformed_cursor_is_422(client, world, cursor):
    login(client, world["requester"])
    response = client.get(API, params={"cursor": cursor})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"


# --- GET /assessments/summary


def test_summary_route_is_not_parsed_as_an_id(client, world):
    """/assessments/summary is declared before /assessments/{id}; in the other order,
    "summary" would be parsed as a UUID and this would be 422."""
    login(client, world["requester"])
    response = client.get(f"{API}/summary")
    assert response.status_code == 200, response.text
    assert set(response.json()["counts"]) == {s.value for s in AssessmentStatus}


def test_summary_counts_only_the_users_org(client, session, world):
    org_a, vendor = world["org_a"], world["vendor_a"]
    for status, n in [(AssessmentStatus.IN_REVIEW, 3), (AssessmentStatus.APPROVED, 2)]:
        for _ in range(n):
            make_assessment(session, org_a, vendor=vendor, status=status)
    # Noise in org B, which must not be counted.
    for _ in range(4):
        make_assessment(session, world["org_b"], vendor=world["vendor_b"], status=AssessmentStatus.IN_REVIEW)

    login(client, world["requester"])
    body = client.get(f"{API}/summary").json()

    assert body["counts"] == {
        "DRAFT": 1, "PROCESSING": 0, "PROCESSING_FAILED": 0, "IN_REVIEW": 3,
        "PENDING_APPROVAL": 0, "APPROVED": 2, "REJECTED": 0,
    }
    assert body["total"] == 6


# --- GET /assessments/{id}/audit-events


def test_audit_events_are_listed_oldest_first(client, session, world):
    draft = world["draft_a"]
    login(client, world["requester"])
    client.patch(f"{API}/{draft.id}", json={"title": "Edited"})
    transition(session, draft.id, world["org_a"].id, AssessmentStatus.DRAFT, AssessmentStatus.PROCESSING,
               actor=world["requester"])
    session.flush()

    ids, actions, cursor = [], [], None
    while True:  # limit=1: every event is its own page, so the cursor is exercised too
        page = client.get(f"{API}/{draft.id}/audit-events",
                          params={"limit": 1, **({"cursor": cursor} if cursor else {})}).json()
        ids += [e["id"] for e in page["items"]]
        actions += [e["action"] for e in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert actions == ["assessment.updated", "assessment.status_changed"]
    assert ids == [str(e.id) for e in audit_events_for(session, draft.id)]


def test_audit_events_list_only_this_assessment(client, session, world):
    login(client, world["requester"])
    other = client.post(API, json=create_body(world)).json()  # writes an event for another assessment
    events = client.get(f"{API}/{world['draft_a'].id}/audit-events").json()["items"]
    assert events == []
    assert len(client.get(f"{API}/{other['id']}/audit-events").json()["items"]) == 1
    assert session.exec(select(AuditEvent).where(AuditEvent.resource_id == world["draft_a"].id)).all() == []
