# NameRes Performance Diagnostics

This document explains how to diagnose why Solr may be slow or under strain, using the
observability built into NameRes. It covers what the metrics in `/status` mean, how to read
the log messages, and a decision tree for identifying CPU pressure, memory pressure, high
load, and other causes.

All the Solr-side metrics below live under `solr_metrics`, which is only fetched when you
call **`/status?full=true`** (it adds a round-trip to Solr, so the default `/status` stays
cheap for Kubernetes liveness probes). See [API.md](API.md#status) for the full response.

---

## 1. `/status` response fields

### Frontend query metrics (`recent_queries`)

Tracked by the Python process; reflects the full round-trip time seen by callers. Always
present (no `?full=true` needed).

| Field | What it means |
|---|---|
| `mean_time_ms` | Average round-trip time (Python → Solr → Python) over the last `count` `/lookup` queries. Rising mean = sustained slowdown. |
| `mean_solr_time_ms` | Of that, the average time spent waiting on Solr. If this is close to `mean_time_ms`, the bottleneck is inside Solr; if it is much smaller, the bottleneck is Python-side result processing. |
| `count` / `max` | How many queries are currently in the rolling window, and its capacity (`RECENT_TIMES_COUNT`, default 50000). |

For latency percentiles, use `solr_metrics.query_handler` below — Solr computes them over its
own `/select` histogram.

### Solr query handler (`solr_metrics.query_handler`)

Cumulative `/select` statistics straight from Solr.

| Field | What it means |
|---|---|
| `requests` | Total `/select` requests handled since Solr started. Sample it twice to get a rate. |
| `errors` / `timeouts` | Cumulative counts. Any non-zero `timeouts` means Solr is dropping queries under load. |
| `mean_ms` | Mean Solr-side request time. |
| `p75_ms` / `p95_ms` / `p99_ms` | Latency percentiles. `p99_ms` spiking while `mean_ms`/`p75_ms` stay stable = occasional GC pauses or one-off expensive queries; all of them rising = sustained overload. |

### Index health (top level of `/status`)

| Field | What it means |
|---|---|
| `segmentCount` | Number of Lucene segments. Above ~20 means Solr does more per-segment work per query. The index is read-only after loading, so an optimize is safe: `POST http://<solr-host>:8983/solr/name_lookup/update?optimize=true`. |
| `numDocs` / `maxDoc` | `maxDoc - numDocs` = soft-deleted docs not yet merged. A high deleted count plus high `segmentCount` amplifies query cost. |
| `size` | Index size on disk. Unexpectedly small may indicate an incomplete data load; it is also the target the Solr host's RAM should approach (see `host` below). |

### JVM (`solr_metrics.jvm`)

| Field | What it means |
|---|---|
| `heap_used_pct` | Percentage of JVM heap in use (0–100). **> 80 = memory pressure.** |
| `heap_used_mb` / `heap_max_mb` | Absolute heap figures. Max is set by `-Xmx` in Solr's JVM config (`SOLR_JAVA_MEM`). |
| `cpu_load` | Solr process CPU (0.0–1.0). **> 0.80 = CPU saturation.** |
| `gc_count` / `gc_time_ms` | Cumulative garbage-collection pause count and total pause time across all collectors. Rising `gc_time_ms` relative to uptime = heap pressure (and a source of `p99_ms` spikes). |

### Host resources (`solr_metrics.host`)

The machine Solr runs on — use these to **size the Solr pod's CPU/memory requests**.

| Field | What it means |
|---|---|
| `available_processors` | CPUs visible to Solr. |
| `system_load_average` / `system_cpu_load` | Host-wide load and CPU (0.0–1.0). If `system_cpu_load` is higher than `jvm.cpu_load`, other processes are competing with Solr. |
| `total_physical_mem_mb` / `free_physical_mem_mb` | Host RAM. Because Solr mmaps its read-only index, RAM beyond the heap becomes OS page cache for the index, so the host wants RAM well above `heap_max_mb` and ideally approaching the on-disk index `size`. Note `free_physical_mem_mb` is Linux `MemFree` and *excludes* page cache, so it reads low on a warm node — that is expected, not a problem. |

### Caches (`solr_metrics.cache`)

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
(default 500 ms):

```
INFO: Lookup query to Solr for "diabetes" (autocomplete=False, ... only_taxa=None):
      took 123.45ms (with 100.12ms waiting for Solr)

WARNING: SLOW QUERY: Lookup query to Solr for "..." ... took 850.12ms (with 840.00ms waiting for Solr)
```

Key interpretation:
- **"waiting for Solr" ≈ total** → the bottleneck is inside Solr (JVM, index, caches).
- **"waiting for Solr" is small, total is high** → the bottleneck is Python result processing
  (large result sets being deserialized or filtered).
- Set `LOGLEVEL=DEBUG` to also log the full Solr request and response JSON for every query.

---

## 3. Diagnostic decision tree

```
Solr seems slow or the service is unresponsive
│
├─ Step 1: Check solr_metrics.host and query_handler in /status?full=true
│    │
│    ├─ system_load_average / system_cpu_load is high AND query_handler.requests is
│    │  climbing fast (sample twice) → the host is under LOAD, not internally broken.
│    │       Check: are callers batching? (/synonyms, /bulk-lookup instead of many /lookup)
│    │       Check: is a client in a retry loop? (repeated identical queries in the logs)
│    │       Fix: rate-limit upstream callers; scale horizontally
│    │
│    └─ Load is normal → the problem is internal to Solr → continue
│
├─ Step 2: Compare recent_queries.mean_solr_time_ms vs mean_time_ms (and the log lines)
│    │
│    ├─ mean_solr_time_ms is small, mean_time_ms is high
│    │    → Python result-processing bottleneck
│    │       Check: is `limit` very large? High result counts = expensive deserialization
│    │       Check: NameRes process CPU (scale horizontally if saturated)
│    │
│    └─ mean_solr_time_ms is most of mean_time_ms → problem is INSIDE Solr → continue
│
├─ Step 3: Check solr_metrics.jvm.heap_used_pct
│    │
│    ├─ > 80 → MEMORY PRESSURE
│    │    ├─ solr_metrics.cache.filterCache.evictions rising → cache too small for the
│    │    │    working set. Fix: raise <maxSize> for filterCache in solrconfig.xml.
│    │    └─ Evictions low but heap still high → data/fieldCache is large.
│    │         Fix: raise -Xmx (SOLR_JAVA_MEM) or add RAM to the host.
│    │
│    └─ < 50 → not a memory issue → continue
│
├─ Step 4: Check solr_metrics.jvm.cpu_load (and host.system_cpu_load)
│    │
│    ├─ > 0.80 → CPU SATURATION
│    │    ├─ segmentCount > 20 → optimize to merge segments
│    │    │    POST http://<solr-host>:8983/solr/name_lookup/update?optimize=true
│    │    ├─ SLOW QUERY warnings for specific queries (many filters, leading wildcards)?
│    │    │    Fix: cache common filter combinations; avoid leading wildcards
│    │    └─ Even load across all queries → scale horizontally (add Solr replicas)
│    │
│    └─ Low CPU and low memory but slow queries → likely JVM GC pauses → continue
│
└─ Step 5: Check query_handler.p99_ms vs mean_ms, and jvm.gc_time_ms
     │
     ├─ p99_ms >> mean_ms (e.g. mean=50ms, p99=5000ms) with rising gc_time_ms → GC-pause
     │    signature. Fix: tune Solr's JVM GC (-XX:+UseG1GC -XX:MaxGCPauseMillis=200);
     │    check Solr's GC logs for Full GC frequency and duration.
     │
     └─ mean_ms and p99_ms both high → sustained overload at all percentiles
          → start with memory (Step 3), then CPU (Step 4)
```

---

## 4. Environment variables

| Variable | Default | Effect |
|---|---|---|
| `SOLR_HOST` | `localhost` | Solr hostname |
| `SOLR_PORT` | `8983` | Solr port |
| `SOLR_CORE` | `name_lookup` | Solr core to query |
| `RECENT_TIMES_COUNT` | `50000` | How many recent `/lookup` timings to retain for `recent_queries`. Lower it to reduce memory on low-traffic instances. |
| `SLOW_QUERY_THRESHOLD_MS` | `500` | `/lookup` queries slower than this (end-to-end) are logged at WARNING |
| `LOGLEVEL` | `INFO` | Set to `DEBUG` to log full Solr request/response JSON for every query |
