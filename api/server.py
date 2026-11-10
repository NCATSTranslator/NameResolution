"""
Name Resolver (Name Lookup, NameRes) API Endpoints

Queries are mostly sent to the underlying the NameRes Solr instance.
"""
import asyncio
import html
import json
import logging
import statistics
import warnings
import weakref
import time
import os
import re
from collections import Counter, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Union, Annotated, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
import httpx
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

from api.apidocs import get_app_info, construct_open_api_schema

@dataclass(frozen=True)
class Config:
    """Runtime configuration, populated from environment variables at import."""
    # Solr connection (private — not exposed via /status).
    solr_host: str = os.getenv("SOLR_HOST", "localhost")
    solr_port: str = os.getenv("SOLR_PORT", "8983")

    # The Solr core to query. In standalone mode this is the core name; the cloud-mode
    # backups we used to ship called it name_lookup_shard1_replica_n1 instead (see status()).
    solr_core: str = os.getenv("SOLR_CORE", "name_lookup")

    # The maximum number of Solr queries a single /bulk-lookup request may have in flight at once.
    # bulk_lookup() runs its per-string lookups concurrently, and `strings` is unbounded, so without
    # this a large bulk request would open one socket per string -- enough to exhaust this process's
    # file descriptors and to stampede Solr. Raising this trades Solr load for bulk-lookup latency.
    #
    # Note that this bounds a *single* request, not the process: the Solr queries in flight across
    # the whole service is this multiplied by the number of concurrent /bulk-lookup requests being
    # served. 100 is set deliberately high on the assumption that Solr can take the strain, and
    # should be revisited once we know the real request rate -- see TranslatorSRI/babel-validation#107.
    #
    # Clamped to at least 1: a value of 0 would produce a semaphore nobody can acquire, wedging
    # every /bulk-lookup request forever with no error and no log line, which is a miserable thing
    # to debug for what is usually a typo in a deployment's environment.
    solr_max_concurrent_lookups: int = max(1, int(os.getenv("SOLR_MAX_CONCURRENT_LOOKUPS", "100")))

    # How long to wait for Solr before giving up on a single query, in seconds.
    #
    # This matters more than it used to. When bulk_lookup() ran its lookups sequentially, a stalled
    # Solr connection held up one query; now that they run concurrently, one stalled connection can
    # pin an otherwise-complete bulk request indefinitely while holding a semaphore slot. Set it to
    # 0 to restore the previous behaviour of waiting forever.
    solr_timeout_seconds: float = float(os.getenv("SOLR_TIMEOUT_SECONDS", "60"))

    # Queries shorter than this (after strip) are rejected: single-char queries
    # are slow in Solr and never useful. Translator expects results at length 2.
    minimum_query_length: int = int(os.getenv("NAMERES_MINIMUM_QUERY_LENGTH", "2"))

    @property
    def solr_timeout(self) -> Optional[float]:
        """The per-query httpx timeout; None (from a non-positive setting) waits forever."""
        return self.solr_timeout_seconds if self.solr_timeout_seconds > 0 else None

    def public(self) -> dict:
        """Config values safe to surface via /status. Infra config stays private."""
        return {"minimum_query_length": self.minimum_query_length}


config = Config()


# One httpx client per event loop, shared by every Solr request, so that requests reuse
# kept-alive connections. A new client per query (as lookup() used to make) builds an SSL
# context and opens a TCP connection each time: on a local Solr that took a 1 ms query to about
# 8 ms, most of what /status reported as solr_round_trip_ms.
#
# Keyed by loop because an httpx client's connections belong to the loop that opened them.
# Uvicorn serves everything from one loop, so in production this is a single client; the
# key matters for TestClient and asyncio.run(), which start a fresh loop per call, and
# would otherwise be handed connections from a loop that has gone. Weak keys let a dead
# loop's client go with it.
_solr_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = \
    weakref.WeakKeyDictionary()


def solr_client() -> httpx.AsyncClient:
    """ The shared Solr client for the running event loop, created on first use. """
    loop = asyncio.get_running_loop()
    client = _solr_clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=config.solr_timeout,
            # No cap on connections, as when each query had its own client: the in-flight
            # bound is SOLR_MAX_CONCURRENT_LOOKUPS per /bulk-lookup request, and a pool cap
            # below that would make queries wait for a connection -- inside
            # SOLR_TIMEOUT_SECONDS, so a busy pool would show up as Solr timeouts. Keep up
            # to that many idle connections alive for reuse.
            limits=httpx.Limits(max_connections=None,
                                max_keepalive_connections=config.solr_max_concurrent_lookups),
        )
        _solr_clients[loop] = client
    return client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """ Close the shared Solr client when the server shuts down. """
    yield
    client = _solr_clients.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.aclose()


