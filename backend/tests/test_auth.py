"""M4: login, logout, /me, the session cookie, current_user and require_role."""

import json
from datetime import timedelta
from http.cookies import SimpleCookie

import pytest
from fastapi import Depends
from sqlmodel import select

from app.auth import passwords
from app.auth.dependencies import require_role
from app.auth.sessions import SESSION_COOKIE, create_session, hash_token, utc_now
from app.config import get_settings
from app.main import create_app
from app.models import User, UserSession
from app.models.enums import Role
from tests.conftest import client_for, login
from tests.factories import TEST_PASSWORD, add, make_org, make_user

pytestmark = pytest.mark.db

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


@pytest.fixture
def user(session) -> User:
    return make_user(session, make_org(session, "Acme Corp"), Role.REVIEWER,
                     email="Rita.Reviewer@acme.example", password=TEST_PASSWORD)


def session_cookie(response) -> SimpleCookie:
    """Parse the Set-Cookie header, so the tests read the real attributes the browser gets."""
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie


def sessions_of(session, user) -> list[UserSession]:
    return list(session.exec(select(UserSession).where(UserSession.user_id == user.id)).all())


# --- Login


def test_login_success_sets_a_cookie_and_stores_only_the_token_hash(client, session, user):
    response = client.post(LOGIN, json={"email": user.email, "password": TEST_PASSWORD})

    assert response.status_code == 200
    assert response.json()["user"]["email"] == user.email
    token = session_cookie(response)[SESSION_COOKIE].value
    [stored] = sessions_of(session, user)
    assert stored.token_hash == hash_token(token)
    assert stored.token_hash != token
    assert stored.revoked_at is None
    # Expires 8 hours from now (allowing for the time the test takes).
    assert timedelta(hours=7, minutes=59) < stored.expires_at - utc_now() <= timedelta(hours=8)


def test_login_email_is_case_insensitive(client, user):
    response = client.post(LOGIN, json={"email": "rita.reviewer@ACME.example", "password": TEST_PASSWORD})
    assert response.status_code == 200


def test_cookie_flags_for_local_http(client, user):
    response = client.post(LOGIN, json={"email": user.email, "password": TEST_PASSWORD})
    morsel = session_cookie(response)[SESSION_COOKIE]

    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "lax"
    assert morsel["path"] == "/"
    assert morsel["max-age"] == str(8 * 3600)
    assert not morsel["secure"]  # SESSION_COOKIE_SECURE defaults to false


def test_cookie_is_secure_when_the_setting_is_on(session, user):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(update={"session_cookie_secure": True})
    response = client_for(app, session).post(LOGIN, json={"email": user.email, "password": TEST_PASSWORD})

    assert response.status_code == 200
    assert session_cookie(response)[SESSION_COOKIE]["secure"] is True


@pytest.fixture
def inactive_user(session) -> User:
    return make_user(session, make_org(session), email="gone@acme.example", password=TEST_PASSWORD, is_active=False)


# Unknown email, wrong password, inactive user (with the right password).
FAILED_LOGINS = [
    ("nobody@acme.example", TEST_PASSWORD, "unknown email"),
    ("Rita.Reviewer@acme.example", "Not-The-Password-42", "wrong password"),
    ("gone@acme.example", TEST_PASSWORD, "inactive user"),
]


@pytest.mark.parametrize(("email", "password", "reason"), FAILED_LOGINS)
def test_failed_logins_all_get_the_same_401(client, session, user, inactive_user, caplog, email, password, reason):
    response = client.post(LOGIN, json={"email": email, "password": password})

    assert response.status_code == 401
    error = response.json()["error"]
    assert (error["code"], error["message"]) == ("invalid_credentials", "Invalid email or password")
    assert "set-cookie" not in response.headers
    assert sessions_of(session, user) == [] and sessions_of(session, inactive_user) == []
    # Logged with the reason for us, but never the email or the password.
    assert f"login failed: {reason}" in caplog.text
    assert email not in caplog.text and password not in caplog.text


@pytest.mark.parametrize(("email", "password", "reason"), FAILED_LOGINS)
def test_every_failed_login_runs_exactly_one_argon2_verify(client, user, inactive_user, monkeypatch, email, password, reason):
    # Response time can't be tested reliably, so test its cause: an unknown email
    # does the same expensive work (one argon2 verify) as a known one.
    real_hasher = passwords._hasher

    class CountingHasher:  # PasswordHasher uses __slots__, so wrap it instead of patching it
        calls = 0

        def verify(self, password_hash, password):
            CountingHasher.calls += 1
            return real_hasher.verify(password_hash, password)

    monkeypatch.setattr(passwords, "_hasher", CountingHasher())

    response = client.post(LOGIN, json={"email": email, "password": password})

    assert response.status_code == 401
    assert CountingHasher.calls == 1


