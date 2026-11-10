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

    # Issue #296: `backend` tells this Solr implementation apart from the
    # Elasticsearch-backed NameRes (biothings/NameResolutionAPI), which reports
    # 'elasticsearch' here.
    assert status['backend'] == 'solr'

    # `version` is the API version, and must be identical to the version in the served
    # OpenAPI document -- that identity is the whole point of the field, so it is pinned
    # against /openapi.json rather than against a literal that would need editing on
    # every version bump.
    assert status['version'] == client.get("/openapi.json").json()['info']['version']
    assert status['nameres_version'] == 'v' + status['version']

    # Everything read from Solr is grouped under 'solr'. Until #296 these fields sat at
    # the top level, where the index version was 'version'; make sure none of them has
    # crept back out.
    for key in ('numDocs', 'maxDoc', 'deletedDocs', 'segmentCount', 'size', 'startTime',
                'lastModified', 'index_version'):
        assert key not in status
    solr = status['solr']

    assert solr['core'] == 'name_lookup'
    assert solr['version'] > 1
    assert solr['size'] != ''
    assert solr['startTime']

    # Count the specific number of test documents we load.
    assert solr['numDocs'] == 89
    assert solr['maxDoc'] == 89
    assert solr['deletedDocs'] == 0


def test_status_shape():
    """Verify /status returns expected fields including recent_queries; no Solr metrics by default."""
    client = TestClient(app)
    response = client.get("/status")
    assert response.status_code == 200
    data = response.json()

    assert data['status'] == 'ok'
    assert 'numDocs' in data['solr']

    # recent_queries should always be present; count/means/percentiles are None before any queries.
    rq = data['recent_queries']
    assert 'count' in rq
    assert 'mean_time_ms' in rq
    assert 'mean_solr_time_ms' in rq
    # End-to-end percentiles (local, no Solr round-trip) are on the default /status path.
    assert 'p50_ms' in rq and 'p95_ms' in rq and 'p99_ms' in rq
    assert 'failed' in rq and 'timed_out' in rq

    # Solr's health metrics are an extra round-trip, so they appear only with ?full=true.
    # They live under 'solr' -- never at the top level, as solr_metrics once did.
    assert 'solr_metrics' not in data
    for key in ('queries', 'cache', 'jvm', 'os', 'metrics_error'):
        assert key not in data['solr']


def test_status_reports_solr_last():
    """`solr` comes last, after config and recent_queries, which are what a person
    reading /status mostly wants; it is a long block of diagnostics. JSON consumers must
    not rely on key order -- this pins only what a reader sees first."""
    client = TestClient(app)
    for params in ({}, {'full': 'true'}):
        keys = list(client.get("/status", params=params).json())
        assert keys[-1] == 'solr'
        assert keys.index('config') < keys.index('recent_queries') < keys.index('solr')


def test_status_metrics_param():
    """With ?full=true, Solr's health metrics are added to `solr`, with the expected structure."""
    client = TestClient(app)
    response = client.get("/status", params={'full': 'true'})
    assert response.status_code == 200
    data = response.json()

    assert 'solr_metrics' not in data
    solr = data['solr']
    # The test Solr is healthy, so the fetch should succeed; the failure paths are
    # covered by the faked-Solr tests below.
    assert 'metrics_error' not in solr, solr['metrics_error']
    # The core's own stats are still there alongside the metrics.
    assert solr['numDocs'] == 89
    assert 'requests' in solr['queries']
    assert 'filterCache' in solr['cache'] and 'queryResultCache' in solr['cache']
    assert 'hitratio' in solr['cache']['filterCache']
    assert 'heap_used_pct' in solr['jvm']
    # GC and OS resource fields drive Solr pod sizing decisions.
    assert 'gc_count' in solr['jvm'] and 'gc_time_ms' in solr['jvm']
    assert isinstance(solr['os']['available_processors'], int)
    assert solr['os']['total_physical_mem_mb'] > 0
    # errors/timeouts should be scalar counts (or None), not nested meter dicts.
    assert not isinstance(solr['queries']['errors'], dict)