app = FastAPI(**get_app_info(), lifespan=lifespan)
logger = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOGLEVEL", logging.INFO))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def diagnostic_setting(name: str, default, parse):
    """
    Read a diagnostics-only setting from the environment, falling back to `default` (with a
    warning) if it is missing, malformed or negative.

    Unlike the settings in Config, which change what the service returns and so ought to
    stop it starting when they're wrong, these only tune what /status and the logs report.
    A typo in one of them ('50k', '-1') shouldn't keep the API container from starting.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = parse(raw)
    except ValueError:
        value = None
    # `not >= 0` rather than `< 0`, so that a NaN is turned away too.
    if value is None or not value >= 0:
        logger.warning("Ignoring %s=%r: expected a non-negative number; using %r instead.",
                       name, raw, default)
        return default
    return value


# We keep the last RECENT_TIMES_COUNT lookups' timings so /status can report on them as
# recent_queries. 50000 entries is only a few MB of memory but gives a much longer
# performance window than 1000 did. 0 keeps nothing.
DEFAULT_RECENT_TIMES_COUNT = 50000
RECENT_TIMES_COUNT = diagnostic_setting("RECENT_TIMES_COUNT", DEFAULT_RECENT_TIMES_COUNT, int)

# How long /status may reuse the latency figures (mean, min, max and percentiles) it last
# computed; see RecentLookups.summary().
PERCENTILE_CACHE_SECONDS = 10


class RecentLookups:
    """
    The last `maxlen` /lookup timings and outcomes, which /status reports as recent_queries.

    /status is what the Kubernetes probes call, so it has to stay cheap however full the
    window is. The counts are therefore running totals, updated as each entry is added and
    evicted, rather than a pass over the window per request. The latency figures need a
    sort of the whole window (a few ms at 50000 entries, on the event loop, for each of the
    two series), so summary() reuses the last ones for up to PERCENTILE_CACHE_SECONDS.

    A series' mean is computed in the same pass as its min, max and percentiles, though a
    running total could keep it current: a current mean beside a min and max up to 10 s old
    reported a mean above the max after a burst of slow lookups, and figures that disagree
    with each other read as broken just when latency is rising. So each series' figures
    describe one moment, while the counts are current. Times are integer nanoseconds, so a
    mean is an exact sum however many entries pass through.

    Everything here runs on the event loop with no await, so it needs no lock.
    """

    def __init__(self, maxlen: int):
        self.maxlen = maxlen
        # (total_ns, solr_ns, outcome) per lookup, oldest first. Evicted by hand, not by
        # deque(maxlen=...), so that the totals below can subtract what leaves.
        self._entries = deque()
        self._outcomes = Counter()
        # Lookups ever recorded: tells summary() whether its cached order statistics are stale.
        self._recorded = 0
        # Per series: 'lookup' (entry[0]) and 'solr_round_trip' (entry[1]).
        self._order_stats = {series: self._NO_ORDER_STATS for series in self._SERIES}
        self._order_stats_recorded = 0
        self._order_stats_at = 0.0

    _NO_ORDER_STATS = {'mean': None, 'min': None, 'max': None, 'p50': None, 'p95': None, 'p99': None}
    # Which element of an entry each timed series is.
    _SERIES = {'lookup': 0, 'solr_round_trip': 1}

    @classmethod
    def _order_stats_of(cls, times_ns: List[int]) -> Dict:
        """ mean, min, max, p50, p95 and p99 of `times_ns` (sorted), in ms. """
        stats = dict(cls._NO_ORDER_STATS)
        if times_ns:
            stats['mean'] = sum(times_ns) / len(times_ns)
            stats['min'], stats['max'] = times_ns[0], times_ns[-1]
        if len(times_ns) >= 2:
            # 'inclusive' keeps every percentile within the observed times. The default
            # ('exclusive') extrapolates past them in a small window: 1 ms and 100 ms
            # lookups gave a p99 of 196 ms, a delay no caller had.
            qs = statistics.quantiles(times_ns, n=100, method='inclusive')
            stats['p50'], stats['p95'], stats['p99'] = qs[49], qs[94], qs[98]
        return {k: None if v is None else round(v / 1_000_000, 2) for k, v in stats.items()}

    def record(self, total_ns: int, solr_ns: int, outcome: str = 'ok'):
        """ Add one lookup, evicting the oldest if the window is full. `outcome` is how it
        ended: 'ok', or one of the failures 'error', 'timeout' and 'cancelled'. """
        if self.maxlen <= 0:
            return
        if len(self._entries) >= self.maxlen:
            _, _, old_outcome = self._entries.popleft()
            self._outcomes[old_outcome] -= 1
        self._entries.append((total_ns, solr_ns, outcome))
        self._outcomes[outcome] += 1
        self._recorded += 1

    def _current_order_stats(self) -> Dict:
        """ The latency figures (mean and order statistics) of each series, keyed as
        _SERIES. Recomputed only if lookups
        have arrived since the last computation and it is more than PERCENTILE_CACHE_SECONDS
        old -- or it was made before there were the two lookups that percentiles need. One
        sort per series, so two sorts of the window per recomputation. """
        now = time.monotonic()
        stale = self._recorded != self._order_stats_recorded and (
            self._order_stats['lookup']['p50'] is None
            or now - self._order_stats_at >= PERCENTILE_CACHE_SECONDS)
        if stale:
            self._order_stats = {
                series: self._order_stats_of(sorted(entry[i] for entry in self._entries))
                for series, i in self._SERIES.items()
            }
            self._order_stats_recorded = self._recorded
            self._order_stats_at = now
        return self._order_stats

    def summary(self) -> Dict:
        """ The recent_queries block of /status. """
        count = len(self._entries)

        order_stats = self._current_order_stats()

        # The latencies are grouped by what they time, and named for it, so that nobody
        # reading /status takes lookup_ms for solr.queries.handler_ms: they time different
        # spans, and the gap between them is NameRes's own overhead. The counts aren't
        # comparable with solr.queries' either: these cover the window, Solr's count from
        # its start.
        return {
            # The window's capacity (RECENT_TIMES_COUNT), not a latency.
            'window_size': self.maxlen,
            'requests': count,
            # Lookups in the window that failed, split by how: `timeouts` hit
            # SOLR_TIMEOUT_SECONDS, `cancelled` were abandoned while waiting on Solr (a
            # server shutdown, say), and `errors` are everything else (Solr returned an
            # error or couldn't be reached, or its answer couldn't be made into results). Disjoint, so their sum is every failure.
            # Their times are in the figures below: a failed lookup kept its caller
            # waiting too. Not the same as solr.queries.timeouts, which counts only
            # requests Solr itself cut short.
            'errors': self._outcomes['error'],
            'timeouts': self._outcomes['timeout'],
            'cancelled': self._outcomes['cancelled'],
            # The whole of lookup(): Solr round trip plus building the results. It doesn't
            # include FastAPI's handling of the request or the network to the caller.
            'lookup_ms': order_stats['lookup'],
            # The part of lookup_ms spent on the Solr request, as NameRes sees it: setting
            # up the HTTP client and connection, Solr's handling (solr.queries.handler_ms),
            # the response's transfer, and decoding its JSON. The lookup log line's
            # "for the Solr round trip" figure.
            'solr_round_trip_ms': order_stats['solr_round_trip'],
        }


recent_lookups = RecentLookups(RECENT_TIMES_COUNT)

# Lookups slower than this (end-to-end, in ms) are logged at WARNING instead of INFO,
# so slow queries stand out in the logs. See documentation/Performance.md.
SLOW_QUERY_THRESHOLD_MS = diagnostic_setting("SLOW_QUERY_THRESHOLD_MS", 500.0, float)

# ENDPOINT /
# If someone tries accessing /, we should redirect them to the Swagger interface.
@app.get("/", include_in_schema=False)
async def docs_redirect():
    """
    Redirect requests to `/` (where we don't have any content) to `/docs` (which is our Swagger interface).
    """
    return RedirectResponse(url='/docs')


def core_metrics_registry(all_metrics: Dict, core_name: str, core: Dict) -> Optional[str]:
    """
    The name of `core_name`'s registry in Solr's /admin/metrics response, or None if it
    isn't there.

    A standalone core's registry is solr.core.<coreName>, but a SolrCloud core's is
    solr.core.<collection>.<shard>.<replica>: the cloud-mode core
    name_lookup_shard1_replica_n1 reports under solr.core.name_lookup.shard1.replica_n1
    (checked against a live Solr 9.10 in cloud mode). The replica part is the tail of the
    core name, not the `replica` in the core's cloud descriptor (that is core_node2 or the
    like). There is deliberately no fallback to some other solr.core.* registry: that would
    report another core's figures as this one's.
    """
    candidates = [f'solr.core.{core_name}']
    cloud = core.get('cloud') if isinstance(core.get('cloud'), dict) else {}
    collection, shard = cloud.get('collection'), cloud.get('shard')
    if collection and shard and core_name.startswith(f'{collection}_{shard}_'):
        replica = core_name[len(f'{collection}_{shard}_'):]
        candidates.append(f'solr.core.{collection}.{shard}.{replica}')
    return next((name for name in candidates if name in all_metrics), None)


def _number(value):
    """ value if it is a number, else None: one odd gauge mustn't fail the whole block. """
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _dict(value) -> Dict:
    """ value if it is a dict, else {}, so that .get() on it can't raise. """
    return value if isinstance(value, dict) else {}


