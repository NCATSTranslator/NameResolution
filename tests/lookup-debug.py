import logging
import pytest

from api.server import app
from fastapi.testclient import TestClient

# Turn on debugging for tests.
logging.basicConfig(level=logging.DEBUG)


@pytest.mark.parametrize("debug_value", ["none", "query", "timing", "results", "all"])
def test_lookup_get_debug(debug_value):
    client = TestClient(app)
    params = {'string': 'beta-secretase', 'debug': debug_value}
    response = client.get("/lookup", params=params)
    results = response.json()
    assert isinstance(results, list)
    assert len(results) > 0
    if debug_value == 'none':
        assert results[0].get('debug') is None
    else:
        assert results[0].get('debug') is not None


@pytest.mark.parametrize("debug_value", ["none", "query", "timing", "results", "all"])
def test_lookup_post_debug(debug_value):
    client = TestClient(app)
    params = {'string': 'beta-secretase', 'debug': debug_value}
    response = client.post("/lookup", params=params)
    results = response.json()
    assert isinstance(results, list)
    assert len(results) > 0
    if debug_value == 'none':
        assert results[0].get('debug') is None
    else:
        assert results[0].get('debug') is not None


@pytest.mark.parametrize("debug_value", ["none", "query", "timing", "results", "all"])
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
    if debug_value == 'none':
        assert results['beta-secretase'][0].get('debug') is None
    else:
        assert results['beta-secretase'][0].get('debug') is not None
