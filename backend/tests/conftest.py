"""Shared test fixtures.

Database tests run against the real Postgres TEST database (never SQLite), and
each test runs inside a transaction that is rolled back afterwards.

The fixtures are lazy: a test that doesn't ask for `engine`, `session` or `client`
never connects, so the M1 tests run without Docker. Mark database tests with
`@pytest.mark.db`; `pytest -m "not db"` then runs everything else with Docker stopped.
"""

import os

# Cheap argon2 parameters, for the test suite only (the production defaults are in
# app/config.py). Set here, at the very top of conftest.py, because pytest loads this
# file before any test module, and app/auth/passwords.py reads the settings once, on
# import, to build its hasher. Real environment variables take precedence over .env.
# 1 pass, 8 KiB, 1 lane is the minimum argon2 accepts (memory >= 8 KiB per lane).
os.environ["ARGON2_TIME_COST"] = "1"
os.environ["ARGON2_MEMORY_COST"] = "8"
os.environ["ARGON2_PARALLELISM"] = "1"

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, col, select

from app.config import get_settings
from app.db import get_session
from app.main import create_app
from app.models import AuditEvent, User
from tests.factories import TEST_PASSWORD

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _test_database_url() -> str:
    """TEST_DATABASE_URL, after checks that make it impossible to hit the dev database."""
    settings = get_settings()
    url = settings.test_database_url
    if not url:
        pytest.fail("TEST_DATABASE_URL is not set (see .env.example)")
    if make_url(url).database == make_url(settings.database_url).database:
        pytest.fail("TEST_DATABASE_URL points at the same database as DATABASE_URL; refusing to run")
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL's database name must end in _test; refusing to run")
    return url


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """One engine for the whole run, on a test database migrated to head.

    Fails loudly (does not skip) if the database is down: a silently skipped
    database test looks like a passing one.
    """
    url = _test_database_url()
    engine = create_engine(url, connect_args={"connect_timeout": 3})
    connect_error = None
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        connect_error = exc.orig  # fail outside the except block: no chained traceback
    if connect_error is not None:
        pytest.fail(
            f"Cannot connect to the test database ({make_url(url).render_as_string(hide_password=True)}).\n"
            "Start it with:  docker compose up -d db\n"
            'Or run only the tests that need no database:  pytest -m "not db"\n'
            f"Cause: {connect_error}",
            pytrace=False,
        )

    # The real migrations, not SQLModel.metadata.create_all(), so tests use the
    # schema production gets.
    alembic_config = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_config.attributes["database_url"] = url
    command.upgrade(alembic_config, "head")

    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A Session inside a transaction that is always rolled back.

    join_transaction_mode="create_savepoint": if the code under test calls
    session.commit(), it only releases a SAVEPOINT; the outer transaction is
    still rolled back here, so no test leaves data behind.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def client_for(app: FastAPI, session: Session, **kwargs) -> TestClient:
    """A TestClient (HTTPX-based) whose requests all use the test session."""
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app, **kwargs)


def login(client: TestClient, user: User, password: str = TEST_PASSWORD) -> None:
    """Log in through the real endpoint; the client's cookie jar keeps the session cookie.
    Logging in as another user replaces the cookie, so one client can switch users."""
    response = client.post("/api/v1/auth/login", json={"email": user.email, "password": password})
    assert response.status_code == 200, response.text


def audit_events_for(session: Session, resource_id) -> list[AuditEvent]:
    """The audit rows about one resource, oldest first (same order as the API)."""
    return list(session.exec(
        select(AuditEvent).where(AuditEvent.resource_id == resource_id)
        .order_by(col(AuditEvent.created_at), col(AuditEvent.id))
    ).all())


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    with client_for(create_app(), session) as test_client:
        yield test_client