async def fetch_solr_metrics(core_name: Optional[str], core: Optional[Dict] = None) -> Dict:
    """
    Read Solr's own health metrics from /admin/metrics, for /status?full=true.

    Returns the `queries`, `cache`, `jvm` and `os` blocks that status() spreads into its
    `solr` object, or just `jvm` and `os` when core_name is None (there is no core to read
    query or cache figures from). `core` is that core's entry in Solr's core STATUS, which
    says where a cloud-mode core keeps its metrics. If the fetch fails, returns
    {'metrics_error': ...} instead, so that a caller who asked for metrics can tell a
    failure from not having asked. If only the core's figures can't be found, the `jvm`
    and `os` blocks come with a metrics_error saying so, rather than a block of nulls.

    Each value is checked before it is used, so that one unexpected value costs only its
    own field. The except at the bottom is a backstop for a response that isn't the shape
    of a metrics response at all.
    """
    metrics_url = f"http://{config.solr_host}:{config.solr_port}/solr/admin/metrics"
    try:
        client = solr_client()
        # A single call with group=core&group=jvm retrieves both in one round-trip.
        metrics_resp = await client.get(metrics_url, params=[
            ('group', 'core'),
            ('group', 'jvm'),
            ('prefix', 'QUERY./select'),
            # Solr 9 registers the searcher caches as CACHE.searcher.*, not
            # CACHE.core.* -- a wrong name here yields nulls, not an error.
            ('prefix', 'CACHE.searcher.filterCache'),
            ('prefix', 'CACHE.searcher.queryResultCache'),
            ('prefix', 'memory.heap'),
            ('prefix', 'gc.'),
            ('prefix', 'os.'),
            ('wt', 'json'),
        ])
        if metrics_resp.status_code >= 300:
            logger.warning("Solr error on accessing /solr/admin/metrics: HTTP %d: %s",
                           metrics_resp.status_code, metrics_resp.text)
            return {'metrics_error': f"Solr's /admin/metrics returned HTTP {metrics_resp.status_code}."}
        all_metrics = _dict(metrics_resp.json().get('metrics'))

        # A memory size of -1 is the JVM's 'undefined' (MemoryUsage.getMax() with no
        # maximum set), so a negative size is unknown, not a size.
        def _mb(n):
            return round(n / 1_048_576, 1) if _number(n) is not None and n >= 0 else None

        metrics = {}

        # Query and cache figures belong to a core; without one there are none to report.
        # Read the registry of the core status() resolved, rather than whichever
        # solr.core.* comes first, so a Solr with more than one core can't report another
        # core's figures.
        registry = core_metrics_registry(all_metrics, core_name, _dict(core)) if core_name else None
        if core_name and registry is None:
            # Without this, every queries and cache field would quietly come back null.
            logger.warning("No metrics registry for Solr core %s among %s", core_name, list(all_metrics))
            metrics['metrics_error'] = f"Solr's /admin/metrics has no metrics for core {core_name}."
        if registry:
            core_data = _dict(all_metrics[registry])
            qh = _dict(core_data.get('QUERY./select.requestTimes'))

            # requests is a plain counter, but errors/timeouts are meters
            # ({count, meanRate, ...}); report just the cumulative count.
            def _count(metric):
                return _number(metric.get('count') if isinstance(metric, dict) else metric)

            # NameRes leans on fq (prefix/type/taxon filters), so filterCache matters
            # as much as queryResultCache. Rising evictions => cache too small.
            def _cache(name):
                c = _dict(core_data.get(f'CACHE.searcher.{name}'))
                return {field: _number(c.get(field))
                        for field in ('hitratio', 'lookups', 'hits', 'evictions', 'size')}

            # Every /select this core served -- /synonyms and /reverse_lookup as well as
            # /lookup -- unlike the top-level recent_queries, which times /lookup alone.
            metrics['queries'] = {
                'requests': _count(core_data.get('QUERY./select.requests')),
                'errors': _count(core_data.get('QUERY./select.errors')),
                'timeouts': _count(core_data.get('QUERY./select.timeouts')),
                # Time inside Solr's /select handler only, from Solr's request timer, which
                # samples roughly the last few minutes. No network, no connection setup,
                # no response writing: compare recent_queries.solr_round_trip_ms for those.
                # Rounded to 2 places, like recent_queries.
                'handler_ms': {
                    name: round(v, 2) if (v := _number(qh.get(f'{solr_name}_ms'))) is not None else None
                    for name, solr_name in (('mean', 'mean'), ('min', 'min'), ('max', 'max'),
                                            ('p50', 'median'), ('p95', 'p95'), ('p99', 'p99'))
                },
            }
            metrics['cache'] = {
                'filterCache': _cache('filterCache'),
                'queryResultCache': _cache('queryResultCache'),
            }

        jvm = _dict(all_metrics.get('solr.jvm'))

        # Solr 9 returns JVM gauges as flat dotted keys ('memory.heap.used'), not as a
        # nested 'memory.heap' map; see the Gotchas in CLAUDE.md.
        heap_used = _number(jvm.get('memory.heap.used'))
        heap_max = _number(jvm.get('memory.heap.max'))
        if heap_max is not None and heap_max <= 0:
            heap_max = None    # -1: no maximum defined, so no percentage of it either.

        # GC pause totals across whichever collectors are configured (G1, etc.).
        # High gc_time_ms relative to uptime points at heap pressure — a heap-sizing signal.
        # Skip anything non-numeric rather than letting one odd gauge fail the whole block.
        # No usable gauge at all is None, not 0: a sum of nothing would report a misread
        # metric name, or a Solr that stopped sending them, as a JVM that never collected.
        #
        # Solr names each gauge after a GarbageCollectorMXBean, spaces turned to hyphens.
        # Some beans time concurrent work that runs beside the application rather than
        # pausing it, and are left out so that the totals stay pause totals. As checked on
        # Temurin 21: G1 adds 'G1 Concurrent GC' (from JDK 20) to 'G1 Young Generation' and
        # 'G1 Old Generation'; ZGC has 'ZGC Cycles' and 'ZGC Pauses' ('ZGC Minor/Major
        # Cycles/Pauses' when generational); Shenandoah 'Shenandoah Cycles' and
        # 'Shenandoah Pauses'. Solr 9.10's image runs JDK 17, which has none of these
        # concurrent beans under G1, but a Solr on a newer JDK would otherwise count G1's
        # concurrent marking as pause time.
        def _is_pause_collector(key):
            collector = key[len('gc.'):].rsplit('.', 1)[0]
            return 'Concurrent' not in collector and not collector.endswith('Cycles')

        def _gc_total(suffix):
            values = [v for k, v in jvm.items()
                      if k.startswith('gc.') and k.endswith(suffix) and _is_pause_collector(k)
                      and _number(v) is not None]
            return sum(values) if values else None

        metrics['jvm'] = {
            'heap_used_mb': _mb(heap_used),
            'heap_max_mb': _mb(heap_max),
            'heap_used_pct': round(heap_used / heap_max * 100, 1) if heap_used is not None and heap_max is not None else None,
            'cpu_load': _number(jvm.get('os.processCpuLoad')),
            'gc_count': _gc_total('.count'),
            'gc_time_ms': _gc_total('.time'),
        }

        # What the JVM's operating-system bean reports, for sizing the Solr pod's CPU/memory
        # requests. Named 'os' after Solr's own os.* gauges rather than 'host' or 'node':
        # in a container, available_processors and total_physical_mem_mb are the container's
        # limits, not the machine's (system_load_average is machine-wide), and 'node' would
        # read as the Kubernetes node.
        # total_physical_mem sizes the pod: Solr mmaps the index, so RAM beyond the heap
        # becomes OS page cache for the (read-only) index. (Free physical memory is
        # deliberately omitted: it is Linux MemFree, which excludes page cache and so reads
        # near-zero on a healthy node -- more misleading than useful.)
        metrics['os'] = {
            'available_processors': _number(jvm.get('os.availableProcessors')),
            'system_load_average': _number(jvm.get('os.systemLoadAverage')),
            'system_cpu_load': _number(jvm.get('os.systemCpuLoad')),
            'total_physical_mem_mb': _mb(jvm.get('os.totalPhysicalMemorySize')),
        }
        return metrics
    except Exception as e:
        logger.warning("Failed to retrieve Solr metrics for /status", exc_info=True)
        return {'metrics_error': f"Could not retrieve Solr metrics: {type(e).__name__}: {e}"}


@app.get("/status",
         summary="Get status and counts for this NameRes instance.",
         description="<p>This endpoint will return status information for this NameRes instance, with the counts and statistics read from its Solr core grouped under <code>solr</code>.</p>"
                     "<p>You can find out more about this endpoint in the <a href=\"https://github.com/NCATSTranslator/NameResolution/blob/master/documentation/API.md#status\">API documentation</a>.</p>"
         )
async def status_get(
    full: bool = Query(
        False,
        description="Also read Solr's own health metrics -- `queries`, `cache`, `jvm` and `os` under "
                    "`solr` -- at the cost of an extra round-trip to Solr. Leave off for liveness probes.",
    ),
) -> Dict:
    """ Return status and count information from the underyling Solr instance. """
    return await status(full)


