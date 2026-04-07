# NameRes Performance Diagnostics

This document explains how to diagnose why Solr may be slow or under strain, using the
observability built into NameRes. It covers what the existing metrics mean, what additional
metrics can be added to the code, and a decision tree for identifying CPU pressure, memory
pressure, and other causes.

---

## 1. Current observability

### `GET /status` — response fields

| Field | What it means |
|---|---|
| `recent_queries.mean_time_ms` | Average round-trip time (Python → Solr → Python) for the last N queries (N set by `RECENT_TIMES_COUNT`, default 1000). Rising mean = sustained slowdown. |
| `recent_queries.recent_times_ms` | The raw list of timings. Scan for a long tail of high values (occasional spikes) vs. a uniformly elevated distribution (sustained slowness). |
| `segmentCount` | Number of Lucene segments in the index. A value above ~20 means Solr is doing more per-segment work per query. Consider triggering a Solr optimize (`/solr/name_lookup/update?optimize=true`). |
| `numDocs` / `maxDoc` | `maxDoc - numDocs = deletedDocs` (soft deletes not yet merged). A very high deleted count combined with high segment count amplifies query cost. |
| `size` | Index size on disk. Unexpectedly small may indicate an incomplete data load. |

### Log messages

Every call to `lookup()` emits a line at INFO level:

```
INFO: Lookup query to Solr for "diabetes" (autocomplete=False, highlighting=False, offset=0, limit=10,
      biolink_types=['biolink:Disease'], only_prefixes=None, exclude_prefixes=None, only_taxa=None)
      took 123.45ms (with 100.12ms waiting for Solr)
```

Key interpretation:
- **"waiting for Solr" ≈ total** → the bottleneck is inside Solr (JVM, index, caches).
- **"waiting for Solr" is small but total is high** → the bottleneck is in Python result processing
  (e.g., large result sets being deserialized or filtered).
- **Consistent high "waiting for Solr"** → follow the decision tree below.

---

## 2. Proposed additional metrics

The following additions to `api/server.py` would give full visibility into Solr internals.
Each is independent and can be implemented separately.

### 2a. JVM and OS info

Add a call to `/solr/admin/info/system?wt=json` in the `status()` function, run in parallel
with the existing `/solr/admin/cores` call using `asyncio.gather()`. Expose these fields in
the status response under `"jvm"` and `"os"` keys:

| New field | Solr JSON path | What it diagnoses |
|---|---|---|
| `jvm.heap_used_pct` | `jvm.memory.raw.used / jvm.memory.raw.max` | **>80% = memory pressure** |
| `jvm.heap_used_bytes` | `jvm.memory.raw.used` | Absolute heap consumption |
| `jvm.heap_max_bytes` | `jvm.memory.raw.max` | JVM -Xmx ceiling |
| `os.process_cpu_load` | `system.processCpuLoad` (0.0–1.0) | **>0.8 = CPU saturation** |
| `os.system_cpu_load` | `system.systemCpuLoad` (0.0–1.0) | Host-wide CPU (other processes) |
| `os.free_physical_memory_bytes` | `system.freePhysicalMemorySize` | OS RAM available to JVM |
| `os.total_physical_memory_bytes` | `system.totalPhysicalMemorySize` | Total host RAM |

Example `status()` change (simplified):
```python
import asyncio, statistics

async with httpx.AsyncClient(timeout=None) as client:
    cores_resp, sysinfo_resp, mbeans_resp = await asyncio.gather(
        client.get(f"http://{SOLR_HOST}:{SOLR_PORT}/solr/admin/cores", params={"action": "STATUS"}),
        client.get(f"http://{SOLR_HOST}:{SOLR_PORT}/solr/admin/info/system", params={"wt": "json"}),
        client.get(f"http://{SOLR_HOST}:{SOLR_PORT}/solr/name_lookup/admin/mbeans",
                   params={"cat": "CACHE", "stats": "true", "wt": "json"}),
    )
```

### 2b. Cache hit/eviction statistics

From the MBeans call above, extract Solr's internal cache statistics under a `"cache"` key:

| New field | What to watch for |
|---|---|
| `cache.filterCache.hitratio` | Should be >0.9. Below 0.5 = Solr re-computing every filter query. |
| `cache.filterCache.evictions` | Rising count = cache too small for the query working set. |
| `cache.queryResultCache.hitratio` | Same interpretation as filterCache. |
| `cache.queryResultCache.evictions` | Same interpretation as filterCache. |

Cache sizes are configured in Solr's `solrconfig.xml`. If evictions are high, increase
`<maxSize>` for the affected cache, or investigate whether requests use many distinct
filter combinations that will never cache well.

### 2c. Query time percentiles

