import asyncio
import logging
import math
import re

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

    # recent_queries is always present, latencies included (they need no Solr round-trip).
    rq = data['recent_queries']
    assert set(rq) == {'window_size', 'requests', 'errors', 'timeouts', 'cancelled',
                       'lookup_ms', 'solr_round_trip_ms'}
    # Grouped by what they time, so the names say which span each figure covers.
    for span in ('lookup_ms', 'solr_round_trip_ms'):
        assert set(rq[span]) == {'mean', 'min', 'max', 'p50', 'p95', 'p99'}, span

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
    """With ?full=true, Solr's health metrics are added to `solr`, read from Solr rather
    than left null.

    A wrong metric name or encoding yields null, not an error, so a check that a key is
    present proves nothing (see CLAUDE.md); each field is checked for a value.
    """
    client = TestClient(app)
    # Make sure the core has served at least one /select to report on.
    client.get("/lookup", params={'string': 'alzheimer'})
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

    queries = solr['queries']
    assert isinstance(queries['requests'], int) and queries['requests'] > 0
    # errors/timeouts are meters in Solr; we report just their counts.
    assert isinstance(queries['errors'], int)
    assert isinstance(queries['timeouts'], int)
    # Solr's handler time, grouped and named so it can't be taken for recent_queries'.
    handler = queries['handler_ms']
    assert set(handler) == {'mean', 'min', 'max', 'p50', 'p95', 'p99'}
    for field, value in handler.items():
        assert isinstance(value, (int, float)), field
        assert value == round(value, 2), field
    assert handler['min'] <= handler['p50'] <= handler['max']

    # The cache values are checked in test_status_reports_cache_metrics.
    assert 'filterCache' in solr['cache'] and 'queryResultCache' in solr['cache']

    jvm = solr['jvm']
    assert jvm['heap_used_mb'] > 0 and jvm['heap_max_mb'] > 0
    assert 0 < jvm['heap_used_pct'] <= 100
    # GC and OS resource fields drive Solr pod sizing decisions. A misread gc.* name gives
    # None. A healthy Solr with a large heap may not have collected yet, so 0 is fine.
    assert isinstance(jvm['gc_count'], int) and jvm['gc_count'] >= 0
    assert isinstance(jvm['gc_time_ms'], int) and jvm['gc_time_ms'] >= 0
    assert isinstance(solr['os']['available_processors'], int)
    assert solr['os']['total_physical_mem_mb'] > 0


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
    import api.server
    # The order statistics may otherwise be reused from an earlier /status in this process.
    api.server.recent_lookups._order_stats_at = float('-inf')
    client = TestClient(app)
    # Two queries so the percentile computation (needs >= 2 samples) is exercised.
    client.get("/lookup", params={'string': 'alzheimer'})
    client.get("/lookup", params={'string': 'diabetes'})
    response = client.get("/status")
    data = response.json()
    rq = data['recent_queries']
    assert rq['requests'] >= 2
    for field, value in rq['lookup_ms'].items():
        assert value is not None, field
    assert rq['lookup_ms']['min'] <= rq['lookup_ms']['p50'] <= rq['lookup_ms']['max']
    for field, value in rq['solr_round_trip_ms'].items():
        assert value is not None, field
    # The round trip is part of the lookup, so it can't be the slower of the two.
    assert rq['solr_round_trip_ms']['max'] <= rq['lookup_ms']['max']


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
        lambda request: httpx.Response(200, request=request, json={'metrics': {
            'solr.core.name_lookup': {'QUERY./select.requests': 1},
            'solr.jvm': {
                'gc.G1-Young-Generation.count': 3,
                'gc.G1-Young-Generation.time': 40,
                'gc.G1-Old-Generation.count': 'n/a',
            },
        }})))
    client = TestClient(app)
    solr = client.get("/status", params={'full': 'true'}).json()['solr']
    assert 'metrics_error' not in solr
    assert solr['jvm']['gc_count'] == 3
    assert solr['jvm']['gc_time_ms'] == 40