async def status(full: bool = False) -> Dict:
    """ Return a dictionary containing status and count information for the underlying Solr instance. """
    query_url = f"http://{config.solr_host}:{config.solr_port}/solr/admin/cores"
    client = solr_client()
    response = await client.get(query_url, params={
        'action': 'STATUS'
    })
    if response.status_code >= 300:
        logger.error("Solr error on accessing /solr/admin/cores?action=STATUS: %s", response.text)
        response.raise_for_status()

    result = response.json()

    # Do we know the Babel version and version URL? It will be stored in an environmental variable if we do.
    babel_version = os.environ.get("BABEL_VERSION", "unknown")
    babel_version_url = os.environ.get("BABEL_VERSION_URL", "")

    # Look up the BIOLINK_MODEL_TAG.
    # Note: this should be a tag from the Biolink Model repo, e.g. "master" or "v4.3.6".
    biolink_model_tag = os.environ.get("BIOLINK_MODEL_TAG", "master")
    biolink_model_url = f"https://github.com/biolink/biolink-model/tree/{biolink_model_tag}"
    biolink_model_download_url = f"https://raw.githubusercontent.com/biolink/biolink-model/{biolink_model_tag}/biolink-model.yaml"

    # Figure out the NameRes version. We report it twice, deliberately: `version` is the
    # OpenAPI info.version verbatim, so that it can be compared against /openapi.json and
    # against NodeNorm's /status, which reports the same key the same way (issue #296);
    # `nameres_version` is the older 'v'-prefixed spelling, kept for existing consumers.
    openapi_version = get_app_info().get('version') or ''
    nameres_version = 'v' + openapi_version if openapi_version else 'master'

    # Computed from NameRes's own record of its lookups (no Solr round-trip), so it stays on
    # the default /status path.
    recent_queries = recent_lookups.summary()

    # We should have a status for our core. Standalone Solr calls it $SOLR_CORE
    # (name_lookup); the older cloud-mode backups called it
    # name_lookup_shard1_replica_n1. A NameRes Solr only ever has one core, so if the
    # expected name isn't there but there is exactly one core, report on that one.
    cores = result.get('status', {})
    core_name = config.solr_core
    core = cores.get(core_name)
    if core is None and len(cores) == 1:
        core_name, core = next(iter(cores.items()))

    # The fields both branches below report. Built once and spread into each, so that the
    # two can't drift: the error branch is what a mis-pointed or still-loading deployment
    # returns, and that is exactly the case in which a caller most needs to know which
    # NameRes it reached.
    common = {
        # Always 'solr' from this implementation. The field exists so that callers can
        # tell us apart from the Elasticsearch-backed NameRes at
        # biothings/NameResolutionAPI, which reports 'elasticsearch' here; until it
        # existed the only difference between the two was a word of prose in `message`
        # (issue #296).
        'backend': 'solr',
        'version': openapi_version or 'unknown',
        'babel_version': babel_version,
        'babel_version_url': babel_version_url,
        'biolink_model': {
            'tag': biolink_model_tag,
            'url': biolink_model_url,
            'download_url': biolink_model_download_url,
        },
        'nameres_version': nameres_version,
        'config': config.public(),
    }

    # Solr's own health metrics are an extra round-trip, so only on ?full=true. Without a
    # core there are no query or cache figures to read, only the JVM's.
    metrics = await fetch_solr_metrics(core_name if core is not None else None, core) if full else {}

    if core is not None:
        index = {}
        if 'index' in core:
            index = core['index']

        return {
            'status': 'ok',
            'message': 'Reporting results from primary core.',
            **common,
            # Timed by NameRes itself rather than read from Solr, so not under 'solr'.
            'recent_queries': recent_queries,
            # Last, in both branches, deliberately: a person reading /status mostly wants
            # the versions, config and recent_queries above, while 'solr' is a long block
            # of diagnostics mainly for tracking and tuning performance. JSON objects are
            # unordered, so nothing may depend on this -- it only decides what a reader
            # sees first. tests/test_status.py pins it.
            #
            # Everything read from Solr goes under 'solr', named after `backend`'s value,
            # so that it can't collide with the service's own keys: until #296 the Solr
            # index version sat at the top level as 'version', which is the key NodeNorm
            # uses for the API version. ?full=true adds Solr's health metrics (queries,
            # cache, jvm, os) alongside the core's stats, or metrics_error if they could not
            # be read.
            'solr': {
                # The core actually reported on, which the single-core fallback above
                # means is not necessarily config.solr_core.
                'core': core_name,
                # .get() rather than [], like every field below it: Solr's core STATUS
                # returns a sparse entry for a core that is still initializing, and
                # /status is what the Kubernetes probes call. A KeyError here would turn
                # a startup window into a 500 instead of a report with blank counts.
                'startTime': core.get('startTime', ''),
                'numDocs': index.get('numDocs', ''),
                'maxDoc': index.get('maxDoc', ''),
                'deletedDocs': index.get('deletedDocs', ''),
                # Solr's index version counter, not a release of anything.
                'version': index.get('version', ''),
                'segmentCount': index.get('segmentCount', ''),
                'lastModified': index.get('lastModified', ''),
                'size': index.get('size', ''),
                **metrics,
            },
        }
    else:
        return {
            'status': 'error',
            'message': 'Expected core not found.',
            **common,
            'recent_queries': recent_queries,
            # Only the JVM's figures, and only with ?full=true: there is no core to report on.
            **({'solr': metrics} if metrics else {}),
        }


# ENDPOINT /reverse_lookup

class DebugOptions(str, Enum):
    # A list of possible Solr debug options from https://solr.apache.org/guide/solr/latest/query-guide/common-query-parameters.html#debug-parameter
    none = "none"
    query = "query"
    timing = "timing"
    results = "results"
    all = "all"

class Request(BaseModel):
    """Reverse-lookup request body."""
    curies: List[str]

class SynonymsRequest(BaseModel):
    """ Synonyms search request body. """
    preferred_curies: List[str]

@app.get(
    "/reverse_lookup",
    summary="Look up synonyms for a CURIE.",
    description="Returns a list of synonyms for a particular CURIE. This endpoint is deprecated; please use /synonyms instead.",
    response_model=Dict[str, Dict],
    tags=["lookup"],
    deprecated=True,
)
async def reverse_lookup_get(
        curies: List[str]= Query(
            examples=["MONDO:0005737", "MONDO:0009757"],
            description="A list of CURIEs to look up synonyms for."
        )
) -> Dict[str, Dict]:
    """Returns a list of synonyms for a particular CURIE."""
    return await name_lookup(curies)


@app.get(
    "/synonyms",
    summary="Look up synonyms for a CURIE.",
    description="<p>Returns a list of synonyms for a particular preferred CURIE. You can normalize a CURIE to a preferred CURIE using NodeNorm.</p>"
                "<p>You can find out more about this endpoint in the <a href=\"https://github.com/NCATSTranslator/NameResolution/blob/master/documentation/API.md#bulk-lookup\">API documentation</a>.</p>"
                "<p>Note that CURIEs are conflated with both GeneProtein and DrugChemical conflation, so that e.g. when searching for a protein, the identifier of the gene that encodes the protein will be returned itself. See <a href=\"https://github.com/NCATSTranslator/NameResolution/blob/master/documentation/API.md#Conflation\">Conflation documentation</a> for more information.</p>",
    response_model=Dict[str, Dict],
    tags=["lookup"],
)
async def synonyms_get(
        preferred_curies: List[str]= Query(
            examples=["MONDO:0005737", "MONDO:0009757"],
            description="A list of CURIEs to look up synonyms for."
        )
) -> Dict[str, Dict]:
    """Returns a list of synonyms for a particular CURIE."""
    return await name_lookup(preferred_curies)


@app.post(
    "/reverse_lookup",
    summary="Look up synonyms for a CURIE.",
    description="Returns a list of synonyms for a particular CURIE. This endpoint is deprecated; please use /synonyms instead.",
    response_model=Dict[str, Dict],
    tags=["lookup"],
    deprecated=True,
)
async def lookup_names_post(
        request: Request = Body(..., examples={
            "curies": ["MONDO:0005737", "MONDO:0009757"],
        }),
) -> Dict[str, Dict]:
    """Returns a list of synonyms for a particular CURIE."""
    return await name_lookup(request.curies)


@app.post(
    "/synonyms",
    summary="Look up synonyms for a CURIE.",
    description="<p>Returns a list of synonyms for a particular preferred CURIE. You can normalize a CURIE to a preferred CURIE using NodeNorm.</p>"
                "<p>You can find out more about this endpoint in the <a href=\"https://github.com/NCATSTranslator/NameResolution/blob/master/documentation/API.md#synonyms\">API documentation</a>.</p>"
                "<p>Note that CURIEs are conflated with both GeneProtein and DrugChemical conflation, so that e.g. a protein that encodes a gene can be looked up with the gene's CURIE, not the protein's CURIE. See <a href=\"https://github.com/NCATSTranslator/NameResolution/blob/master/documentation/API.md#Conflation\">Conflation documentation</a> for more information.</p>",
    response_model=Dict[str, Dict],
    tags=["lookup"],
)
async def synonyms_post(
        request: SynonymsRequest = Body(..., examples={
            "preferred_curies": ["MONDO:0005737", "MONDO:0009757"],
        }),
) -> Dict[str, Dict]:
    """Returns a list of synonyms for a particular CURIE."""
    return await name_lookup(request.preferred_curies)


async def name_lookup(curies) -> Dict[str, Dict]:
    """Returns a list of synonyms for a particular CURIE."""
    time_start = time.perf_counter_ns()
    query = f"http://{config.solr_host}:{config.solr_port}/solr/{config.solr_core}/select"
    curie_filter = " OR ".join(
        f"curie:\"{curie}\""
        for curie in curies
    )
    params = {
        "query": curie_filter,
        "limit": 1000000,
    }
    client = solr_client()
    response = await client.post(query, json=params)
    response.raise_for_status()
    response_json = response.json()
    output = {
        curie: {}
        for curie in curies
    }
    for doc in response_json["response"]["docs"]:
        output[doc["curie"]] = doc
    time_end = time.perf_counter_ns()

    logger.info(f"CURIE Lookup on {len(curies)} CURIEs {json.dumps(curies)}: took {(time_end - time_start)/1_000_000:.2f}ms")

    return output

class ExactMatchMode(str, Enum):
    """Controls exact-match behaviour in lookup queries."""
    label    = "label"     # match against preferred_name_exactish only
    synonyms = "synonyms"  # match against names_exactish only
    any      = "any"       # match against either


class LookupResult(BaseModel):
    curie:str
    label: str
    highlighting: Dict[str, List[str]]
    synonyms: List[str]
    taxa: List[str]
    types: List[str]
    score: float
    clique_identifier_count: int
    explain: Optional[dict]   # Explanation for this specific result
    debug: Optional[dict]     # The debug information for the entire query