def test_login_rejects_unknown_fields(client, user):
    response = client.post(LOGIN, json={"email": user.email, "password": TEST_PASSWORD, "remember_me": True})
    assert response.status_code == 422


@pytest.mark.parametrize("send", [
    # What a plain HTML <form> on another site can send without CORS approval.
    pytest.param(lambda c, body: c.post(LOGIN, data=body), id="form-urlencoded"),
    pytest.param(lambda c, body: c.post(LOGIN, content=json.dumps(body), headers={"content-type": "text/plain"}),
                 id="json-as-text-plain"),
])
def test_login_accepts_only_a_json_body(client, session, user, send):
    # Correct credentials, so the 422 can only come from the content type. Backs up
    # D-05: cross-site forms can't produce application/json, and SameSite=Lax keeps
    # the cookie off cross-site POSTs.
    response = send(client, {"email": user.email, "password": TEST_PASSWORD})

    assert response.status_code == 422
    assert "set-cookie" not in response.headers
    assert sessions_of(session, user) == []


# --- /me and current_user


def test_me_returns_the_user_org_and_role(client, user):
    login(client, user)
    body = client.get(ME).json()

    assert body == {
        "user": {"id": str(user.id), "email": user.email, "full_name": "Test User", "role": "REVIEWER"},
        "org": {"id": str(user.org_id), "name": "Acme Corp"},
    }


def test_me_without_a_cookie_is_401(client):
    response = client.get(ME)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_me_with_an_unknown_token_is_401(client):
    client.cookies.set(SESSION_COOKIE, "made-up-token")
    assert client.get(ME).status_code == 401


def test_expired_session_is_rejected(client, session, user):
    token = "expired-session-token"
    add(session, UserSession(user_id=user.id, token_hash=hash_token(token), expires_at=utc_now() - timedelta(seconds=1)))
    client.cookies.set(SESSION_COOKIE, token)

    assert client.get(ME).status_code == 401


def test_revoked_session_is_rejected(client, session, user):
    token = "revoked-session-token"
    add(session, UserSession(user_id=user.id, token_hash=hash_token(token),
                             expires_at=utc_now() + timedelta(hours=1), revoked_at=utc_now()))
    client.cookies.set(SESSION_COOKIE, token)

    assert client.get(ME).status_code == 401


def test_deactivating_a_user_ends_their_live_session(client, session, user):
    token = create_session(session, user)
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get(ME).status_code == 200

    user.is_active = False
    session.add(user)
    session.flush()

    assert client.get(ME).status_code == 401


# --- Logout


def test_logout_revokes_the_session_and_clears_the_cookie(client, session, user):
    login(client, user)
    token = client.cookies[SESSION_COOKIE]

    response = client.post(LOGOUT)

    assert response.status_code == 204
    morsel = session_cookie(response)[SESSION_COOKIE]
    assert morsel.value == "" and morsel["max-age"] == "0"
    [stored] = sessions_of(session, user)
    assert stored.revoked_at is not None
    # The old token is dead even if someone kept a copy of the cookie.
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get(ME).status_code == 401


def test_logout_without_a_session_still_succeeds(client):
    assert client.post(LOGOUT).status_code == 204


# --- require_role, on a route that exists only in this test's app


@pytest.fixture
def approver_only_client(session):
    app = create_app()

    @app.get("/test-only/approver")
    def approver_only(user: User = Depends(require_role(Role.APPROVER))):
        return {"ok": True}

    return client_for(app, session)


def test_wrong_role_gets_403(approver_only_client, user):
    login(approver_only_client, user)  # a REVIEWER
    response = approver_only_client.get("/test-only/approver")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_right_role_is_allowed(approver_only_client, session, user):
    approver = make_user(session, make_org(session), Role.APPROVER, password=TEST_PASSWORD)
    login(approver_only_client, approver)

    assert approver_only_client.get("/test-only/approver").status_code == 200


def test_role_check_without_login_is_401_not_403(approver_only_client):
    assert approver_only_client.get("/test-only/approver").status_code == 401
