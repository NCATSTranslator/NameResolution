# NameRes Performance Diagnostics

This document explains how to diagnose why Solr may be slow or under strain, using the
observability built into NameRes. It covers what the metrics in `/status` mean, how to
read the log messages, and a decision tree for identifying CPU pressure, memory pressure,
high query rate, and other causes.

---

## 1. `/status` response fields

The response has two main diagnostic sections: `recent_queries` (Python frontend metrics)
and `solr` (everything from the Solr database itself).

### Frontend query metrics (`recent_queries`)

These are tracked by the Python process and reflect the full round-trip time seen by callers.

#### Latency

| Field | What it means |
|---|---|
| `mean_time_ms` | Average round-trip time (Python → Solr → Python) over the entries in `query_log`. Rising mean = sustained slowdown. |
| `p50_ms` / `p95_ms` / `p99_ms` | Latency percentiles over the same window. p50 rising = every query is slow. p99 spiking but p50 stable = occasional GC pauses or one-off expensive queries. |

#### Rate (`recent_queries.rate`)

Query start timestamps and durations are stored together in a single `query_log` deque
(up to `QUERY_LOG_SIZE` entries, default 50,000). The large size ensures rate estimates
stay meaningful even at high query rates (e.g., 500 qps fills 1,000 entries in 2 seconds,
but 50,000 entries covers 100 seconds).

| Field | What it means |
|---|---|
| `history_span_seconds` | Time from the oldest to newest entry in the log. Shows how much history backs the rate estimates. |
| `time_since_last_query_seconds` | Seconds since the most recent query. Large values mean the service is idle and windowed rates are stale. |
| `queries_last_10s` / `queries_per_second_last_10s` | 10-second window. Use this to catch the onset of a spike before the 60s average catches up. |
| `queries_last_60s` / `queries_per_second_last_60s` | 1-minute average rate. Use this for current load. |
| `queries_last_300s` / `queries_per_second_last_300s` | 5-minute average rate. Use this to smooth over short bursts. |
| `inter_arrival_ms.mean` | Average gap between consecutive queries in ms. Equals 1000 / mean_qps; cross-checks the windowed rates. |
| `inter_arrival_ms.median` | Median gap. More robust than mean under burst traffic. |
| `inter_arrival_ms.min` | Tightest burst observed — how closely packed the busiest queries were. |
| `inter_arrival_ms.max` | Longest idle gap in the log window. |
| `inter_arrival_ms.p95` | 95% of queries arrive within this gap. |

The key diagnostic use: **if Solr is slow AND the query rate is high**, the cause is likely
load rather than an internal Solr problem. If the rate is normal but Solr is slow, look at
the `solr` fields below.

### Solr database metrics (`solr`)

All fields under `solr` come from Solr admin endpoints fetched in parallel when `/status`
is called. They are `null` if the relevant Solr endpoint is unreachable (a warning is logged).

#### Index health

| Field | What it means |
|---|---|
| `solr.segmentCount` | Number of Lucene segments. Above ~20 means Solr does more per-segment work per query. Consider triggering an optimize (`POST /solr/name_lookup/update?optimize=true`). |
| `solr.numDocs` / `solr.maxDoc` | `maxDoc - numDocs` = soft-deleted docs not yet merged. High deleted count + high segment count amplifies query cost. |
| `solr.size` | Index size on disk. Unexpectedly small may indicate an incomplete data load. |

#### JVM and OS (`solr.jvm`, `solr.os`)

Fetched from Solr's `/solr/admin/info/system` endpoint.

| Field | What it means |
|---|---|
| `solr.jvm.heap_used_pct` | Fraction of JVM heap in use (0.0–1.0). **>0.80 = memory pressure.** |
| `solr.jvm.heap_used_bytes` / `solr.jvm.heap_max_bytes` | Absolute heap figures. Max is set by `-Xmx` in Solr's JVM config. |
| `solr.os.process_cpu_load` | Solr process CPU (0.0–1.0). **>0.80 = CPU saturation.** |
| `solr.os.system_cpu_load` | Host-wide CPU. If higher than process load, other processes are competing. |
| `solr.os.free_physical_memory_bytes` | OS RAM available. If low, the OS may be swapping. |

#### Cache statistics (`solr.cache`)

Fetched from Solr's MBeans endpoint. Reports `filterCache` and `queryResultCache`.

| Field | What it means |
|---|---|
| `hitratio` | Fraction of cache lookups that were hits. Should be >0.90. Below 0.50 = Solr is re-computing filters on nearly every query. |
| `evictions` | Rising count = cache too small for the working set (a symptom of memory pressure). |
| `size` / `maxSize` | Current entries vs. configured maximum. If `size ≈ maxSize`, the cache is full and evictions are likely. |

Cache sizes are configured in Solr's `solrconfig.xml`. If evictions are high, increase
`<maxSize>` for the affected cache — or investigate whether requests use many distinct
filter combinations that defeat caching.

