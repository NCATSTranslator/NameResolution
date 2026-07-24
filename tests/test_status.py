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

    # recent_queries should always be present; count/means/percentiles are None before any queries.
    rq = data['recent_queries']
    assert 'count' in rq
    assert 'mean_time_ms' in rq
    assert 'mean_solr_time_ms' in rq
    # End-to-end percentiles (local, no Solr round-trip) are on the default /status path.
    assert 'p50_ms' in rq and 'p95_ms' in rq and 'p99_ms' in rq

    # solr_metrics should be present but with only a message unless ?full=true is passed.
    assert 'solr_metrics' in data and 'message' in data['solr_metrics']


def test_status_metrics_param():
    """With ?full=true, solr_metrics is included and has the expected structure."""
    client = TestClient(app)
    response = client.get("/status", params={'full': 'true'})
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
        assert 'host' in sm
        assert 'requests' in sm['query_handler']
        assert 'filterCache' in sm['cache'] and 'queryResultCache' in sm['cache']
        assert 'hitratio' in sm['cache']['filterCache']
        assert 'heap_used_pct' in sm['jvm']
        # GC and host resource fields drive Solr pod sizing decisions.
        assert 'gc_count' in sm['jvm'] and 'gc_time_ms' in sm['jvm']
        assert 'available_processors' in sm['host']
        assert 'total_physical_mem_mb' in sm['host']
        # errors/timeouts should be scalar counts (or None), not nested meter dicts.
        assert not isinstance(sm['query_handler']['errors'], dict)


def test_status_recent_queries_populated():
    """After a lookup, recent_queries should reflect at least one recorded time."""
    client = TestClient(app)
    # Two queries so the percentile computation (needs >= 2 samples) is exercised.
    client.get("/lookup", params={'string': 'alzheimer'})
    client.get("/lookup", params={'string': 'diabetes'})
    response = client.get("/status")
    data = response.json()
    rq = data['recent_queries']
    assert rq['count'] >= 2
    assert rq['mean_time_ms'] is not None
    assert rq['mean_solr_time_ms'] is not None
    assert rq['p50_ms'] is not None and rq['p99_ms'] is not None


def test_slow_query_logs_warning(monkeypatch, caplog):
    """A lookup slower than SLOW_QUERY_THRESHOLD_MS logs at WARNING (as SLOW QUERY)."""
    import api.server
    # Threshold of 0 makes every real query count as slow (any query takes > 0ms).
    monkeypatch.setattr(api.server, "SLOW_QUERY_THRESHOLD_MS", 0)
    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="api.server"):
        client.get("/lookup", params={'string': 'alzheimer'})
    assert any(r.levelno == logging.WARNING and "SLOW QUERY" in r.getMessage()
               for r in caplog.records)


def test_fast_query_does_not_warn(monkeypatch, caplog):
    """Below the threshold, a lookup logs at INFO, not as a SLOW QUERY warning."""
    import api.server
    monkeypatch.setattr(api.server, "SLOW_QUERY_THRESHOLD_MS", 10_000_000)
    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="api.server"):
        client.get("/lookup", params={'string': 'diabetes'})
    assert not any("SLOW QUERY" in r.getMessage() for r in caplog.records)
