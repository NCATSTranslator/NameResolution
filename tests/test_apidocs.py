from api.apidocs import construct_open_api_schema
from api.server import app


def test_construct_open_api_schema_returns_cached_schema():
    """FastAPI caches the schema on the app, and construct_open_api_schema() hands the
    cached copy back on later calls. It used to call it -- `return app.openapi_schema()`
    -- which raises TypeError on a dict."""
    first = construct_open_api_schema(app)
    assert isinstance(first, dict)

    # Force the cached branch, whether or not importing the app already populated it.
    app.openapi_schema = first
    second = construct_open_api_schema(app)

    assert second is first
    assert sorted(second["paths"]) == ["/bulk-lookup", "/lookup", "/reverse_lookup", "/status", "/synonyms"]
