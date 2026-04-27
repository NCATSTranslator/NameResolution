import logging

from api.server import app
from fastapi.testclient import TestClient

# Turn on debugging for tests.
logging.basicConfig(level=logging.DEBUG)

def test_status():
    client = TestClient(app)
    response = client.get("/status")
    status = response.json()

    assert status['status'] == 'ok'
    assert status['message'] != ''
    assert 'babel_version' in status
    assert 'babel_version_url' in status
    assert 'biolink_model' in status
    assert 'tag' in status['biolink_model']
    assert 'nameres_version' in status
    assert status['version'] > 1
    assert status['size'] != ''
    assert status['startTime']

    # Count the specific number of test documents we load.
    assert status['numDocs'] == 89
    assert status['maxDoc'] == 89
    assert status['deletedDocs'] == 0


def test_status_shape():
    """Verify /status returns expected fields including recent_queries; solr_metrics absent by default."""
    client = TestClient(app)
    response = client.get("/status")
    assert response.status_code == 200
    data = response.json()

    assert data['status'] == 'ok'
    assert 'numDocs' in data

    # recent_queries should always be present; count/means are None before any queries.
    rq = data['recent_queries']
    assert 'count' in rq
    assert 'mean_time_ms' in rq
    assert 'mean_solr_time_ms' in rq

    # solr_metrics should be present but with only a message unless ?metrics=true is passed.
    assert 'solr_metrics' in data and 'message' in data['solr_metrics']


def test_status_metrics_param():
    """With ?metrics=true, solr_metrics is included and has the expected structure."""
    client = TestClient(app)
    response = client.get("/status", params={'metrics': 'true'})
    assert response.status_code == 200
    data = response.json()

    assert 'solr_metrics' in data
    # solr_metrics may be None if Solr's metrics API is unavailable, but if present
    # it must contain the expected structure.
    if 'message' not in data['solr_metrics']:
        sm = data['solr_metrics']
        assert 'query_handler' in sm
        assert 'cache' in sm
        assert 'jvm' in sm
        assert 'requests' in sm['query_handler']
        assert 'hitratio' in sm['cache']
        assert 'heap_used_pct' in sm['jvm']


def test_status_recent_queries_populated():
    """After a lookup, recent_queries should reflect at least one recorded time."""
    client = TestClient(app)
    client.get("/lookup", params={'string': 'alzheimer'})
    response = client.get("/status")
    data = response.json()
    assert data['recent_queries']['count'] >= 1
    assert data['recent_queries']['mean_time_ms'] is not None
    assert data['recent_queries']['mean_solr_time_ms'] is not None
