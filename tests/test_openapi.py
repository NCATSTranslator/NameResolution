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


def test_building_the_schema_twice_leaves_the_first_alone(monkeypatch):
    """openapi.yml is parsed once and cached, so each build must get its own copy.

    construct_open_api_schema() rewrites the servers block in place from the
    environment; if it worked on the cached parse, a later build would reach back and
    edit a document already served to somebody.
    """
    monkeypatch.setenv("MATURITY_VALUE", "development")
    first = construct_open_api_schema(app)

    monkeypatch.setenv("MATURITY_VALUE", "production")
    second = construct_open_api_schema(app)

    assert first["servers"][0]["x-maturity"] == "development"
    assert second["servers"][0]["x-maturity"] == "production"