The existing `recent_query_times` deque holds up to 1000 timings. Currently only the mean
is exposed. Add p50/p95/p99 to `recent_queries` using `statistics.quantiles(data, n=100)`:

```python
import statistics
times = list(recent_query_times)
if len(times) >= 2:
    qs = statistics.quantiles(times, n=100)
    p50, p95, p99 = qs[49], qs[94], qs[98]
```

These distinguish:
- **p50 rising** = sustained overload (every query is slow)
- **p99 spiking but p50 stable** = occasional GC pauses or one-off expensive queries

### 2d. Slow query warning logs

Add an environment variable `SLOW_QUERY_THRESHOLD_MS` (default: 500). In `lookup()`, after
the existing INFO log line, add:

```python
SLOW_QUERY_THRESHOLD_MS = float(os.getenv("SLOW_QUERY_THRESHOLD_MS", "500"))

# ... inside lookup(), after computing time_taken_ms:
if time_taken_ms > SLOW_QUERY_THRESHOLD_MS:
    logger.warning(
        f"SLOW QUERY ({time_taken_ms:.2f}ms, {solr_ms:.2f}ms in Solr): "
        f"query={json.dumps(string)}, autocomplete={autocomplete}, "
        f"biolink_types={biolink_types}, only_prefixes={only_prefixes}, "
        f"exclude_prefixes={exclude_prefixes}, only_taxa={only_taxa}, "
        f"results={len(outputs)}"
    )
```

This surfaces outlier queries in log aggregators (e.g., CloudWatch, Datadog) without
needing to poll the status endpoint or scan INFO-level logs.

---

## 3. Diagnostic decision tree

Use this when Solr seems slow or unresponsive.

```
Solr seems slow or strained
│
├─ Step 1: Check logs for "waiting for Solr" vs total time
│    │
│    ├─ "waiting for Solr" is small, total is high
│    │    → Python result-processing bottleneck
│    │       Check: large result sets (increase Solr's limit param or reduce result size)
│    │       Check: Python CPU at capacity (scale horizontally)
│    │
│    └─ "waiting for Solr" is most of total → problem is INSIDE Solr → continue below
│
├─ Step 2: Check jvm.heap_used_pct in /status (requires 2a above)
│    │
│    ├─ >80% → MEMORY PRESSURE
│    │    │
│    │    ├─ Check cache.filterCache.evictions (requires 2b above)
│    │    │    ├─ Rising evictions → cache is too small
│    │    │    │    Fix: increase <maxSize> in Solr's solrconfig.xml filterCache config
│    │    │    └─ No evictions but heap still high → data itself is large
│    │    │         Fix: increase JVM -Xmx (SOLR_JAVA_MEM in Solr's env config)
│    │    │              or add more RAM to the host
│    │    │
│    │    └─ Heap high AND evictions low → not a cache problem
│    │         Consider: index warming, Solr fieldCache for sorted fields
│    │
│    └─ <50% → NOT a memory issue → continue
│
├─ Step 3: Check os.process_cpu_load in /status (requires 2a above)
│    │
│    ├─ >0.8 → CPU SATURATION
│    │    │
│    │    ├─ Check segmentCount in /status
│    │    │    ├─ >20 → run Solr optimize to merge segments
│    │    │    │    POST http://solr-host:8983/solr/name_lookup/update?optimize=true
│    │    │    └─ Low segmentCount → CPU is busy with query evaluation
│    │    │
│    │    ├─ Check slow-query WARNINGs in logs (requires 2d above)
│    │    │    Are expensive queries (many filters, wildcard-heavy) driving the load?
│    │    │    Fix: cache common filter combinations; avoid leading wildcards in queries
│    │    │
│    │    └─ Even load across all queries → scale horizontally (add Solr replicas)
│    │
│    └─ Low CPU and low memory with slow queries → likely GC pauses → continue
│
└─ Step 4: Check p99 vs p50 (requires 2c above)
     │
     ├─ p99 >> p50 (e.g. p50=50ms, p99=5000ms) → GC pause signature
     │    Fix: tune JVM GC settings
     │         -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -XX:G1HeapRegionSize=...
     │         Check Solr GC logs (solr-gc.log) for Full GC frequency
     │
     └─ p50 and p99 both high → Solr is overloaded at all percentiles
          → All of the above apply; start with memory (Step 2)
```

---

## 4. Quick reference: environment variables

| Variable | Default | Effect |
|---|---|---|
| `SOLR_HOST` | `localhost` | Solr hostname |
| `SOLR_PORT` | `8983` | Solr port |
| `RECENT_TIMES_COUNT` | `1000` | How many recent query times to track |
| `SLOW_QUERY_THRESHOLD_MS` | `500` | Log a WARNING for queries slower than this (requires code change 2d) |
| `LOGLEVEL` | `INFO` | Set to `DEBUG` to log full Solr request/response JSON |
