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
    assert sorted(second["paths"]) == [
        "/bulk-lookup", "/llms.txt", "/lookup", "/reverse_lookup", "/status", "/synonyms"
    ]


def test_lookup_result_fields_are_all_described():
    """/openapi.json is what an agent reads to learn the response shape without being told to.
    LookupResult's fields carried no descriptions at all until recently, so a new field added
    without one silently reopens that gap."""
    schema = construct_open_api_schema(app)
    properties = schema["components"]["schemas"]["LookupResult"]["properties"]

    undescribed = sorted(name for name, spec in properties.items() if not spec.get("description"))

    assert not undescribed, f"LookupResult fields with no description: {undescribed}"


def test_openapi_description_points_at_llms_txt():
    """The only way an agent given nothing but a base URL discovers the instructions."""
    description = construct_open_api_schema(app)["info"]["description"]

    assert "/llms.txt" in description