@app.get("/lookup",
     summary="Look up cliques for a fragment of a name or synonym.",
     description="<p>Returns cliques with a name or synonym that contains a specified string.</p>"
                 "<p>You can find out more about this endpoint in the <a href=\"https://github.com/NCATSTranslator/NameResolution/blob/master/documentation/API.md#lookup\">API documentation</a>.</p>"
                 "<p>Note that CURIEs are conflated with both GeneProtein and DrugChemical conflation, so that e.g. when searching for a protein, the identifier of the gene that encodes the protein will be returned itself. See <a href=\"https://github.com/NCATSTranslator/NameResolution/blob/master/documentation/API.md#Conflation\">Conflation documentation</a> for more information.</p>",
     response_model=List[LookupResult],
     tags=["lookup"]
)
async def lookup_curies_get(
        string: Annotated[str, Query(
            description="The string to search for. Must be at least the configured minimum length "
                        "(see `minimum_query_length` in `/status`, default 2) after leading/trailing "
                        "whitespace is stripped; shorter queries are rejected with HTTP 422. The "
                        "minimum does not apply when `exact` is set, where any non-empty string is "
                        "searched for."
        )],
        autocomplete: Annotated[bool, Query(
            description="Is the input string incomplete (autocomplete=true) or a complete phrase (autocomplete=false)?"
        )] = False,
        highlighting: Annotated[bool, Query(
            description="Return information on which labels and synonyms matched the search query?"
        )] = False,
        offset: Annotated[int, Query(
            description="The number of results to skip. Can be used to page through the results of a query.",
            # Offset should be greater than or equal to zero.
            ge=0
        )] = 0,
        limit: Annotated[int, Query(
            description="The number of results to return. Can be used to page through the results of a query.",
            # Limit should be greater than or equal to zero and less than or equal to 1000.
            ge=0,
            le=1000
        )] = 10,
        biolink_type: Annotated[Union[List[str], None], Query(
            description="The <a href=\"https://biolink.github.io/biolink-model/\">Biolink Model</a> types to filter to (with or without the `biolink:` prefix), "
                        "e.g. `biolink:Disease` or `Disease`. Multiple types will be combined with OR, i.e. filtering "
                        "for PhenotypicFeature and Disease will return concepts that are either PhenotypicFeatures OR "
                        "Disease, not concepts that are both PhenotypicFeature AND Disease.",
            # We can't use `example` here because otherwise it gets filled in when you click "Try it out",
            # which is easy to overlook.
            # examples=["biolink:Disease", "biolink:PhenotypicFeature"]
        )] = [],
        only_prefixes: Annotated[Union[str, None], Query(
            description="Pipe-separated, case-sensitive list of prefixes to filter to, e.g. `MONDO|EFO`.",
            # We can't use `example` here because otherwise it gets filled in when filling this in.
            # examples="MONDO|EFO"
        )] = None,
        exclude_prefixes: Annotated[Union[str, None], Query(
            description="Pipe-separated, case-sensitive list of prefixes to exclude, e.g. `UMLS|EFO`.",
            # We can't use `example` here because otherwise it gets filled in when filling this in.
            # examples="UMLS|EFO"
        )] = None,
        only_taxa: Annotated[Union[str, None], Query(
            description="Pipe-separated, case-sensitive list of taxa to filter, "
                        "e.g. `NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955`.",
            # We can't use `example` here because otherwise it gets filled in when filling this in.
            # examples="NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955"
        )] = None,
        debug: Annotated[Union[DebugOptions, None], Query(
            description="Provide debugging information on the Solr query as described in <a href=\"https://solr.apache.org/guide/solr/latest/query-guide/common-query-parameters.html#debug-parameter\">Solr's debug parameters</a>."
        )] = 'none',
        exact: Annotated[Optional[ExactMatchMode], Query(
            description="Exact-match mode: 'label' matches the preferred name only, "
                        "'synonyms' matches any synonym, 'any' matches either. In every mode the "
                        "entire string must match, case-insensitively. "
                        "Omit for the default tokenized search."
        )] = None,
) -> List[LookupResult]:
    """
    Returns cliques with a name or synonym that contains a specified string.
    """
    return await lookup(string, autocomplete, highlighting, offset, limit, biolink_type, only_prefixes, exclude_prefixes, only_taxa, debug, exact, raise_on_too_short=True)


@app.post("/lookup",
    summary="Look up cliques for a fragment of a name or synonym.",
    description="<p>Returns cliques with a name or synonym that contains a specified string.</p>"
                "<p>You can find out more about this endpoint in the <a href=\"https://github.com/NCATSTranslator/NameResolution/blob/master/documentation/API.md#lookup\">API documentation</a>.</p>"
                "<p>Note that CURIEs are conflated with both GeneProtein and DrugChemical conflation, so that e.g. when searching for a protein, the identifier of the gene that encodes the protein will be returned itself. See <a href=\"https://github.com/NCATSTranslator/NameResolution/blob/master/documentation/API.md#Conflation\">Conflation documentation</a> for more information.</p>",
    response_model=List[LookupResult],
    tags=["lookup"]
)
async def lookup_curies_post(
        string: Annotated[str, Query(
            description="The string to search for. Must be at least the configured minimum length "
                        "(see `minimum_query_length` in `/status`, default 2) after leading/trailing "
                        "whitespace is stripped; shorter queries are rejected with HTTP 422. The "
                        "minimum does not apply when `exact` is set, where any non-empty string is "
                        "searched for."
        )],
        autocomplete: Annotated[bool, Query(
            description="Is the input string incomplete (autocomplete=true) or a complete phrase (autocomplete=false)?"
        )] = False,
        highlighting: Annotated[bool, Query(
            description="Return information on which labels and synonyms matched the search query?"
        )] = False,
        offset: Annotated[int, Query(
            description="The number of results to skip. Can be used to page through the results of a query.",
            # Offset should be greater than or equal to zero.
            ge=0
        )] = 0,
        limit: Annotated[int, Query(
            description="The number of results to return. Can be used to page through the results of a query.",
            # Limit should be greater than or equal to zero and less than or equal to 1000.
            ge=0,
            le=1000
        )] = 10,
        biolink_type: Annotated[Union[List[str], None], Query(
            description="The <a href=\"https://biolink.github.io/biolink-model/\">Biolink Model</a> types to filter to (with or without the `biolink:` prefix), "
                        "e.g. `biolink:Disease` or `Disease`. Multiple types will be combined with OR, i.e. filtering "
                        "for PhenotypicFeature and Disease will return concepts that are either PhenotypicFeatures OR "
                        "Disease, not concepts that are both PhenotypicFeature AND Disease.",
            # We can't use `example` here because otherwise it gets filled in when you click "Try it out",
            # which is easy to overlook.
            # examples=["biolink:Disease", "biolink:PhenotypicFeature"]
        )] = [],
        only_prefixes: Annotated[Union[str, None], Query(
            description="Pipe-separated, case-sensitive list of prefixes to filter to, e.g. `MONDO|EFO`.",
            # We can't use `example` here because otherwise it gets filled in when filling this in.
            # examples="MONDO|EFO"
        )] = None,
        exclude_prefixes: Annotated[Union[str, None], Query(
            description="Pipe-separated, case-sensitive list of prefixes to exclude, e.g. `UMLS|EFO`.",
            # We can't use `example` here because otherwise it gets filled in when filling this in.
            # examples="UMLS|EFO"
        )] = None,
        only_taxa: Annotated[Union[str, None], Query(
            description="Pipe-separated, case-sensitive list of taxa to filter, "
                        "e.g. `NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955`.",
            # We can't use `example` here because otherwise it gets filled in when filling this in.
            # examples="NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955"
        )] = None,
        debug: Annotated[Union[DebugOptions, None], Query(
            description="Provide debugging information on the Solr query as per <a href=\"https://solr.apache.org/guide/solr/latest/query-guide/common-query-parameters.html#debug-parameter\">Solr's debug parameter</a>."
        )] = 'none',
        exact: Annotated[Optional[ExactMatchMode], Query(
            description="Exact-match mode: 'label' matches the preferred name only, "
                        "'synonyms' matches any synonym, 'any' matches either. In every mode the "
                        "entire string must match, case-insensitively. "
                        "Omit for the default tokenized search."
        )] = None,
) -> List[LookupResult]:
    """
    Returns cliques with a name or synonym that contains a specified string.
    """
    return await lookup(string, autocomplete, highlighting, offset, limit, biolink_type, only_prefixes, exclude_prefixes, only_taxa, debug, exact, raise_on_too_short=True)


