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

Tracked by the Python process; reflects the full round-trip time seen by callers of `/lookup`.
Always present (no `?full=true` needed). `/bulk-lookup` is left out: one bulk request can hold
thousands of strings, enough to swamp the window, and its strings compete with each other for
Solr in a way a `/lookup` caller's doesn't.

| Field | What it means |
|---|---|
| `mean_ms` | Average round-trip time (Python → Solr → Python) over the last `requests` `/lookup` queries. Rising mean = sustained slowdown. |
| `mean_solr_wait_ms` | Of that, the average time spent waiting on Solr. If this is close to `mean_ms`, the bottleneck is inside Solr; if it is much smaller, the bottleneck is Python-side result processing. |
| `p50_ms` / `p95_ms` / `p99_ms` | **End-to-end** latency percentiles as the caller experiences them, over the window. Because these include NameRes processing, comparing them against Solr's own `solr.queries` percentiles localizes a latency tail: if the end-to-end `p99_ms` is far above `solr.queries.p99_ms`, the tail is in NameRes (result processing, GC in the Python process), not Solr. Computed locally, so they need no `?full=true` round-trip; they cost a sort of the whole window, though, so `/status` reuses them for up to 10 seconds. |
| `requests` / `window_size` | How many queries are currently in the rolling window, and its capacity (`RECENT_TIMES_COUNT`, default 50000). Unlike `solr.queries.requests`, `requests` is windowed, not cumulative, so the two can't be compared. |
| `errors` / `timeouts` / `cancelled` | Queries in the window that failed: `timeouts` hit `SOLR_TIMEOUT_SECONDS`, `cancelled` were abandoned while waiting on Solr (a server shutdown, say), and `errors` are the rest (Solr returned an error or couldn't be reached). They are disjoint, so their sum is every failure. Unlike `solr.queries.errors`/`timeouts` these are windowed, not cumulative, and they include failures Solr never saw (it was unreachable, or NameRes gave up waiting). Failed queries are counted in the timings above, so a run of timeouts shows up in `p99_ms` too. |

### Solr queries (`solr.queries`)

Solr's own figures for every `/select` the core has served -- `/synonyms` and `/reverse_lookup`
as well as `/lookup`, where `recent_queries` times `/lookup` alone. `requests`, `errors` and
`timeouts` count from Solr's start. `mean_ms` and the percentiles come from Solr's request timer,
which by default samples with an exponentially decaying reservoir, so they describe roughly the
last few minutes rather than Solr's lifetime.

| Field | What it means |
|---|---|
| `requests` | Total `/select` requests handled since Solr started. Sample it twice to get a rate. |
| `errors` / `timeouts` | Cumulative counts. `timeouts` counts only requests Solr itself cut short and answered with partial results (as `timeAllowed` would; NameRes does not set it), so it normally stays at 0. Requests NameRes gave up waiting for are `recent_queries.timeouts`. |
| `mean_ms` | Mean Solr-side request time, over the same recent sample as the percentiles. |
| `p50_ms` / `p95_ms` / `p99_ms` | Solr-side latency percentiles. `p99_ms` spiking while `p50_ms` stays stable = occasional GC pauses or one-off expensive queries; all of them rising = sustained overload. Compare against `recent_queries` percentiles to tell Solr-side tails from NameRes-side ones. |

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
| `gc_count` / `gc_time_ms` | Cumulative garbage-collection pause count and total pause time across all collectors. Rising `gc_time_ms` relative to uptime = heap pressure (and a source of `p99_ms` spikes). |

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

Cache sizes are configured in Solr's `solrconfig.xml`. If evictions are high, increase
`<maxSize>` for the affected cache — or check whether requests use many distinct filter
combinations that defeat caching.

---

## 2. Log messages

Every `lookup()` call emits a line at INFO — or WARNING if it exceeds `SLOW_QUERY_THRESHOLD_MS`
(default 500 ms). The strings of a `/bulk-lookup` stay at INFO however slow they are, so that one
large bulk request against a struggling Solr can't bury the log in SLOW QUERY warnings; it ends
with one `Bulk lookup query for N strings` line giving the total time. For example:

```
INFO: Lookup query to Solr for "diabetes" (autocomplete=False, ... only_taxa=None):
      took 123.45ms (with 100.12ms waiting for Solr)

WARNING: SLOW QUERY: Lookup query to Solr for "..." ... took 850.12ms (with 840.00ms waiting for Solr)
```

A lookup that fails instead logs at WARNING with the outcome (`timeout`, `cancelled` or `error`)
and the exception type, before the exception is re-raised (and, unless it was cancelled, becomes
an HTTP 500). Bulk strings that fail are logged this way too:

```
WARNING: FAILED QUERY (timeout: ReadTimeout): Lookup query to Solr for "..." ...: failed after 60012.40ms
```

Key interpretation:
- **"waiting for Solr" ≈ total** → the bottleneck is inside Solr (JVM, index, caches).
- **"waiting for Solr" is small, total is high** → the bottleneck is Python result processing
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
├─ Step 2: Compare recent_queries.mean_solr_wait_ms vs mean_ms (and the log lines)
│    │
│    ├─ mean_solr_wait_ms is small, mean_ms is high
│    │    → Python result-processing bottleneck
│    │       Check: is `limit` very large? High result counts = expensive deserialization
│    │       Check: NameRes process CPU (scale horizontally if saturated)
│    │
│    └─ mean_solr_wait_ms is most of mean_ms → problem is INSIDE Solr → continue
│
├─ Step 3: Check solr.jvm.heap_used_pct
│    │
│    ├─ > 80 → MEMORY PRESSURE
│    │    ├─ solr.cache.filterCache.evictions rising → cache too small for the
│    │    │    working set. Fix: raise <maxSize> for filterCache in solrconfig.xml.
│    │    └─ Evictions low but heap still high → data/fieldCache is large.
│    │         Fix: raise -Xmx (SOLR_JAVA_MEM) or add RAM to the host.
│    │
│    └─ < 50 → not a memory issue → continue
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
│    └─ Low CPU and low memory but slow queries → likely JVM GC pauses → continue
│
└─ Step 5: Compare the two p99s to locate the tail, then check jvm.gc_time_ms
     │
     ├─ solr.queries.p99_ms >> p50_ms (e.g. p50=50ms, p99=5000ms) with rising gc_time_ms
     │    → Solr-side GC-pause signature. Confirm in the Solr GC log (see "Solr's own
     │      logs" above for where it goes; look for "Pause Young"/"Pause Full" durations
     │      lining up with the slow window). Fix: tune GC_TUNE in the chart's values
     │      (-XX:+UseG1GC -XX:MaxGCPauseMillis=...).
     │
     ├─ recent_queries.p99_ms >> solr.queries.p99_ms → the tail is in NameRes, not Solr
     │    (large result sets, Python-side GC). Check `limit`; scale the API horizontally.
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
