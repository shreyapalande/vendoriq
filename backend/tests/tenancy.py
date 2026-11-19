"""The reusable cross-org check. Every endpoint that takes a resource id gets a test
that calls this (rule 1).

    login(client, user_in_org_b)
    assert_hidden_from_other_org(client, "GET", "/api/v1/vendors/{id}", vendor_of_org_a.id)

It sends the same request twice, once with the other org's real id and once with a
random id that exists nowhere, and asserts both give the same 404 with the same
error code and message. So the response cannot even reveal that the other org's
id exists, which a 403, or a differently worded 404, would.

For a mutating endpoint, the calling test should also check afterwards that the
other org's row is unchanged.
"""

import uuid

from fastapi.testclient import TestClient


def assert_hidden_from_other_org(
    client: TestClient, method: str, path_template: str, foreign_id: uuid.UUID, **request_kwargs,
) -> None:
    foreign = client.request(method, path_template.format(id=foreign_id), **request_kwargs)
    missing = client.request(method, path_template.format(id=uuid.uuid7()), **request_kwargs)

    assert foreign.status_code == 404, f"{method} {path_template} with another org's id: {foreign.status_code} {foreign.text}"
    assert missing.status_code == 404, f"{method} {path_template} with a random id: {missing.status_code} {missing.text}"

    foreign_error = foreign.json()["error"]
    missing_error = missing.json()["error"]
    assert foreign_error["code"] == "not_found"
    # Identical apart from the per-request id.
    assert (foreign_error["code"], foreign_error["message"]) == (missing_error["code"], missing_error["message"])
    assert str(foreign_id) not in foreign.text
