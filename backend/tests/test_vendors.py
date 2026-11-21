"""Vendors. M4: a user sees only their own org's vendors. M5: a Requester creates and
renames vendors; each change writes an audit event in the same transaction."""

import uuid

import pytest
from sqlmodel import select

from app.domain import audit
from app.main import create_app
from app.models import AuditEvent, Organization, Vendor
from app.models.enums import Role
from tests.conftest import audit_events_for, client_for, login
from tests.factories import TEST_PASSWORD, make_org, make_user, make_vendor
from tests.tenancy import assert_hidden_from_other_org

pytestmark = pytest.mark.db


@pytest.fixture
def two_orgs(session):
    """Org A and org B, each with a logged-in-able user and its own vendors.
    Both orgs have a vendor called "Shared Name": names are unique per org only."""
    org_a, org_b = make_org(session), make_org(session)
    return {
        "user_a": make_user(session, org_a, Role.REQUESTER, password=TEST_PASSWORD),
        "user_b": make_user(session, org_b, Role.APPROVER, password=TEST_PASSWORD),
        "vendors_a": [make_vendor(session, org_a, "zeta Systems"), make_vendor(session, org_a, "Shared Name"),
                      make_vendor(session, org_a, "Acme Cloud")],
        "vendors_b": [make_vendor(session, org_b, "Shared Name")],
    }


def test_vendor_list_shows_only_the_users_org_sorted_by_name(client, two_orgs):
    login(client, two_orgs["user_a"])
    response = client.get("/api/v1/vendors")

    assert response.status_code == 200
    assert [v["name"] for v in response.json()] == ["Acme Cloud", "Shared Name", "zeta Systems"]
    assert {v["id"] for v in response.json()} == {str(v.id) for v in two_orgs["vendors_a"]}