def test_status_reports_missing_gc_metrics_as_null(monkeypatch):
    """No gc.* gauges at all is unknown, not zero: a sum over nothing would report a
    misread metric name as a JVM that never collected garbage."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(200, request=request, json={'metrics': {
            'solr.core.name_lookup': {},
            'solr.jvm': {'os.availableProcessors': 4},
        }})))
    client = TestClient(app)
    jvm = client.get("/status", params={'full': 'true'}).json()['solr']['jvm']
    assert jvm['gc_count'] is None and jvm['gc_time_ms'] is None


@pytest.mark.parametrize("gauges, count, time_ms", [
    # G1 on JDK 20+: the concurrent bean times marking that runs beside the application.
    ({'gc.G1-Young-Generation.count': 8, 'gc.G1-Young-Generation.time': 36,
      'gc.G1-Old-Generation.count': 1, 'gc.G1-Old-Generation.time': 4,
      'gc.G1-Concurrent-GC.count': 5, 'gc.G1-Concurrent-GC.time': 900}, 9, 40),
    # ZGC, and generational ZGC: the Cycles beans are concurrent, the Pauses beans aren't.
    ({'gc.ZGC-Cycles.count': 3, 'gc.ZGC-Cycles.time': 700,
      'gc.ZGC-Pauses.count': 9, 'gc.ZGC-Pauses.time': 2}, 9, 2),
    ({'gc.ZGC-Minor-Cycles.time': 500, 'gc.ZGC-Minor-Pauses.time': 1,
      'gc.ZGC-Major-Cycles.time': 800, 'gc.ZGC-Major-Pauses.time': 3}, None, 4),
    ({'gc.Shenandoah-Cycles.time': 600, 'gc.Shenandoah-Pauses.time': 5}, None, 5),
])
def test_status_gc_totals_count_only_pauses(monkeypatch, gauges, count, time_ms):
    """gc_count and gc_time_ms are pause totals, so the beans that time concurrent GC work
    are left out (bean names as a Temurin 21 JVM reports them)."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(200, request=request, json={'metrics': {
            'solr.core.name_lookup': {},
            'solr.jvm': gauges,
        }})))
    client = TestClient(app)
    jvm = client.get("/status", params={'full': 'true'}).json()['solr']['jvm']
    assert jvm['gc_count'] == count and jvm['gc_time_ms'] == time_ms


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

    # errors and timeouts are disjoint: each failure counts in exactly one of them.
    assert after['timeouts'] == before['timeouts'] + (1 if outcome == 'timeout' else 0)
    assert after['errors'] == before['errors'] + (1 if outcome == 'error' else 0)
    assert after['requests'] == min(before['requests'] + 1, after['window_size'])
    assert any(r.levelno == logging.WARNING and f"FAILED QUERY ({outcome}" in r.getMessage()
               for r in caplog.records)


def test_lookup_failing_after_solr_answered_is_recorded(monkeypatch, caplog):
    """A lookup that fails while building its results, after Solr has answered, is a 500 to
    its caller like any other failure, so it must count in recent_queries.errors too."""
    async def unexpected_doc_post(self, url, **kwargs):
        # A document with no `id` can't be matched up with its highlighting.
        return httpx.Response(200, json={'response': {'docs': [{'curie': 'MONDO:0004975'}]}})
    monkeypatch.setattr(httpx.AsyncClient, "post", unexpected_doc_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get())
    client = TestClient(app, raise_server_exceptions=False)

    before = client.get("/status").json()['recent_queries']
    with caplog.at_level(logging.WARNING, logger="api.server"):
        assert client.get("/lookup", params={'string': 'alzheimer'}).status_code == 500
    after = client.get("/status").json()['recent_queries']

    assert after['errors'] == before['errors'] + 1
    assert after['requests'] == min(before['requests'] + 1, after['window_size'])
    assert any(r.levelno == logging.WARNING and "FAILED QUERY (error: KeyError)" in r.getMessage()
               for r in caplog.records)


