import logging

from api.server import app, query_log, slow_query_samples
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
    assert solr['cache'] is None


def test_status_full_includes_metrics():
    """?full=true fetches JVM, OS, and cache metrics."""
    client = TestClient(app)
    solr = client.get("/status?full=true").json()['solr']
    assert solr['jvm'] is not None
    assert solr['jvm']['heap_used_bytes'] is not None
    assert solr['os'] is not None
    # assert solr['cache'] is not None -- TODO: figure out why this doesn't work on our little Docker image.


def test_recent_queries_per_endpoint():
    """ After exercising /lookup, /synonyms, and /bulk-lookup the /status payload should
    expose per-endpoint latency stats keyed by the same endpoint names. """
    query_log.clear()
    client = TestClient(app)
    client.post("/lookup", params={'string': 'alzheimer', 'limit': 1})
    client.get("/synonyms", params={'preferred_curies': ['CHEBI:74925']})
    client.post("/bulk-lookup", json={'strings': ['Parkinson'], 'limit': 1})

    rq = client.get("/status").json()['recent_queries']
    assert 'per_endpoint' in rq
    per = rq['per_endpoint']
    assert set(per.keys()) >= {'lookup', 'synonyms', 'bulk-lookup'}
    for ep_stats in per.values():
        assert ep_stats['count'] >= 1
        assert 'mean_time_ms' in ep_stats
        assert 'latency_buckets' in ep_stats


def test_recent_queries_slow_samples_shape():
    """ Slow samples should be exposed as a list. We can't reliably trigger a slow sample
    against the test fixture (test queries are fast), so we just assert shape and append a
    synthetic record to verify the field is wired up to the deque. """
    slow_query_samples.clear()
    client = TestClient(app)
    rq = client.get("/status").json()['recent_queries']
    assert 'slow_samples' in rq
    assert rq['slow_samples'] == []

    slow_query_samples.append({
        'ts': 0.0, 'endpoint': 'lookup', 'duration_ms': 9999.99,
        'autocomplete': False, 'highlighting': False,
        'biolink_types_count': 0, 'only_prefixes': False,
        'exclude_prefixes': False, 'only_taxa': False,
    })
    rq = client.get("/status").json()['recent_queries']
    assert len(rq['slow_samples']) == 1
    assert rq['slow_samples'][0]['endpoint'] == 'lookup'