async def lookup(string: str,
           autocomplete: bool = False,
           highlighting: bool = False,
           offset: int = 0,
           limit: Annotated[int, Field(strict=True, gt=0, le=1000)] = 10,
           biolink_types: List[str] = None,
           only_prefixes: str = "",
           exclude_prefixes: str = "",
           only_taxa: str = "",
           debug: DebugOptions = 'none',
           exact: Optional[ExactMatchMode] = None,
           raise_on_too_short: bool = False,
           bulk: bool = False,
) -> List[LookupResult]:
    """
    Returns cliques with a name or synonym that contains a specified string.

    :param bulk: This lookup is one string of a /bulk-lookup. It is then left out of
        recent_queries, which describes /lookup: a single bulk request can hold thousands of
        strings, enough to swamp the window, and its strings compete with each other for Solr
        while a /lookup caller's doesn't. For the same reason a slow string is logged at INFO
        rather than as a SLOW QUERY warning, so that one bulk request against a struggling
        Solr can't bury the log in them. Its failures are logged at INFO too, and
        bulk_lookup() logs one FAILED BULK QUERY warning for the request: a bulk request
        against an unreachable Solr would otherwise log one warning per string.

    :param autocomplete: Should we do the lookup in autocomplete mode (in which we expect the final word to be
        incomplete) or not (in which the entire phrase is expected to be complete, i.e. as an entity linker)?
    :param highlighting: Return information on which labels and synonyms matched the search query.
    :param biolink_types: A list of Biolink types to filter (with or without the `biolink:` prefix). Note that these are
        additive, i.e. if this list is ['PhenotypicFeature', 'Disease'], then both phenotypic features AND diseases
        will be returned, rather than filtering to concepts that are both PhenotypicFeature and Disease.
    """

    # Durations come from perf_counter_ns(), a monotonic clock, not time.time_ns(): the wall
    # clock can be stepped (NTP, a VM resuming) mid-lookup, and a negative or inflated
    # duration would then sit in recent_queries' min, max and mean until it left the window.
    time_start = time.perf_counter_ns()

    # autocomplete asks us to treat the last word as a prefix; exact asks us to match the whole
    # string and nothing else. There is no sensible reading of the two together, so rather than
    # silently ignoring one of them, say so.
    if exact and autocomplete:
        raise HTTPException(
            status_code=400,
            detail="autocomplete=true cannot be combined with exact matching: autocomplete treats "
                   "the final word as an incomplete prefix, while exact requires the entire string "
                   "to match. Please use one or the other.",
        )

    # First, we strip and lowercase the query since all our indexes are case-insensitive.
    string_lc = string.strip().lower()

    # There is a possibility that the input text isn't in UTF-8.
    # We could try a bunch of Python packages to try to determine what the encoding actually is:
    #   - https://pypi.org/project/charset-normalizer/
    #   - https://www.crummy.com/software/BeautifulSoup/bs4/doc/#unicode-dammit
    # But the only issue we've actually run into so far has been the Windows smart
    # quote (https://github.com/NCATSTranslator/NameResolution/issues/176), so for now
    # let's detect and replace just those characters.
    #
    # Deliberately not done in exact mode. The *_exactish fields are a KeywordTokenizer and a
    # LowerCaseFilter, with no punctuation folding of their own, so the indexed value keeps
    # whichever quote characters Babel emitted. Rewriting the query's quotes would therefore make
    # exact mode search for a string the caller did not type, and would put any label containing a
    # typographic quote permanently out of reach. The default search is unaffected either way,
    # because StandardTokenizer discards the punctuation at index and query time alike.
    if not exact:
        string_lc = re.sub(r"[“”]", '"', re.sub(r"[‘’]", "'", string_lc))

    # Is the query long enough to be worth searching?
    #
    # config.minimum_query_length exists because short queries are slow in the default tokenized
    # search -- a one-character query matches a prefix of half the index -- and never return
    # anything useful. Exact mode has neither problem: it is a single filter query against an
    # untokenized field, and single-character labels are real (the gene T, the element symbols),
    # so the minimum would put them permanently out of reach for no gain. It is therefore held to
    # nothing but non-emptiness.
    #
    # The floor of 1 is not redundant with the setting. An empty query is rejected whatever
    # minimum_query_length is set to, because it would otherwise reach Solr as `"" OR ()` and come
    # back as a parse error -- an HTTP 500 for what is really an empty search box.
    minimum_length = 1 if exact else max(1, config.minimum_query_length)
    if len(string_lc) < minimum_length:
        if raise_on_too_short:
            # A RequestValidationError rather than an HTTPException, so that a query rejected for
            # its length is reported in the same shape -- and documented by the same schema -- as
            # one rejected by FastAPI's own parameter validation. A caller iterating `detail` as a
            # list of errors should not have to special-case this one rejection.
            raise RequestValidationError([{
                "type": "string_too_short",
                "loc": ("query", "string"),
                "msg": f"String should have at least {minimum_length} character(s) after "
                       f"leading and trailing whitespace is stripped",
                "input": string,
                "ctx": {"min_length": minimum_length},
            }])
        return []

    # For reasons I don't understand, we need to use backslash to escape characters (e.g. "\(") to remove the special
    # significance of characters inside round brackets, but not inside double-quotes. So we escape them separately:
    # - For a full exact search, we only remove double-quotes and slashes, leaving other special characters as-is.
    string_lc_escape_groupings = string_lc.replace('"', '').replace('\\', '')

    # - For a tokenized search, we escape all special characters with backslashes as well as other characters that might
    #   mess up the search.
    string_lc_escape_everything = re.sub(r'([!(){}\[\]^"~*?:/+-\\])', r'\\\g<0>', string_lc) \
        .replace('&&', ' ').replace('||', ' ')

    # If autocomplete mode is turned on, add an asterisk at the end so that we look for incomplete terms.
    if autocomplete:
        query = f'"{string_lc_escape_groupings}" OR ({string_lc_escape_everything}*)'
    else:
        query = f'"{string_lc_escape_groupings}" OR ({string_lc_escape_everything})'

    # Apply filters as needed.
    filters = []

    # Biolink type filter
    if biolink_types:
        biolink_types_filters = []
        for biolink_type in biolink_types:
            biolink_type_stripped = biolink_type.strip()
            if biolink_type_stripped:
                if biolink_type_stripped.startswith('biolink:'):
                    biolink_type_stripped = biolink_type_stripped[8:]
                biolink_types_filters.append(f"types:{biolink_type_stripped}")
        filters.append(" OR ".join(biolink_types_filters))

    # Prefix: only filter
    if only_prefixes:
        prefix_filters = []
        for prefix in re.split('\\s*\\|\\s*', only_prefixes):
            prefix_filters.append(f"curie:/{prefix}:.*/")
        filters.append(" OR ".join(prefix_filters))

    # Prefix: exclude filter
    if exclude_prefixes:
        prefix_exclude_filters = []
        for prefix in re.split('\\s*\\|\\s*', exclude_prefixes):
            prefix_exclude_filters.append(f"NOT curie:/{prefix}:.*/")
        filters.append(" AND ".join(prefix_exclude_filters))

    # Taxa filter.
    # only_taxa is like: 'NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955'
    if only_taxa:
        taxon_ids = re.split('\\s*\\|\\s*', only_taxa)
        if taxon_ids:
            taxa_filters = []

            for taxon in taxon_ids:
                taxa_filters.append(f'taxa:"{taxon}"')

            # We also need to include entries that don't have taxa specified.
            taxa_filters.append('taxon_specific:false')

            # Combine all taxa filters.
            filters.append('(' + " OR ".join(taxa_filters) + ')')

    # Turn on highlighting if requested.
    inner_params = {}
    # In exact mode there is no scored query for Solr to highlight against (see below), so the
    # highlighting is synthesized from the returned documents instead of asked for here.
    if highlighting and not exact:
        inner_params.update({
            # Highlighting
            "hl": "true",
            "hl.method": "unified",
            "hl.encoder": "html",
            "hl.tag.pre": "<strong>",
            "hl.tag.post": "</strong>",
            # "hl.usePhraseHighlighter": "true",
            # "hl.highlightMultiTerm": "true",
        })

    if debug and debug != 'none':
        inner_params['debug'] = debug

        # Rather than returning the explain as a string, return it as structured JSON.
        inner_params['debug.explain.structured'] = 'true'

    if exact:
        # Exact mode: bypass eDisMax entirely and match with a filter query against the *_exactish
        # fields. Unlike the default query below, which matches the string's tokens in any order,
        # this requires the whole string to match (case-insensitively -- see the exactish fieldType
        # in the schema). A filter query is the right shape for that: there is nothing to score.
        string_lc_escaped = string_lc.replace('\\', '\\\\').replace('"', '\\"')
        if exact == ExactMatchMode.label:
            exact_clause = f'preferred_name_exactish:"{string_lc_escaped}"'
        elif exact == ExactMatchMode.synonyms:
            exact_clause = f'names_exactish:"{string_lc_escaped}"'
        else:  # ExactMatchMode.any
            exact_clause = (
                f'(preferred_name_exactish:"{string_lc_escaped}" OR names_exactish:"{string_lc_escaped}")'
            )

        # Marked uncached deliberately. Solr's filterCache is an entry count, not a size in bytes
        # (solrconfig.xml sets 512 entries), and the entries it holds are shared, reusable filters
        # like types: and taxa: that nearly every search benefits from. This clause is the opposite
        # of that: one distinct entry per distinct search string. The workload exact mode exists to
        # serve -- an NER pipeline resolving large numbers of *different* strings -- would therefore
        # evict the whole cache on every request and slow down the ordinary search path as
        # collateral damage, in exchange for a hit rate near zero on its own entries. Repeated
        # identical exact lookups are still served from the queryResultCache, which is bounded by
        # RAM rather than by entry count and caches the whole (query, filters, sort) result.
        filters.append(f'{{!cache=false}}{exact_clause}')
        params = {
            "query": "*:*",
            "filter": filters,
            "sort": "clique_identifier_count DESC, curie_suffix ASC",
            "limit": limit,
            "offset": offset,
            "fields": "*, score",
            "params": inner_params,
        }
    else:
        params = {
            "query": {
                "edismax": {
                    "query": query,
                    # qf = query fields, i.e. how should we boost these fields if they contain the same fields as the input.
                    # https://solr.apache.org/guide/solr/latest/query-guide/dismax-query-parser.html#qf-query-fields-parameter
                    "qf": "preferred_name_exactish^250 names_exactish^100 preferred_name^25 names^10",
                    # pf = phrase fields, i.e. how should we boost these fields if they contain the entire search phrase.
                    # https://solr.apache.org/guide/solr/latest/query-guide/dismax-query-parser.html#pf-phrase-fields-parameter
                    "pf": "preferred_name_exactish^300 names_exactish^200 preferred_name^30 names^20",
                    # Boosts
                    "bq": [],
                    "boost": [
                        # The boost is multiplied with score -- calculating the log() reduces how quickly this increases
                        # the score for increasing clique identifier counts.
                        "log(sum(clique_identifier_count, 1))"
                    ],
                },
            },
            "sort": "score DESC, clique_identifier_count DESC, curie_suffix ASC",
            "limit": limit,
            "offset": offset,
            "filter": filters,
            "fields": "*, score",
            "params": inner_params,
        }
    logger.debug(f"Query: {json.dumps(params, indent=2)}")

    log_msg_prefix = (f"Lookup query to Solr for {json.dumps(string)} " +
                      f"(autocomplete={autocomplete}, highlighting={highlighting}, offset={offset}, limit={limit}, biolink_types={biolink_types}, only_prefixes={only_prefixes}, exclude_prefixes={exclude_prefixes}, only_taxa={only_taxa}, exact={exact})")

    time_solr_start = time.perf_counter_ns()
    time_solr_end = None    # Set once Solr has answered; see the except below.
    query_url = f"http://{config.solr_host}:{config.solr_port}/solr/{config.solr_core}/select"
    try:
        client = solr_client()
        response = await client.post(query_url, json=params)
        if response.status_code >= 300:
            logger.error("Solr REST error: %s", response.text)
            response.raise_for_status()
        response = response.json()
        time_solr_end = time.perf_counter_ns()

        # Do we have any debug.explain information?
        explain_info = {}
        if 'debug' in response and 'explain' in response['debug']:
            explain_info = response['debug']['explain']

        logger.debug(f"Solr response: {json.dumps(response, indent=2)}")

        # Associate highlighting information with search results.
        highlighting_response = response.get("highlighting", {})

        outputs = []
        for doc in response['response']['docs']:
            preferred_matches = []
            synonym_matches = []

            if exact and highlighting:
                # Solr did not highlight anything for us: exact mode matches with a filter query
                # against fields that are not stored, so there is no scored query and nothing for the
                # highlighter to mark up. Synthesize the same shape from the documents instead, so that
                # a caller reading `highlighting` does not have to care which mode produced it.
                #
                # Every match in exact mode is a whole-value match, so the "highlighted" form of a
                # matching name is simply the entire name wrapped in the same tags Solr would have
                # used. Escape it first, since we ask Solr for hl.encoder=html in the default path.
                def mark(value: str) -> str:
                    return f"<strong>{html.escape(value)}</strong>"

                if exact in {ExactMatchMode.label, ExactMatchMode.any}:
                    preferred_name = doc.get("preferred_name", "")
                    if preferred_name.lower() == string_lc:
                        preferred_matches.append(mark(preferred_name))

                if exact in {ExactMatchMode.synonyms, ExactMatchMode.any}:
                    synonym_matches.extend(
                        mark(name) for name in doc.get("names", []) if name.lower() == string_lc
                    )

            elif doc['id'] in highlighting_response:
                matches = highlighting_response[doc['id']]

                # We order exactish matches before token matches.
                if 'preferred_name_exactish' in matches:
                    preferred_matches.extend(matches['preferred_name_exactish'])
                if 'preferred_name' in matches:
                    preferred_matches.extend(matches['preferred_name'])

                # Solr sometimes returns duplicates or a blank string here?
                preferred_matches = list(filter(lambda s: s, set(preferred_matches)))

                # We order exactish matches before token matches.
                if 'names_exactish' in matches:
                    synonym_matches.extend(matches['names_exactish'])
                if 'names' in matches:
                    synonym_matches.extend(matches['names'])

                # Solr sometimes returns duplicates or a blank string here?
                synonym_matches = list(filter(lambda s: s, set(synonym_matches)))

            # Prepare debugging and explain information for this request.
            debug_for_this_request = response.get('debug', None)
            explain_for_this_doc = None
            if debug in {DebugOptions.results, DebugOptions.all}:
                if doc['id'] in explain_info:
                    explain_for_this_doc = explain_info[doc['id']]

                    # If we have explain information, we don't need to also include it in the debugging information.
                    debug_for_this_request['explain'] = {"_comment": "Removed to avoid data duplication"}


            outputs.append(LookupResult(curie=doc.get("curie", ""),
                               label=doc.get("preferred_name", ""),
                               highlighting={
                                   'labels': preferred_matches,
                                   'synonyms': synonym_matches,
                               } if highlighting else {},
                               synonyms=doc.get("names", []),
                               score=doc.get("score", ""),
                               taxa=doc.get("taxa", []),
                               clique_identifier_count=doc.get("clique_identifier_count", 0),
                               types=[f"biolink:{d}" for d in doc.get("types", [])],
                               explain=explain_for_this_doc,
                               debug=debug_for_this_request))
    except BaseException as e:
        # A failed lookup still has to be timed. The ones that hit SOLR_TIMEOUT_SECONDS are the
        # slowest requests of all, so recording times only on success would leave them out of
        # recent_queries -- and out of the log -- and make /status look healthiest exactly when
        # Solr is struggling. BaseException rather than Exception so that a lookup cancelled
        # while it waits on Solr (asyncio.CancelledError) is timed too: those are slow ones
        # as well. It is re-raised below, so nothing is swallowed.
        #
        # The try covers building the results as well as the Solr request, so that a lookup
        # that fails on a document it can't turn into a LookupResult is counted and logged
        # too: its caller gets a 500 all the same, and /status mustn't report no failures
        # while /lookup is returning them. Such a failure's Solr round trip ended when Solr
        # answered, not when the lookup failed.
        time_failed = time.perf_counter_ns()
        time_taken_ms = (time_failed - time_start)/1_000_000
        if isinstance(e, httpx.TimeoutException):
            outcome = 'timeout'
        elif isinstance(e, asyncio.CancelledError):
            outcome = 'cancelled'
        else:
            outcome = 'error'
        if not bulk:
            solr_end = time_solr_end if time_solr_end is not None else time_failed
            recent_lookups.record(time_failed - time_start, solr_end - time_solr_start, outcome)
        # A bulk string's failure is at INFO: bulk_lookup() warns once for the whole request.
        (logger.info if bulk else logger.warning)(
            f"FAILED QUERY ({outcome}: {type(e).__name__}): {log_msg_prefix}: "
            f"failed after {time_taken_ms:.2f}ms")
        raise

    time_end = time.perf_counter_ns()
    time_taken_ms = (time_end - time_start)/1_000_000
    time_taken_ms_solr = (time_solr_end - time_solr_start)/1_000_000
    if not bulk:
        recent_lookups.record(time_end - time_start, time_solr_end - time_solr_start)
    # Parsed downstream (TranslatorSRI/babel-validation's NameRes log analysis, added in its
    # PR #107: LOOKUP_LINE_RE in log-analysis/nameres/analyze_nameres_logs.py), so change the
    # wording only together with that parser, which must keep accepting the old text:
    # v1.7.1 and earlier logged "(with Yms waiting for Solr)", and the parser reads both.
    # It ignores the log level. tests/test_status.py pins the format.
    log_msg = (f"{log_msg_prefix}: "
               f"took {time_taken_ms:.2f}ms (with {time_taken_ms_solr:.2f}ms for the Solr round trip)")
    if time_taken_ms > SLOW_QUERY_THRESHOLD_MS and not bulk:
        logger.warning("SLOW QUERY: " + log_msg)
    else:
        logger.info(log_msg)

    return outputs

