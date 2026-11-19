"""M4: the first tenant-scoped endpoints. A user sees only their own org's vendors."""

import pytest

from app.models.enums import Role
from tests.conftest import login
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
