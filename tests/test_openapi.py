"""
Tests for the OpenAPI document served at /openapi.json.

These go through TestClient on purpose. The regression in issue #294 was invisible to
any test that called construct_open_api_schema() directly: the builder kept returning
the right document throughout, while FastAPI served its own default one instead.
"""

from fastapi.testclient import TestClient

from api.apidocs import construct_open_api_schema
from api.server import app


def test_openapi_json_carries_translator_metadata():
    """The served spec must carry what openapi.yml declares, not FastAPI's default document."""
    openapi = TestClient(app).get("/openapi.json").json()
    info = openapi["info"]

    # x-translator.infores is what SmartAPI registration keys off: without it, the
    # deployment looks like an unregistered service.
    assert info["x-translator"]["infores"] == "infores:sri-name-resolver"
    assert info["x-translator"]["component"] == "Utility"
    assert info["x-translator"]["team"]
    assert info["termsOfService"]
    assert info["license"]["name"]
    assert info["contact"]["email"]
    assert info["contact"]["name"]
    assert info["description"]
    assert openapi["servers"]
    assert openapi["tags"]

    # Every server needs the maturity and location values ITRB sets.
    for server in openapi["servers"]:
        assert server["x-maturity"]
        assert server["x-location"]


def test_openapi_json_is_stable_across_requests():
    """Every request must get the same custom schema, not just the first one."""
    client = TestClient(app)

    first = client.get("/openapi.json")
    second = client.get("/openapi.json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


def test_server_metadata_respects_environment(monkeypatch):
    """SERVER_ROOT, MATURITY_VALUE and LOCATION_VALUE override the servers block.

    This calls the builder rather than the route because the served schema is cached
    after the first request, so it can't see an environment changed later.
    """
    monkeypatch.setenv("SERVER_ROOT", "/nameres")
    monkeypatch.setenv("MATURITY_VALUE", "testing")
    monkeypatch.setenv("LOCATION_VALUE", "RENCI")

    servers = construct_open_api_schema(app)["servers"]

    assert servers
    for server in servers:
        assert server["url"] == "/nameres/"
        assert server["x-maturity"] == "testing"
        assert server["x-location"] == "RENCI"