def test_status_reports_cache_metrics():
    """The cache figures are read from Solr, not left null.

    Solr 9 registers its searcher caches as CACHE.searcher.*; reading CACHE.core.* instead
    returned null for every field, which a key-presence check cannot catch.
    """
    client = TestClient(app)
    # A type filter goes through filterCache, so it has at least one lookup to report.
    client.get("/lookup", params={'string': 'Parkinson', 'biolink_type': 'biolink:Disease'})
    cache = client.get("/status", params={'full': 'true'}).json()['solr']['cache']
    filter_cache = cache['filterCache']
    assert isinstance(filter_cache['lookups'], int) and filter_cache['lookups'] > 0
    assert isinstance(cache['queryResultCache']['lookups'], int)


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

HEALTHY_CORES = {'name_lookup': {'startTime': '2026-01-01T00:00:00Z', 'index': {'numDocs': 1}}}


def _fake_solr_get(metrics_response=None, cores=HEALTHY_CORES):
    """An AsyncClient.get that answers the core STATUS call with `cores` (by default one
    healthy core) and /admin/metrics with metrics_response (a callable taking the request)."""
    async def get(self, url, params=None, **kwargs):
        request = httpx.Request("GET", url)
        if url.endswith("/admin/metrics"):
            return metrics_response(request)
        return httpx.Response(200, request=request, json={'status': cores})
    return get


def test_status_reports_metrics_http_error(monkeypatch, caplog):
    """A non-2xx from /admin/metrics is logged and reported, not mistaken for 'did not ask'."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(503, request=request, text="Solr is overloaded")))
    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="api.server"):
        response = client.get("/status", params={'full': 'true'})
    assert response.status_code == 200
    solr = response.json()['solr']
    assert '503' in solr['metrics_error']
    # The core's own stats still come through when only the metrics fetch failed.
    assert solr['numDocs'] == 1
    assert any("/solr/admin/metrics" in r.getMessage() and "503" in r.getMessage()
               for r in caplog.records)


def test_status_reports_metrics_exception(monkeypatch):
    """An exception while reading the metrics is reported, not swallowed into an absent block."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(200, request=request, text="not json")))
    client = TestClient(app)
    solr = client.get("/status", params={'full': 'true'}).json()['solr']
    assert 'Could not retrieve Solr metrics' in solr['metrics_error']
    assert 'jvm' not in solr


def test_status_tolerates_non_numeric_gc_gauge(monkeypatch):
    """One non-numeric gc.* gauge must not throw away every other metric."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(200, request=request, json={'metrics': {'solr.jvm': {
            'gc.G1-Young-Generation.count': 3,
            'gc.G1-Young-Generation.time': 40,
            'gc.G1-Old-Generation.count': 'n/a',
        }}})))
    client = TestClient(app)
    solr = client.get("/status", params={'full': 'true'}).json()['solr']
    assert 'metrics_error' not in solr
    assert solr['jvm']['gc_count'] == 3
    assert solr['jvm']['gc_time_ms'] == 40


# Two cores, neither of them name_lookup, so the single-core fallback can't rescue it and
# status() takes its error branch.
MISPOINTED_CORES = {'other_a': {}, 'other_b': {}}


def test_status_error_branch_reports_only_jvm_metrics(monkeypatch):
    """With no core to report on, ?full=true still gives the JVM's metrics, but no
    query or cache figures -- and none of those from some other core."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(200, request=request, json={'metrics': {
            'solr.core.other_a': {'QUERY./select.requests': 7},
            'solr.jvm': {'os.availableProcessors': 4},
        }}), cores=MISPOINTED_CORES))
    client = TestClient(app)

    data = client.get("/status").json()
    assert data['status'] == 'error'
    assert 'solr' not in data

    data = client.get("/status", params={'full': 'true'}).json()
    assert data['status'] == 'error'
    assert data['backend'] == 'solr'
    assert set(data['solr']) == {'jvm', 'os'}
    assert data['solr']['os']['available_processors'] == 4


def test_status_reads_metrics_of_the_resolved_core(monkeypatch):
    """Query figures come from the core status() reports on, not whichever is listed first."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(200, request=request, json={'metrics': {
            'solr.core.another_core': {'QUERY./select.requests': 7},
            'solr.core.name_lookup': {'QUERY./select.requests': 42},
        }}), cores={**HEALTHY_CORES, 'another_core': {}}))
    client = TestClient(app)
    solr = client.get("/status", params={'full': 'true'}).json()['solr']
    assert solr['core'] == 'name_lookup'
    assert solr['queries']['requests'] == 42


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
