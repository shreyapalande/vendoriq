"""M5: GET /security-controls. Global reference data: every org sees the same list,
but only when logged in."""

import pytest

from app.models.enums import Role
from tests.conftest import login
from tests.factories import TEST_PASSWORD, make_control, make_org, make_user

pytestmark = pytest.mark.db

URL = "/api/v1/security-controls"


@pytest.mark.parametrize("role", list(Role))
def test_any_logged_in_user_gets_all_controls_sorted_by_code(client, session, role):
    # Created out of order; the test database may hold other controls too.
    created = [make_control(session, code) for code in ("TEST_ZZZ", "TEST_AAA", "TEST_MMM")]
    login(client, make_user(session, make_org(session), role, password=TEST_PASSWORD))

    response = client.get(URL)

    assert response.status_code == 200
    body = response.json()
    codes = [c["code"] for c in body]
    assert codes == sorted(codes)
    assert set(codes) >= {"TEST_AAA", "TEST_MMM", "TEST_ZZZ"}
    aaa = next(c for c in body if c["code"] == "TEST_AAA")
    assert aaa == {"id": str(created[1].id), "code": "TEST_AAA", "title": "T", "description": "D"}


def test_users_of_different_orgs_see_the_same_controls(client, session):
    make_control(session)
    user_a = make_user(session, make_org(session), password=TEST_PASSWORD)
    user_b = make_user(session, make_org(session), password=TEST_PASSWORD)

    login(client, user_a)
    seen_by_a = client.get(URL).json()
    login(client, user_b)
    assert client.get(URL).json() == seen_by_a


def test_security_controls_need_login(client, session):
    make_control(session)
    response = client.get(URL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
