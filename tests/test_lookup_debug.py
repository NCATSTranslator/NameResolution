# This file tests the new debug parameters on the lookup and bulk_lookup endpoints.
import json
import logging
import pytest

from api.server import app
from fastapi.testclient import TestClient

# Debug values (as per Solr https://solr.apache.org/guide/solr/latest/query-guide/common-query-parameters.html#debug-parameter)
DEBUG_VALUES = ["none", "query", "timing", "results", "all"]

# Turn on debugging for tests.
logging.basicConfig(level=logging.DEBUG)


def check_debug_output(debug_value, response):
    if debug_value not in DEBUG_VALUES:
        raise ValueError(f"Invalid debug value: {debug_value}")

    if debug_value == "none":
        assert response.get('debug') is None
        return

    if debug_value in ["timing", "all"]:
        timing = response.get('debug').get('timing')
        assert timing is not None
        assert isinstance(timing, dict)
        assert 'time' in timing

    if debug_value in ["results", "all"]:
        explain = response.get('explain')
        assert explain is not None
        assert isinstance(explain, dict)
        assert isinstance(explain.get('description'), str)
        assert isinstance(explain.get('details'), list)
        assert explain.get('details') != []

    if debug_value in ["query", "all"]:
        parsed_query = response.get('debug').get('parsedquery_toString')
        assert parsed_query is not None
        assert isinstance(parsed_query, str)
        assert parsed_query.startswith("FunctionScoreQuery(")

@pytest.mark.parametrize("debug_value", DEBUG_VALUES)
def test_lookup_get_debug(debug_value):
    client = TestClient(app)
    params = {'string': 'beta-secretase', 'debug': debug_value}
    response = client.get("/lookup", params=params)
    results = response.json()
    assert isinstance(results, list)
    assert len(results) > 0
    for result in results:
        check_debug_output(debug_value, result)

@pytest.mark.parametrize("debug_value", DEBUG_VALUES)
def test_lookup_post_debug(debug_value):
    client = TestClient(app)
    params = {'string': 'beta-secretase', 'debug': debug_value}
    response = client.post("/lookup", params=params)
    results = response.json()
    assert isinstance(results, list)
    assert len(results) > 0
    for result in results:
        check_debug_output(debug_value, result)

@pytest.mark.parametrize("debug_value", DEBUG_VALUES)
def test_bulk_lookup_debug(debug_value):
    client = TestClient(app)
    payload = {
        'strings': ['beta-secretase', 'Parkinson'],
        'limit': 100,
        'debug': debug_value
    }
    response = client.post("/bulk-lookup", json=payload)
    results = response.json()
    # Should return a dict mapping each input string to a list of results
    assert isinstance(results, dict)
    assert 'beta-secretase' in results
    assert isinstance(results['beta-secretase'], list)
    assert len(results['beta-secretase']) > 0
    for result in results['beta-secretase']:
        check_debug_output(debug_value, result)
