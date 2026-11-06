"""M2: every error response has the shape {"error": {"code", "message", "request_id"}}.

Test-only routes are added to a fresh create_app() here, never to the real app.
None of these tests touch the database.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app.errors import ApiError
from app.main import create_app


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")  # rule 9: request models forbid extra fields
    name: str
    quantity: int


@pytest.fixture
def client() -> TestClient:
    app = create_app()

    @app.post("/test/items")
    def create_item(item: Item):
        return item

    @app.get("/test/not-found")
    def not_found():
        raise HTTPException(status_code=404, detail="Assessment not found")

    @app.get("/test/conflict")
    def conflict():
        raise ApiError(409, "invalid_state", "Assessment is not awaiting review")

    @app.get("/test/boom")
    def boom():
        raise RuntimeError("secret detail: password=hunter2")

    # raise_server_exceptions=False: get the 500 response instead of the exception.
    return TestClient(app, raise_server_exceptions=False)


def assert_error_shape(response, status: int, code: str) -> dict:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    return body["error"]


def test_unknown_route_is_404(client):
    error = assert_error_shape(client.get("/api/v1/nope"), 404, "not_found")
    assert error["message"] == "Not Found"


def test_wrong_method_is_405_and_keeps_the_allow_header(client):
    response = client.delete("/api/v1/health")
    assert_error_shape(response, 405, "method_not_allowed")
    assert response.headers["Allow"] == "GET"


def test_http_exception_from_an_endpoint(client):
    error = assert_error_shape(client.get("/test/not-found"), 404, "not_found")
    assert error["message"] == "Assessment not found"


def test_api_error_sets_its_own_code(client):
    error = assert_error_shape(client.get("/test/conflict"), 409, "invalid_state")
    assert error["message"] == "Assessment is not awaiting review"


def test_missing_field_is_a_validation_error(client):
    error = assert_error_shape(client.post("/test/items", json={"name": "x"}), 422, "validation_error")
    assert error["message"] == "body.quantity: Field required"


def test_extra_field_is_a_validation_error(client):
    response = client.post("/test/items", json={"name": "x", "quantity": 1, "is_admin": True})
    error = assert_error_shape(response, 422, "validation_error")
    assert error["message"] == "body.is_admin: Extra inputs are not permitted"


def test_validation_error_does_not_echo_input_values(client):
    response = client.post("/test/items", json={"name": "x", "quantity": "my-secret-value"})
    error = assert_error_shape(response, 422, "validation_error")
    assert "body.quantity" in error["message"]
    assert "my-secret-value" not in response.text


def test_valid_request_still_works(client):
    response = client.post("/test/items", json={"name": "x", "quantity": 2})
    assert response.status_code == 200
    assert response.json() == {"name": "x", "quantity": 2}


def test_unexpected_error_is_a_generic_500_without_details(client, caplog):
    response = client.get("/test/boom")
    error = assert_error_shape(response, 500, "internal_error")
    assert error["message"] == "Internal server error"
    assert "hunter2" not in response.text and "Traceback" not in response.text
    # ...but the server log has the traceback and the same request id.
    assert "secret detail" in caplog.text
    assert error["request_id"] in caplog.text


def test_every_response_gets_a_fresh_request_id(client):
    first = client.get("/api/v1/nope").headers["X-Request-ID"]
    second = client.get("/api/v1/nope").headers["X-Request-ID"]
    assert first != second


def test_incoming_request_id_is_ignored(client):
    response = client.get("/api/v1/nope", headers={"X-Request-ID": "injected\nfake log line"})
    assert response.headers["X-Request-ID"] != "injected\nfake log line"
    assert len(response.headers["X-Request-ID"]) == 32