def test_status_tolerates_unexpected_metric_values(monkeypatch):
    """An unexpected value costs only its own field, not every block: a non-numeric heap
    maximum once raised a TypeError that threw away the query, cache and OS figures too."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(200, request=request, json={'metrics': {
            'solr.core.name_lookup': {
                'QUERY./select.requests': 42,
                'QUERY./select.requestTimes': 'not a timer',
                'CACHE.searcher.filterCache': {'lookups': 9, 'hitratio': 'n/a'},
            },
            'solr.jvm': {
                'memory.heap.used': 1_048_576,
                'memory.heap.max': {'unexpected': 'shape'},
                'os.availableProcessors': 4,
            },
        }})))
    client = TestClient(app)
    solr = client.get("/status", params={'full': 'true'}).json()['solr']
    assert 'metrics_error' not in solr
    assert solr['queries']['requests'] == 42
    assert solr['queries']['handler_ms']['mean'] is None
    assert solr['cache']['filterCache']['lookups'] == 9
    assert solr['cache']['filterCache']['hitratio'] is None
    assert solr['jvm']['heap_used_mb'] == 1.0
    assert solr['jvm']['heap_max_mb'] is None and solr['jvm']['heap_used_pct'] is None
    assert solr['os']['available_processors'] == 4


@pytest.mark.parametrize("heap_max", [-1, 0])
def test_status_reports_undefined_heap_max_as_null(monkeypatch, heap_max):
    """The JVM reports a heap with no defined maximum as -1, which is unknown: it must not
    come out as a negative heap_max_mb or heap_used_pct (or, at 0, a ZeroDivisionError)."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(200, request=request, json={'metrics': {
            'solr.core.name_lookup': {},
            'solr.jvm': {'memory.heap.used': 1_048_576, 'memory.heap.max': heap_max},
        }})))
    client = TestClient(app)
    jvm = client.get("/status", params={'full': 'true'}).json()['solr']['jvm']
    assert jvm['heap_used_mb'] == 1.0
    assert jvm['heap_max_mb'] is None and jvm['heap_used_pct'] is None


# What a SolrCloud core looks like in core STATUS (as Solr 9.10 in cloud mode reports it):
# its metrics registry is solr.core.<collection>.<shard>.<replica>, with the replica taken
# from the core name, not the cloud descriptor's `replica`.
CLOUD_CORES = {'name_lookup_shard1_replica_n1': {
    'cloud': {'collection': 'name_lookup', 'shard': 'shard1', 'replica': 'core_node2'},
    'index': {'numDocs': 1},
}}


def test_status_reads_metrics_of_a_cloud_mode_core(monkeypatch):
    """The single-core fallback exists for cloud-mode cores, whose metrics registry has
    dots where the core name has underscores; reading solr.core.<coreName> found nothing
    and reported every query and cache field as null."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(200, request=request, json={'metrics': {
            'solr.core.name_lookup.shard1.replica_n1': {'QUERY./select.requests': 7},
            'solr.jvm': {},
        }}), cores=CLOUD_CORES))
    client = TestClient(app)
    solr = client.get("/status", params={'full': 'true'}).json()['solr']
    assert solr['core'] == 'name_lookup_shard1_replica_n1'
    assert 'metrics_error' not in solr
    assert solr['queries']['requests'] == 7


def test_status_reports_missing_core_metrics(monkeypatch):
    """If the core's metrics registry can't be found, say so instead of returning nulls --
    and don't borrow another core's registry -- while still reporting the JVM's figures."""
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_solr_get(
        lambda request: httpx.Response(200, request=request, json={'metrics': {
            'solr.core.some_other_core': {'QUERY./select.requests': 7},
            'solr.jvm': {'os.availableProcessors': 4},
        }})))
    client = TestClient(app)
    solr = client.get("/status", params={'full': 'true'}).json()['solr']
    assert 'name_lookup' in solr['metrics_error']
    assert 'queries' not in solr and 'cache' not in solr
    assert solr['os']['available_processors'] == 4


def test_bulk_lookup_is_not_in_recent_queries(monkeypatch, caplog):
    """recent_queries describes /lookup. A single /bulk-lookup can hold thousands of
    strings, so it would swamp the window -- and, with every slow string a SLOW QUERY
    warning, the log."""
    import api.server
    monkeypatch.setattr(api.server, "SLOW_QUERY_THRESHOLD_MS", 0)
    client = TestClient(app)
    before = client.get("/status").json()['recent_queries']['requests']
    with caplog.at_level(logging.INFO, logger="api.server"):
        response = client.post("/bulk-lookup", json={'strings': ['alzheimer', 'diabetes']})
    assert response.status_code == 200
    assert client.get("/status").json()['recent_queries']['requests'] == before
    # Each string is still logged, at INFO.
    lookup_lines = [r for r in caplog.records if "Lookup query to Solr" in r.getMessage()]
    assert len(lookup_lines) == 2
    assert not any("SLOW QUERY" in r.getMessage() for r in caplog.records)


