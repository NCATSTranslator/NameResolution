# NameRes Performance Diagnostics

This document explains how to diagnose why Solr may be slow or under strain, using the
observability built into NameRes. It covers what the metrics in `/status` mean, how to read
the log messages, and a decision tree for identifying CPU pressure, memory pressure, high
load, and other causes.

All the Solr-side metrics below live under `solr` -- `solr.queries`, `solr.cache`, `solr.jvm`
and `solr.os` -- and are only fetched when you call **`/status?full=true`** (it adds a
round-trip to Solr, so the default `/status` stays cheap for Kubernetes liveness probes). If
`solr.metrics_error` appears instead, the fetch from Solr's `/admin/metrics` failed, and the
NameRes log has the details. See
[API.md](API.md#status) for the full response.

---

## 1. `/status` response fields

### Frontend query metrics (`recent_queries`)

Tracked by the Python process over the last `RECENT_TIMES_COUNT` `/lookup` queries. Always
present (no `?full=true` needed). `/bulk-lookup` is left out: one bulk request can hold thousands
of strings, enough to swamp the window, and its strings compete with each other for Solr in a way
a `/lookup` caller's doesn't (#309 is about tracking it separately).

Three latencies nest inside one another, and the fields are named for which one they time:

| Span | Where | What it covers |
|---|---|---|
| Lookup | `recent_queries.lookup_ms` | All of NameRes's `lookup()`: the Solr round trip below, plus building the results. Not FastAPI's handling of the request, nor the network to the caller. |
| Solr round trip | `recent_queries.solr_round_trip_ms` | The Solr request as NameRes sees it: setting up the HTTP client and connection, Solr's handling, transferring the response and decoding its JSON. |
| Solr handler | `solr.queries.handler_ms` (with `?full=true`) | Only the time inside Solr's `/select` handler, as Solr's own timer measures it. |

So `lookup_ms` − `solr_round_trip_ms` is NameRes's result processing, and `solr_round_trip_ms` −
`handler_ms` is connection, transfer and JSON overhead. NameRes shares one HTTP client with kept-
alive connections across its Solr requests, so that second gap should be small; a large one points
at the network between the API and Solr, or at large responses. (When `lookup()` opened a new
client and connection per query, that overhead took a 1 ms query on a local Solr to about 8 ms.)

| Field | What it means |
|---|---|
| `lookup_ms.mean` | Mean lookup time. Rising mean = sustained slowdown. |
| `lookup_ms.min` / `max` | The fastest and slowest lookups in the window. `max` is the single worst one — often a timeout (about `SOLR_TIMEOUT_SECONDS`), which then stays there until it ages out of the window; the SLOW QUERY and FAILED QUERY log lines say which query it was. `min` is usually a cache hit. |
| `lookup_ms.p50` / `p95` / `p99` | Lookup-time percentiles over the window. Comparing them against `solr.queries.handler_ms` helps localize a latency tail, but it's a pointer, not proof: the two cover different traffic and periods, and these include failures (see Step 5 of the decision tree). |
| `solr_round_trip_ms` | The same six values for the part of each lookup spent on the Solr request. Matches the "for the Solr round trip" figure in the log line. A `p99` or `max` well above `solr.queries.handler_ms`' points at a tail in the connection, network or transfer rather than in Solr. |
| `requests` / `window_size` | How many queries are currently in the rolling window, and its capacity (`RECENT_TIMES_COUNT`, default 50000). Unlike `solr.queries.requests`, `requests` is windowed, not cumulative, so the two can't be compared. |
| `errors` / `timeouts` / `cancelled` | Queries in the window that failed: `timeouts` hit `SOLR_TIMEOUT_SECONDS`, `cancelled` were abandoned while waiting on Solr (a server shutdown, say), and `errors` are the rest (Solr returned an error or couldn't be reached). They are disjoint, so their sum is every failure. Unlike `solr.queries.errors`/`timeouts` these are windowed, not cumulative, and they include failures Solr never saw (it was unreachable, or NameRes gave up waiting). Failed queries are counted in the timings above, so a run of timeouts shows up in `lookup_ms.p99` and `max` too. |

The means are running totals and always current. `min`, `max` and the percentiles need a sort of
the whole window, so `/status` reuses them for up to 10 seconds; the percentiles need at least
two lookups in the window.

### Solr queries (`solr.queries`)

Solr's own figures for every `/select` the core has served -- `/synonyms` and `/reverse_lookup`
as well as `/lookup`, where `recent_queries` times `/lookup` alone. `requests`, `errors` and
`timeouts` count from Solr's start. `handler_ms` comes from Solr's request timer, which by default
samples with an exponentially decaying reservoir, so it describes roughly the last few minutes
rather than Solr's lifetime.

| Field | What it means |
|---|---|
| `requests` | Total `/select` requests handled since Solr started. Sample it twice to get a rate. |
| `errors` / `timeouts` | Cumulative counts. `timeouts` counts only requests Solr itself cut short and answered with partial results (as `timeAllowed` would; NameRes does not set it), so it normally stays at 0. Requests NameRes gave up waiting for are `recent_queries.timeouts`. |
| `handler_ms.mean` | Mean time inside Solr's `/select` handler, over the recent sample. |
| `handler_ms.min` / `max` | The fastest and slowest requests still in Solr's sample. The sample weights recent requests more heavily, so an older slow request can remain as `max` while barely moving the percentiles. |
| `handler_ms.p50` / `p95` / `p99` | Solr-side latency percentiles. `p99` spiking while `p50` stays stable = occasional GC pauses or one-off expensive queries; all of them rising = sustained overload. Compare against `recent_queries.lookup_ms` to tell Solr-side tails from NameRes-side ones. |

### Index health (`solr`)

| Field | What it means |
|---|---|
| `segmentCount` | Number of Lucene segments. Above ~20 means Solr does more per-segment work per query. The index is read-only after loading, so an optimize is safe: `POST http://<solr-host>:8983/solr/name_lookup/update?optimize=true`. |
| `numDocs` / `maxDoc` | `maxDoc - numDocs` = soft-deleted docs not yet merged. A high deleted count plus high `segmentCount` amplifies query cost. |
| `size` | Index size on disk. Unexpectedly small may indicate an incomplete data load; it is also the target the Solr host's RAM should approach (see `os` below). |

### JVM (`solr.jvm`)

| Field | What it means |
|---|---|
| `heap_used_pct` | Percentage of JVM heap in use (0–100). **> 80 = memory pressure.** |
| `heap_used_mb` / `heap_max_mb` | Absolute heap figures. Max is set by `-Xmx` in Solr's JVM config (`SOLR_JAVA_MEM`). |
| `cpu_load` | Solr process CPU (0.0–1.0). **> 0.80 = CPU saturation.** |
| `gc_count` / `gc_time_ms` | Cumulative garbage-collection pause count and total pause time across all collectors. Rising `gc_time_ms` relative to uptime = heap pressure (and a source of `handler_ms.p99` spikes). |

### OS resources (`solr.os`)

What the JVM's operating-system bean reports — use these to **size the Solr pod's CPU/memory
requests**. In a container, `available_processors` and `total_physical_mem_mb` are the
container's CPU and memory limits, not the machine's (a `solr:9.10` container started with
`--cpus 2 --memory 1g` on a 12-CPU machine reports 2 and 1024). `system_load_average` is
machine-wide even in a container.

| Field | What it means |
|---|---|
| `available_processors` | CPUs available to Solr: the container's CPU limit, if it has one. |
| `system_load_average` / `system_cpu_load` | Host-wide load and CPU (0.0–1.0). If `system_cpu_load` is higher than `jvm.cpu_load`, other processes are competing with Solr. |
| `total_physical_mem_mb` | RAM available to Solr: the container's memory limit, if it has one. Because Solr mmaps its read-only index, RAM beyond the heap becomes OS page cache for the index, so the host wants RAM well above `heap_max_mb` and ideally approaching the on-disk index `size`. (Free physical memory is not reported: Solr's JVM exposes only Linux `MemFree`, which excludes page cache and reads near-zero on a healthy node. Nor is it read from `/proc/meminfo` by the NameRes API: that would describe the API pod's node, which is not necessarily Solr's. Page-cache stats need a node-level exporter on the Solr host — see #267.) |

### Caches (`solr.cache`)

Reports both `filterCache` and `queryResultCache`. NameRes filters heavily by prefix, type, and
taxon (Solr `fq`), so `filterCache` usually matters most.

| Field | What it means |
|---|---|
| `hitratio` | Fraction of lookups served from cache (0.0–1.0). Should be > 0.90; below 0.50 means Solr is recomputing filters on nearly every query. |
| `evictions` | Rising = the cache is too small for the working set (a symptom of memory pressure). |
| `size` / `lookups` / `hits` | Current entry count, and cumulative lookups/hits. |

Cache sizes are configured in `data-loading/configsets/name_lookup/conf/solrconfig.xml`, and
the two caches are bounded differently. If evictions are high, raise `filterCache`'s `size` (an
entry count), or `queryResultCache`'s `maxRamMB` (a RAM bound; `size` is ignored once `maxRamMB` is
set). Or check whether requests use many distinct filter combinations that defeat caching.

---

## 2. Log messages

Every `lookup()` call emits a line at INFO — or WARNING if it exceeds `SLOW_QUERY_THRESHOLD_MS`
(default 500 ms). The strings of a `/bulk-lookup` stay at INFO however slow they are, so that one
large bulk request against a struggling Solr can't bury the log in SLOW QUERY warnings; it ends
with one `Bulk lookup query for N strings` line giving the total time. For example:

```
INFO: Lookup query to Solr for "diabetes" (autocomplete=False, ... only_taxa=None):
      took 123.45ms (with 100.12ms for the Solr round trip)

WARNING: SLOW QUERY: Lookup query to Solr for "..." ... took 850.12ms (with 840.00ms for the Solr round trip)
```

NameRes v1.7.1 and earlier wrote `(with 100.12ms waiting for Solr)` for the same figure, so
anything parsing these lines should accept both.

A lookup that fails instead logs at WARNING with the outcome (`timeout`, `cancelled` or `error`)
and the exception type, before the exception is re-raised (and, unless it was cancelled, becomes
an HTTP 500). Bulk strings that fail are logged this way too:

```
WARNING: FAILED QUERY (timeout: ReadTimeout): Lookup query to Solr for "..." ...: failed after 60012.40ms
```

Key interpretation:
- **Solr round trip ≈ total** → the time is in the Solr request. That is either Solr itself
  (JVM, index, caches) or the connection, network and transfer in between; compare
  `solr.queries.handler_ms` in `/status?full=true` to tell them apart.
- **Solr round trip is small, total is high** → the bottleneck is Python result processing
  (large result sets being deserialized or filtered).
- Set `LOGLEVEL=DEBUG` to also log the full Solr request and response JSON for every query.

### Solr's own logs

Solr's application log (`solr.log`) is written to both a file and stdout, so in Kubernetes it is
collected by the cluster log stack (Grafana) along with everything else the pod prints.

Solr's **GC log** is separate: it is a JVM-level `-Xlog` sink that bypasses log4j2 entirely.
The `name-lookup` chart routes it to stdout through the `solr.gc_log` value (default
`-Xlog:gc:stdout:time,uptime`, passed to the container as `GC_LOG_OPTS`), so GC lines land in
Grafana next to `solr.log` and survive pod restarts. This arrived with
helxplatform/translator-devops#1044; on ITRB CI it was live by November 2026. To check another
deployment, run `kubectl exec <solr-pod> -- printenv GC_LOG_OPTS`, or search its Solr pod's logs
for `Pause Young`.

Each collection prints one line, with no log level or logger name: the wall-clock time, the JVM
uptime, then the collection number, pause type, heap before -> after (heap size) and the pause
duration, which is what you correlate against a latency tail. In Grafana the line is the `log`
field of the container's record. From ITRB CI's 16G heap:

```
[2026-11-05T19:00:36.672+0000][1.368s] GC(0) Pause Young (Concurrent Start) (Metadata GC Threshold) 382M->50M(16384M) 6.750ms
```

Clearing `solr.gc_log` (setting it to `""`) falls back to Solr's default: a rotating
`$SOLR_LOGS_DIR/solr_gc.log` (`/var/solr/logs`, capped at 9 × 20 MB). That directory is **not**
on the Solr PVC -- only `/var/solr/data` is mounted -- so the file is lost on exactly the pod
restart that makes you want to read it. Until then you can read it in place:

```
kubectl exec <solr-pod> -- tail -n 200 /var/solr/logs/solr_gc.log
```

Note this is `-Xlog:gc`, not `-Xlog:gc*` — the bare form is roughly an order of magnitude less
chatty. Switch to `gc*` temporarily if you are debugging GC itself.

If GC logs are unavailable for any reason, `solr.jvm.gc_count` / `gc_time_ms` in
`/status?full=true` give you the same signal in aggregate — sample them twice to get a rate.

---

## 3. Diagnostic decision tree

```
Solr seems slow or the service is unresponsive
│
├─ Step 1: Check solr.os and solr.queries in /status?full=true
│    │
│    ├─ system_load_average / system_cpu_load is high AND solr.queries.requests is
│    │  climbing fast (sample twice) → the host is under LOAD, not internally broken.
│    │       Check: are callers batching? (/synonyms, /bulk-lookup instead of many /lookup)
│    │       Check: is a client in a retry loop? (repeated identical queries in the logs)
│    │       Fix: rate-limit upstream callers; scale horizontally
│    │
│    └─ Load is normal → the problem is internal to Solr → continue
│
├─ Step 2: Compare the three nested means: recent_queries.lookup_ms.mean,
│    recent_queries.solr_round_trip_ms.mean and solr.queries.handler_ms.mean
│    │
│    ├─ solr_round_trip_ms is small, lookup_ms is high
│    │    → Python result-processing bottleneck
│    │       Check: is `limit` very large? High result counts = expensive deserialization
│    │       Check: NameRes process CPU (scale horizontally if saturated)
│    │
│    ├─ solr_round_trip_ms is high but handler_ms is small
│    │    → the time is between NameRes and Solr: connection setup, network, transfer,
│    │      JSON decoding. Check: network between the API and Solr pods; large responses
│    │      (`limit`, highlighting).
│    │
│    └─ handler_ms is most of solr_round_trip_ms → problem is INSIDE Solr → continue
│
├─ Step 3: Check solr.jvm.heap_used_pct
│    │
│    ├─ > 80 → MEMORY PRESSURE
│    │    ├─ solr.cache.filterCache.evictions rising → cache too small for the
│    │    │    working set. Fix: raise filterCache's `size` in solrconfig.xml.
│    │    └─ Evictions low but heap still high → data/fieldCache is large.
│    │         Fix: raise -Xmx (SOLR_JAVA_MEM) or add RAM to the host.
│    │
│    └─ < 50 → no HEAP pressure. That doesn't rule out PAGE-CACHE pressure: Solr reads
│         its index through mmap, so the RAM it needs is outside the heap (#265).
│         Check: total_physical_mem_mb − heap_max_mb against the index `size`; if it is
│         far smaller, index reads go to disk. Confirm with disk I/O on the Solr host.
│         Fix: shrink the heap or add RAM to the pod. Otherwise → continue
│
├─ Step 4: Check solr.jvm.cpu_load (and solr.os.system_cpu_load)
│    │
│    ├─ > 0.80 → CPU SATURATION
│    │    ├─ solr.segmentCount > 20 → optimize to merge segments
│    │    │    POST http://<solr-host>:8983/solr/name_lookup/update?optimize=true
│    │    ├─ SLOW QUERY warnings for specific queries (many filters, leading wildcards)?
│    │    │    Fix: cache common filter combinations; avoid leading wildcards
│    │    └─ Even load across all queries → scale horizontally (add Solr replicas)
│    │
│    └─ Low CPU, no heap or page-cache pressure, but slow queries → check for JVM GC
│         pauses → continue
│
└─ Step 5: Compare the two p99s to locate the tail, then check jvm.gc_time_ms
     │
     ├─ solr.queries.handler_ms.p99 >> p50 (e.g. p50=50ms, p99=5000ms) with rising gc_time_ms
     │    → Solr-side GC-pause signature. Confirm in the Solr GC log (see "Solr's own
     │      logs" above for where it goes; look for "Pause Young"/"Pause Full" durations
     │      lining up with the slow window). Fix: tune GC_TUNE in the chart's values
     │      (-XX:+UseG1GC -XX:MaxGCPauseMillis=...).
     │
     ├─ recent_queries.lookup_ms.p99 >> solr.queries.handler_ms.p99
     │    → the tail is probably outside Solr, but the two aren't like for like:
     │    recent_queries covers /lookup only over the last RECENT_TIMES_COUNT lookups
     │    (perhaps hours), and its times include failures and the network; solr.queries
     │    covers every /select over the last few minutes.
     │    Rule those out first: timeouts or errors in recent_queries inflate its p99; an
     │    earlier Solr slowdown can still be in the window; and a solr_round_trip_ms that
     │    is most of lookup_ms puts the time on the Solr round trip, not in Python. If they
     │    are ruled out, the tail is in NameRes (large result sets, Python-side GC):
     │    check `limit`, and scale the API horizontally.
     │
     └─ Both p50 and p99 high → sustained overload at all percentiles
          → start with memory (Step 3), then CPU (Step 4)
```

---

## 4. Environment variables

| Variable | Default | Effect |
|---|---|---|
| `SOLR_HOST` | `localhost` | Solr hostname |
| `SOLR_PORT` | `8983` | Solr port |
| `SOLR_CORE` | `name_lookup` | Solr core to query |
| `SOLR_MAX_CONCURRENT_LOOKUPS` | `100` | Most Solr queries a single `/bulk-lookup` request keeps in flight (see [Deployment.md](./Deployment.md)) |
| `SOLR_TIMEOUT_SECONDS` | `60` | Per-query Solr timeout, also applied to `/status`; `0` waits forever |
| `RECENT_TIMES_COUNT` | `50000` | How many recent `/lookup` timings to retain for `recent_queries`. Lower it to reduce memory on low-traffic instances; `0` keeps none. A malformed or negative value logs a warning and falls back to the default, rather than stopping the API from starting. |
| `SLOW_QUERY_THRESHOLD_MS` | `500` | `/lookup` queries slower than this (end-to-end) are logged at WARNING. Falls back to the default like `RECENT_TIMES_COUNT`. |
| `LOGLEVEL` | `INFO` | Set to `DEBUG` to log full Solr request/response JSON for every query |