## BULK ENDPOINT

class NameResQuery(BaseModel):
    """
    A request for name resolution.
    """
    strings: List[str] = Field(
        ..., # Ellipsis means field is required
        description="The strings to search for. The returned results will be in a dictionary with these values as keys."
    )
    autocomplete: Optional[bool] = Field(
        False,
        description="Is the input string incomplete (autocomplete=true) or a complete phrase (autocomplete=false)?"
    )
    highlighting: Optional[bool] = Field(
        False,
        description="Return information on which labels and synonyms matched the search query?"
    )
    offset: Optional[int] = Field(
        0,
        description="The number of results to skip. Can be used to page through the results of a query.",
        # Offset should be greater than or equal to zero.
        ge=0
    )
    limit: Optional[int] = Field(
        10,
        description="The number of results to return. Can be used to page through the results of a query.",
        # Limit should be greater than or equal to zero and less than or equal to 1000.
        ge=0,
        le=1000
    )
    biolink_types: Optional[List[str]] = Field(
        [],
        description="The <a href=\"https://biolink.github.io/biolink-model/\">Biolink Model</a> types to filter to (with or without the `biolink:` prefix), "
                    "e.g. `biolink:Disease` or `Disease`. Multiple types will be combined with OR, i.e. filtering "
                    "for PhenotypicFeature and Disease will return concepts that are either PhenotypicFeatures OR "
                    "Disease, not concepts that are both PhenotypicFeature AND Disease.",
    )
    only_prefixes: Optional[str] = Field(
        "",
        description="Pipe-separated, case-sensitive list of prefixes to filter to, e.g. `MONDO|EFO`.",
        # We can't use `example` here because otherwise it gets filled in when filling this in.
        # examples="MONDO|EFO"
    )
    exclude_prefixes: Optional[str] = Field(
        "",
        description="Pipe-separated, case-sensitive list of prefixes to exclude, e.g. `UMLS|EFO`.",
        # We can't use `example` here because otherwise it gets filled in when filling this in.
        # examples="UMLS|EFO"
    )
    only_taxa: Optional[str] = Query(
        "",
        description="Pipe-separated, case-sensitive list of taxa to filter, "
                    "e.g. `NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955`.",
        # We can't use `example` here because otherwise it gets filled in when filling this in.
        # examples="NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955"
    )
    debug: Optional[DebugOptions] = Field(
        'none',
        description="Provide debugging information on the Solr query as per <a href=\"https://solr.apache.org/guide/solr/latest/query-guide/common-query-parameters.html#debug-parameter\">Solr's debug parameter</a>."
    )
    exact: Optional[ExactMatchMode] = Field(
        None,
        description="Exact-match mode: 'label' matches the preferred name only, "
                    "'synonyms' matches any synonym, 'any' matches either. In every mode the "
                    "entire string must match, case-insensitively. "
                    "Omit (or null) for the default tokenized search.",
    )