def test_get_own_vendor(client, two_orgs):
    vendor = two_orgs["vendors_a"][0]
    login(client, two_orgs["user_a"])
    response = client.get(f"/api/v1/vendors/{vendor.id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(vendor.id)
    assert response.json()["name"] == "zeta Systems"


def test_other_orgs_vendor_is_404(client, two_orgs):
    login(client, two_orgs["user_b"])
    assert_hidden_from_other_org(client, "GET", "/api/v1/vendors/{id}", two_orgs["vendors_a"][0].id)


def test_vendor_endpoints_need_login(client, two_orgs):
    assert client.get("/api/v1/vendors").status_code == 401
    assert client.get(f"/api/v1/vendors/{two_orgs['vendors_a'][0].id}").status_code == 401


def test_malformed_vendor_id_is_422(client, two_orgs):
    login(client, two_orgs["user_a"])
    assert client.get("/api/v1/vendors/not-a-uuid").status_code == 422


# --- M5: create and rename (Requester only), with an audit event each


def vendor_names(session, org_id):
    return sorted(session.exec(select(Vendor.name).where(Vendor.org_id == org_id)).all())


def test_create_vendor(client, session, two_orgs):
    user = two_orgs["user_a"]
    login(client, user)
    response = client.post("/api/v1/vendors", json={"name": "  New Vendor  "})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "New Vendor"  # surrounding whitespace stripped
    vendor = session.get(Vendor, uuid.UUID(body["id"]))
    assert vendor.org_id == user.org_id  # the org comes from the session, never the body

    [event] = audit_events_for(session, vendor.id)
    assert (event.action, event.resource_type, event.org_id, event.actor_user_id) == (
        "vendor.created", "vendor", user.org_id, user.id)
    assert (event.old_values, event.new_values) == (None, {"name": "New Vendor"})
    assert event.request_id == response.headers["X-Request-ID"]


def test_duplicate_vendor_name_in_any_case_is_409(client, session, two_orgs):
    login(client, two_orgs["user_a"])
    before = vendor_names(session, two_orgs["user_a"].org_id)

    response = client.post("/api/v1/vendors", json={"name": "ACME CLOUD"})  # "Acme Cloud" exists

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_vendor_name"
    assert vendor_names(session, two_orgs["user_a"].org_id) == before
    assert session.exec(select(AuditEvent).where(AuditEvent.action == "vendor.created")).all() == []


def test_same_vendor_name_in_another_org_is_allowed(client, two_orgs):
    login(client, two_orgs["user_a"])
    # Org B has "Shared Name" too; org A gets a name only org B has.
    assert client.post("/api/v1/vendors", json={"name": "Only In B"}).status_code == 201


def test_rename_vendor(client, session, two_orgs):
    vendor = two_orgs["vendors_a"][0]
    login(client, two_orgs["user_a"])
    response = client.patch(f"/api/v1/vendors/{vendor.id}", json={"name": "Zeta Systems Ltd"})

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Zeta Systems Ltd"
    [event] = audit_events_for(session, vendor.id)
    assert event.action == "vendor.updated"
    assert (event.old_values, event.new_values) == ({"name": "zeta Systems"}, {"name": "Zeta Systems Ltd"})


def test_rename_to_another_vendors_name_is_409(client, session, two_orgs):
    vendor = two_orgs["vendors_a"][0]
    login(client, two_orgs["user_a"])
    response = client.patch(f"/api/v1/vendors/{vendor.id}", json={"name": "acme cloud"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_vendor_name"
    session.refresh(vendor)
    assert vendor.name == "zeta Systems"
    assert audit_events_for(session, vendor.id) == []


def test_rename_to_the_same_name_writes_no_audit_event(client, session, two_orgs):
    vendor = two_orgs["vendors_a"][0]
    login(client, two_orgs["user_a"])
    assert client.patch(f"/api/v1/vendors/{vendor.id}", json={"name": "zeta Systems"}).status_code == 200
    assert audit_events_for(session, vendor.id) == []


@pytest.mark.parametrize("role", [Role.REVIEWER, Role.APPROVER])
def test_only_a_requester_may_create_or_rename_vendors(client, session, two_orgs, role):
    org_id = two_orgs["user_a"].org_id
    other = make_user(session, session.get(Organization, org_id), role, password=TEST_PASSWORD)
    vendor = two_orgs["vendors_a"][0]
    login(client, other)

    created = client.post("/api/v1/vendors", json={"name": "Nope"})
    renamed = client.patch(f"/api/v1/vendors/{vendor.id}", json={"name": "Nope"})

    assert (created.status_code, renamed.status_code) == (403, 403)
    assert created.json()["error"]["code"] == "forbidden"
    assert "Nope" not in vendor_names(session, org_id)


def test_vendor_writes_need_login(client, two_orgs):
    assert client.post("/api/v1/vendors", json={"name": "X"}).status_code == 401
    assert client.patch(f"/api/v1/vendors/{two_orgs['vendors_a'][0].id}", json={"name": "X"}).status_code == 401


def test_renaming_another_orgs_vendor_is_404_and_changes_nothing(client, session, two_orgs):
    requester_b = make_user(session, session.get(Organization, two_orgs["user_b"].org_id), Role.REQUESTER,
                            password=TEST_PASSWORD)
    vendor_a = two_orgs["vendors_a"][0]
    login(client, requester_b)

    assert_hidden_from_other_org(client, "PATCH", "/api/v1/vendors/{id}", vendor_a.id, json={"name": "Hacked"})
    session.refresh(vendor_a)
    assert vendor_a.name == "zeta Systems"


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "   "}, {"name": "x" * 201},
                                  {"name": "Ok", "org_id": str(uuid.uuid4())}])
def test_invalid_vendor_body_is_422(client, two_orgs, body):
    login(client, two_orgs["user_a"])
    response = client.post("/api/v1/vendors", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_vendor_is_not_created_when_its_audit_event_fails(session, two_orgs, monkeypatch):
    """The vendor and its audit event are one transaction (rule 4).

    record() is made to fail AFTER the vendor's INSERT has run. The endpoint
    returns 500 without committing; get_session would then roll back on close,
    which session.rollback() stands in for here. If the endpoint had committed the
    vendor before writing the audit event, the rollback could not remove it.
    """
    def broken_record(*args, **kwargs):
        raise RuntimeError("audit write failed")

    with client_for(create_app(), session, raise_server_exceptions=False) as client:
        login(client, two_orgs["user_a"])
        monkeypatch.setattr(audit, "record", broken_record)
        response = client.post("/api/v1/vendors", json={"name": "Half Saved"})

    assert response.status_code == 500
    session.rollback()
    assert "Half Saved" not in vendor_names(session, two_orgs["user_a"].org_id)