def test_failed_bulk_lookup_warns_once_and_cancels_the_rest(monkeypatch, caplog):
    """A bulk request against a failing Solr logs one warning, not one per string, and
    stops sending its remaining strings to Solr once the request has failed."""
    import api.server
    calls = []

    async def first_fails_then_hangs(self, url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused")
        await asyncio.Event().wait()   # Never answers: only cancellation ends it.
    monkeypatch.setattr(httpx.AsyncClient, "post", first_fails_then_hangs)
    client = TestClient(app, raise_server_exceptions=False)

    strings = [f"string {i}" for i in range(3 * api.server.config.solr_max_concurrent_lookups)]
    with caplog.at_level(logging.INFO, logger="api.server"):
        assert client.post("/bulk-lookup", json={'strings': strings}).status_code == 500

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].startswith(f"FAILED BULK QUERY (ConnectError): Bulk lookup query for {len(strings)} strings")
    assert "1 failed" in warnings[0]
    # The strings that were waiting on Solr when the request failed were cancelled, and
    # those still waiting for a slot never reached it.
    assert any("FAILED QUERY (cancelled" in r.getMessage() and r.levelno == logging.INFO
               for r in caplog.records)
    assert len(calls) <= api.server.config.solr_max_concurrent_lookups + 1


def test_cancelled_lookup_is_recorded(monkeypatch, caplog):
    """A lookup cancelled while it waits on Solr is timed and counted as `cancelled`.
    CancelledError is a BaseException, which an `except Exception` lets past."""
    import api.server

    async def cancelled_post(self, url, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(httpx.AsyncClient, "post", cancelled_post)

    before = api.server.recent_lookups.summary()
    with caplog.at_level(logging.WARNING, logger="api.server"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(api.server.lookup('alzheimer'))
    after = api.server.recent_lookups.summary()
    assert after['cancelled'] == before['cancelled'] + 1
    assert after['errors'] == before['errors'] and after['timeouts'] == before['timeouts']
    assert any("FAILED QUERY (cancelled" in r.getMessage() for r in caplog.records)


def test_recent_lookups_window():
    """The counts stay exact as entries are evicted, and every figure is of what the
    window holds now."""
    from api.server import RecentLookups
    window = RecentLookups(3)
    window.record(1_000_000, 500_000)
    window.record(2_000_000, 500_000, 'timeout')
    window.record(3_000_000, 1_000_000)
    window.record(6_000_000, 2_000_000, 'error')   # evicts the first
    summary = window.summary()
    assert summary['requests'] == 3 and summary['window_size'] == 3
    assert summary['lookup_ms']['mean'] == round((2 + 3 + 6) / 3, 2)
    assert summary['solr_round_trip_ms']['mean'] == round((0.5 + 1 + 2) / 3, 2)
    assert summary['timeouts'] == 1 and summary['errors'] == 1 and summary['cancelled'] == 0
    assert summary['lookup_ms']['p50'] == 3.0
    # min and max are of what is in the window: the evicted 1 ms lookup is gone.
    assert summary['lookup_ms']['min'] == 2.0 and summary['lookup_ms']['max'] == 6.0
    # The round trip has its own order statistics, from its own times.
    round_trip = summary['solr_round_trip_ms']
    assert (round_trip['min'], round_trip['p50'], round_trip['max']) == (0.5, 1.0, 2.0)

    window.record(1_000_000, 0)   # evicts the timeout
    assert window.summary()['timeouts'] == 0


def test_recent_lookups_percentiles_are_cached(monkeypatch):
    """The latency figures cost a sort of the whole window, so /status reuses them for up
    to PERCENTILE_CACHE_SECONDS rather than re-sorting on every probe. A series' mean is
    cached with its min and max, so that the three always agree."""
    import api.server
    clock = [1000.0]
    monkeypatch.setattr(api.server.time, "monotonic", lambda: clock[0])
    window = api.server.RecentLookups(100)

    # Fewer than two lookups: min and max, but no percentiles, and nothing worth caching.
    window.record(1_000_000, 0)
    first = window.summary()['lookup_ms']
    assert first['min'] == first['max'] == 1.0 and first['p50'] is None
    window.record(1_000_000, 0)
    assert window.summary()['lookup_ms']['p50'] == 1.0

    window.record(100_000_000, 0)
    window.record(100_000_000, 0)
    cached = window.summary()
    assert cached['lookup_ms']['p50'] == 1.0 and cached['lookup_ms']['max'] == 1.0   # still cached
    # The mean is from the same moment as the max: a current mean of 50.5 beside a cached
    # max of 1.0 is what this used to report.
    assert cached['lookup_ms']['mean'] == 1.0
    assert cached['requests'] == 4   # but the counts are current

    clock[0] += api.server.PERCENTILE_CACHE_SECONDS
    fresh = window.summary()['lookup_ms']
    assert fresh['p50'] == 50.5 and fresh['max'] == 100.0 and fresh['mean'] == 50.5


def test_recent_lookups_percentiles_stay_within_observed_times():
    """A small window mustn't report a percentile slower than any lookup it holds: the
    default 'exclusive' method gave 1 ms and 100 ms lookups a p99 of 196 ms."""
    from api.server import RecentLookups
    window = RecentLookups(100)
    window.record(1_000_000, 0)
    window.record(100_000_000, 0)
    lookup_ms = window.summary()['lookup_ms']
    assert 1.0 == lookup_ms['min'] <= lookup_ms['p50'] <= lookup_ms['p95'] <= lookup_ms['p99'] <= lookup_ms['max'] == 100.0


def test_recent_lookups_window_of_zero_keeps_nothing():
    from api.server import RecentLookups
    window = RecentLookups(0)
    window.record(1_000_000, 0)
    summary = window.summary()
    assert summary['requests'] == 0
    assert all(value is None for value in summary['lookup_ms'].values())
    assert all(value is None for value in summary['solr_round_trip_ms'].values())


@pytest.mark.parametrize("raw", ["50k", "-1", "", "nan"])
def test_malformed_diagnostic_setting_falls_back(monkeypatch, caplog, raw):
    """A typo in RECENT_TIMES_COUNT or SLOW_QUERY_THRESHOLD_MS mustn't stop the API from
    starting: they only tune what /status and the logs report."""
    from api.server import diagnostic_setting
    monkeypatch.setenv("NAMERES_TEST_SETTING", raw)
    parse = float if raw == "nan" else int
    with caplog.at_level(logging.WARNING, logger="api.server"):
        assert diagnostic_setting("NAMERES_TEST_SETTING", 7, parse) == 7
    assert any("NAMERES_TEST_SETTING" in r.getMessage() for r in caplog.records)


def test_diagnostic_setting_reads_valid_values(monkeypatch):
    from api.server import diagnostic_setting
    monkeypatch.delenv("NAMERES_TEST_SETTING", raising=False)
    assert diagnostic_setting("NAMERES_TEST_SETTING", 7, int) == 7
    monkeypatch.setenv("NAMERES_TEST_SETTING", "0")
    assert diagnostic_setting("NAMERES_TEST_SETTING", 7, int) == 0
    monkeypatch.setenv("NAMERES_TEST_SETTING", "250.5")
    assert math.isclose(diagnostic_setting("NAMERES_TEST_SETTING", 7.0, float), 250.5)


# The lookup log line is parsed downstream (TranslatorSRI/babel-validation's NameRes log
# analysis, its PR #107: LOOKUP_LINE_RE in log-analysis/nameres/analyze_nameres_logs.py), so
# its shape is an interface. Change this pattern only with that parser.
LOOKUP_LOG_LINE = re.compile(
    r'^Lookup query to Solr for "alzheimer" \(autocomplete=False, .*\): '
    r'took (?P<took>[\d.]+)ms \(with (?P<solr>[\d.]+)ms for the Solr round trip\)$')


def test_lookup_log_line_format(caplog):
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="api.server"):
        client.get("/lookup", params={'string': 'alzheimer'})
    matches = [LOOKUP_LOG_LINE.match(r.getMessage()) for r in caplog.records]
    match = next(m for m in matches if m)
    assert float(match['solr']) <= float(match['took'])