@app.post("/bulk-lookup",
          summary="Look up cliques for a fragment of multiple names or synonyms.",
          description="<p>Returns cliques for each query.</p>"
                      "<p>You can find out more about this endpoint in the <a href=\"https://github.com/NCATSTranslator/NameResolution/blob/master/documentation/API.md#bulk-lookup\">API documentation</a>.</p>"
                      "<p>Note that CURIEs are conflated with both GeneProtein and DrugChemical conflation, so that e.g. a protein that encodes a gene can be looked up with the gene's CURIE, not the protein's CURIE. See <a href=\"https://github.com/NCATSTranslator/NameResolution/blob/master/documentation/API.md#Conflation\">Conflation documentation</a> for more information.</p>",
          response_model=Dict[str, List[LookupResult]],
          tags=["lookup"]
)
async def bulk_lookup(query: NameResQuery) -> Dict[str, List[LookupResult]]:
    time_start = time.perf_counter_ns()

    # Bounded so that a single large request can't open a socket per string; see
    # Config.solr_max_concurrent_lookups.
    semaphore = asyncio.Semaphore(config.solr_max_concurrent_lookups)

    async def do_lookup(string: str):
        async with semaphore:
            results = await lookup(
                string,
                query.autocomplete,
                query.highlighting,
                query.offset,
                query.limit,
                query.biolink_types,
                query.only_prefixes,
                query.exclude_prefixes,
                query.only_taxa,
                query.debug,
                query.exact,
                bulk=True,
            )
        return string, results

    tasks = [asyncio.create_task(do_lookup(s)) for s in query.strings]
    try:
        pairs = await asyncio.gather(*tasks)
    except BaseException as e:
        # One string has failed, so the request has (it becomes a 500). gather() doesn't
        # cancel the others when one fails, so without this every remaining string would
        # still be sent to a Solr that is probably down or overloaded, each failing in turn.
        # Cancel them, and wait for them so that none outlives the request.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        failed = sum(1 for task in tasks if not task.cancelled() and task.exception() is not None)
        time_failed = time.perf_counter_ns()
        # The one warning for the request: its strings' own failures are logged at INFO
        # (see lookup()'s `bulk`), so that a bulk request against an unreachable Solr logs
        # one warning rather than one per string.
        logger.warning(f"FAILED BULK QUERY ({type(e).__name__}): Bulk lookup query for "
                       f"{len(query.strings)} strings ({query}): {failed} failed, and those not yet "
                       f"done were cancelled, after {(time_failed - time_start)/1_000_000:.2f}ms")
        raise
    result = dict(pairs)

    time_end = time.perf_counter_ns()
    logger.info(f"Bulk lookup query for {len(query.strings)} strings ({query}): took {(time_end - time_start)/1_000_000:.2f}ms")
    return result


# Override the OpenAPI schema with the one we build from api/resources/openapi.yml.
#
# This has to replace the openapi() method rather than assign to app.openapi_schema:
# since FastAPI 0.137.0, openapi() rebuilds the schema whenever the app's recorded
# routes version doesn't match the router's current one, and a schema we assigned
# ourselves never carries that stamp -- so the first request to /openapi.json silently
# overwrote it with FastAPI's default document (issue #294).
def custom_openapi():
    """Build the custom OpenAPI schema once, then serve it from the cache."""
    if not app.openapi_schema:
        app.openapi_schema = construct_open_api_schema(app)
    return app.openapi_schema


app.openapi = custom_openapi

# Set up opentelemetry if enabled.
if os.environ.get('OTEL_ENABLED', 'false') == 'true':
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    # from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    # httpx connections need to be open a little longer by the otel decorators
    # but some libs display warnings of resource being unclosed.
    # these supresses such warnings.
    logging.captureWarnings(capture=True)
    warnings.filterwarnings("ignore", category=ResourceWarning)
    otel_service_name = os.environ.get('SERVER_NAME', 'infores:sri-name-resolver')
    assert otel_service_name and isinstance(otel_service_name, str)

    otlp_host = os.environ.get("JAEGER_HOST", "http://localhost/").rstrip('/')
    otlp_port = os.environ.get("JAEGER_PORT", "4317")
    otlp_endpoint = f'{otlp_host}:{otlp_port}'
    otlp_exporter = OTLPSpanExporter(endpoint=f'{otlp_endpoint}')
    processor = BatchSpanProcessor(otlp_exporter)
    # processor = BatchSpanProcessor(ConsoleSpanExporter())
    resource = Resource.create(attributes={
        SERVICE_NAME: os.environ.get("JAEGER_SERVICE_NAME", otel_service_name),
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls=
                                       "docs,openapi.json")
    HTTPXClientInstrumentor().instrument()
