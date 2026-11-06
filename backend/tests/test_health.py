"""M2: GET /api/v1/health."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from app.main import create_app
from tests.conftest import client_for


@pytest.mark.db
def test_health_ok_when_the_database_answers(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert len(response.headers["X-Request-ID"]) == 32


def test_health_503_when_the_database_is_down(caplog):
    # A real connection failure: nothing listens on port 1. No Docker needed.
    dead_engine = create_engine("postgresql+psycopg://nobody:secret-pw@127.0.0.1:1/nowhere",
                                connect_args={"connect_timeout": 2})
    with Session(dead_engine) as dead_session:
        response = client_for(create_app(), dead_session).get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
    # The cause is logged for us, and nothing about it reaches the client.
    assert "database did not answer" in caplog.text
    assert "127.0.0.1" not in response.text and "secret-pw" not in response.text


def test_health_is_only_get():
    response = TestClient(create_app()).post("/api/v1/health")
    assert response.status_code == 405