---

## 2. Log messages

Every call to `lookup()` emits a line at INFO (or WARNING if slow):

```
INFO: Lookup query to Solr for "diabetes" (autocomplete=False, highlighting=False, offset=0,
      limit=10, biolink_types=['biolink:Disease'], only_prefixes=None, exclude_prefixes=None,
      only_taxa=None) took 123.45ms (with 100.12ms waiting for Solr)
```

```
WARNING: SLOW QUERY: Lookup query to Solr for "..." ... took 850.12ms (with 840.00ms waiting for Solr)
```

Key interpretation:
- **"waiting for Solr" ≈ total** → the bottleneck is inside Solr (JVM, index, caches).
- **"waiting for Solr" is small, total is high** → the bottleneck is Python result processing
  (large result sets being deserialized or filtered).
- A WARNING is emitted when total time exceeds `SLOW_QUERY_THRESHOLD_MS` (default 500ms).
  Set `LOGLEVEL=DEBUG` to also log the full Solr request and response JSON.

---

## 3. Diagnostic decision tree

```
Solr seems slow or the service is unresponsive
│
├─ Step 1: Check recent_queries.rate in /status
│    │
│    ├─ queries_per_second_last_60s is unusually high (e.g. 10x normal)
│    │    → HIGH QUERY RATE is driving the load
│    │       Check: are requests batching correctly? (use /reverse_lookup or /bulk_lookup)
│    │       Check: is a client in a retry loop? (look for repeated identical queries in logs)
│    │       Fix: rate-limit upstream callers; scale horizontally
│    │
│    └─ Rate is normal → the problem is internal to Solr → continue
│
├─ Step 2: Check log messages for "waiting for Solr" vs total time
│    │
│    ├─ "waiting for Solr" is small, total is high
│    │    → Python result-processing bottleneck
│    │       Check: is limit very large? High result counts = expensive deserialization
│    │       Check: Python process CPU (scale horizontally if saturated)
│    │
│    └─ "waiting for Solr" is most of total → problem is INSIDE Solr → continue
│
├─ Step 3: Check solr.jvm.heap_used_pct in /status
│    │
│    ├─ >0.80 → MEMORY PRESSURE
│    │    │
│    │    ├─ Check solr.cache.filterCache.evictions
│    │    │    ├─ Rising evictions → cache is too small for the working set
│    │    │    │    Fix: increase <maxSize> in solrconfig.xml for filterCache
│    │    │    └─ Evictions low but heap still high → data or fieldCache is large
│    │    │         Fix: increase JVM -Xmx (SOLR_JAVA_MEM in Solr's environment config)
│    │    │              or add more RAM to the host
│    │    │
│    │    └─ Heap high AND evictions low → not a cache-size problem
│    │         Consider: Solr fieldCache warming on startup; large stored fields
│    │
│    └─ <0.50 → NOT a memory issue → continue
│
├─ Step 4: Check solr.os.process_cpu_load in /status
│    │
│    ├─ >0.80 → CPU SATURATION
│    │    │
│    │    ├─ Check solr.segmentCount in /status
│    │    │    ├─ >20 → run Solr optimize to merge segments
│    │    │    │    POST http://solr-host:8983/solr/name_lookup/update?optimize=true
│    │    │    └─ Low segmentCount → CPU is busy with query evaluation itself
│    │    │
│    │    ├─ Check SLOW QUERY WARNINGs in logs
│    │    │    Are specific queries (many filters, wildcard-heavy) driving the load?
│    │    │    Fix: cache common filter combinations; avoid leading wildcards
│    │    │
│    │    └─ Even load across all queries → scale horizontally (add Solr replicas)
│    │
│    └─ Low CPU and low memory but slow queries → likely JVM GC pauses → continue
│
└─ Step 5: Check p99 vs p50 in recent_queries
     │
     ├─ p99 >> p50 (e.g. p50=50ms, p99=5000ms) → GC pause signature
     │    Fix: tune JVM GC settings in Solr's JVM config:
     │         -XX:+UseG1GC -XX:MaxGCPauseMillis=200
     │         Check Solr GC logs (solr-gc.log) for Full GC frequency and duration
     │
     └─ p50 and p99 both high → sustained overload at all percentiles
          → All of the above apply; start with memory (Step 3)
```

---

## 4. Environment variables

| Variable | Default | Effect |
|---|---|---|
| `SOLR_HOST` | `localhost` | Solr hostname |
| `SOLR_PORT` | `8983` | Solr port |
| `QUERY_LOG_SIZE` | `50000` | How many `(timestamp, duration)` pairs to retain. Covers both latency percentiles and rate estimation. At 500 qps this covers ~100 seconds; lower to reduce memory on low-traffic instances. |
| `SLOW_QUERY_THRESHOLD_MS` | `500` | Queries slower than this are logged at WARNING level |
| `LOGLEVEL` | `INFO` | Set to `DEBUG` to log full Solr request/response JSON for every query |
