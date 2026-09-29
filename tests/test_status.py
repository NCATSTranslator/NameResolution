import logging

import httpx
import pytest

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
    assert 'failed' in rq and 'timed_out' in rq

    # solr_metrics should be present but with only a message unless ?full=true is passed.
    assert 'solr_metrics' in data and 'message' in data['solr_metrics']


def test_status_metrics_param():
    """With ?full=true, solr_metrics is included and has the expected structure."""
    client = TestClient(app)
    response = client.get("/status", params={'full': 'true'})
    assert response.status_code == 200
    data = response.json()

    sm = data['solr_metrics']
    # The test Solr is healthy, so the fetch should succeed; the failure paths are
    # covered by the faked-Solr tests below.
    assert 'error' not in sm, sm['error']
    assert 'message' not in sm
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


# The tests below fake Solr's responses by patching httpx.AsyncClient, so they exercise
# the failure paths without needing a broken Solr (or any Solr at all).

def _fake_solr_get(metrics_response=None):
    """An AsyncClient.get that answers the core STATUS call with one healthy core and
    /admin/metrics with metrics_response (a callable taking the request)."""
    async def get(self, url, params=None, **kwargs):
        request = httpx.Request("GET", url)
        if url.endswith("/admin/metrics"):
            return metrics_response(request)
        return httpx.Response(200, request=request, json={
            'status': {'name_lookup': {'startTime': '2026-01-01T00:00:00Z', 'index': {'numDocs': 1}}},
        })
    return get


def test_status_reports_metrics_http_error(monkeypatch, caplog):
    """A non-2xx from /admin/metrics is logged and reported, not mistaken for 'did not ask'."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(503, request=request, text="Solr is overloaded")))
    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="api.server"):
        response = client.get("/status", params={'full': 'true'})
    assert response.status_code == 200
    sm = response.json()['solr_metrics']
    assert 'message' not in sm
    assert '503' in sm['error']
    assert any("/solr/admin/metrics" in r.getMessage() and "503" in r.getMessage()
               for r in caplog.records)


def test_status_reports_metrics_exception(monkeypatch):
    """An exception while reading the metrics is reported, not swallowed into the placeholder."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(200, request=request, text="not json")))
    client = TestClient(app)
    sm = client.get("/status", params={'full': 'true'}).json()['solr_metrics']
    assert 'message' not in sm
    assert 'Could not retrieve Solr metrics' in sm['error']


def test_status_tolerates_non_numeric_gc_gauge(monkeypatch):
    """One non-numeric gc.* gauge must not throw away every other metric."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(200, request=request, json={'metrics': {'solr.jvm': {
            'gc.G1-Young-Generation.count': 3,
            'gc.G1-Young-Generation.time': 40,
            'gc.G1-Old-Generation.count': 'n/a',
        }}})))
    client = TestClient(app)
    sm = client.get("/status", params={'full': 'true'}).json()['solr_metrics']
    assert 'error' not in sm
    assert sm['jvm']['gc_count'] == 3
    assert sm['jvm']['gc_time_ms'] == 40


@pytest.mark.parametrize("failure, outcome", [
    (httpx.ReadTimeout("timed out"), 'timeout'),
    (httpx.ConnectError("connection refused"), 'error'),
])
def test_failed_lookup_is_recorded(monkeypatch, caplog, failure, outcome):
    """A lookup that fails -- above all one that times out -- still counts in recent_queries."""
    async def failing_post(self, url, **kwargs):
        raise failure
    monkeypatch.setattr(httpx.AsyncClient, "post", failing_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get())
    client = TestClient(app, raise_server_exceptions=False)

    before = client.get("/status").json()['recent_queries']
    with caplog.at_level(logging.WARNING, logger="api.server"):
        assert client.get("/lookup", params={'string': 'alzheimer'}).status_code == 500
    after = client.get("/status").json()['recent_queries']

    assert after['failed'] == before['failed'] + 1
    assert after['timed_out'] == before['timed_out'] + (1 if outcome == 'timeout' else 0)
    assert after['count'] == min(before['count'] + 1, after['max'])
    assert any(r.levelno == logging.WARNING and f"FAILED QUERY ({outcome}" in r.getMessage()
               for r in caplog.records)
