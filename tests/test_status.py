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

    solr = status['solr']
    assert solr['version'] != ''
    assert solr['size'] != ''
    assert solr['startTime']

    # Count the specific number of test documents we load.
    assert solr['numDocs'] == 88
    assert solr['maxDoc'] == 88
    assert solr['deletedDocs'] == 0


def test_status_default_excludes_metrics():
    """Default /status omits expensive metrics — only one Solr call needed."""
    client = TestClient(app)
    solr = client.get("/status").json()['solr']
    assert solr['numDocs'] is not None
    assert solr['jvm'] is None
    assert solr['os'] is None
    # assert solr['cache'] is None -- not set up on our little Docker setup


def test_status_full_includes_metrics():
    """?full=true fetches JVM, OS, and cache metrics."""
    client = TestClient(app)
    solr = client.get("/status?full=true").json()['solr']
    assert solr['jvm'] is not None
    assert solr['jvm']['heap_used_bytes'] is not None
    assert solr['os'] is not None
    assert solr['cache'] is not None
