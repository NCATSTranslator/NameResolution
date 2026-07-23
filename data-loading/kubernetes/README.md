# Loading NameRes data on Kubernetes

These three files create a pod on a Kubernetes cluster big enough to load a whole
Babel release into Solr and produce the `snapshot.backup.tar.gz` that NameRes
instances restore. The pipeline itself is described in
[`../README.md`](../README.md); this file is about the resources it needs and how to
tell, from Grafana, whether it has the right ones.

| File | What it is |
| --- | --- |
| `nameres-loading.k8s.yaml` | The pod. A workspace you exec into, not a job. |
| `nameres-loading-solr.k8s.yaml` | PVC mounted at `/var/solr` -- the Solr home, i.e. the index. |
| `nameres-loading-data.k8s.yaml` | PVC mounted at `data/` -- synonym files in, tarball out. |

## Running a load

```shell
$ kubectl apply -f nameres-loading-solr.k8s.yaml
$ kubectl apply -f nameres-loading-data.k8s.yaml
$ kubectl apply -f nameres-loading.k8s.yaml
$ kubectl exec -it nameres-loading -- /bin/bash

# Inside the pod:
$ cd /code/nameres-data-loading
$ make all SYNONYMS_URL=https://stars.renci.org/var/babel_outputs/<release>/synonyms/
$ make data/backup.done
```

`make all` downloads and splits the synonym files, starts Solr, creates the
`name_lookup` core from the checked-in configset and loads it. `make
data/backup.done` optimizes the index, stops Solr and writes
`data/snapshot.backup.tar.gz`. Copy that out (`kubectl cp`, or push it straight to
wherever `dataUrl` will point) and delete the pod and both PVCs.

Run it under `screen` or `tmux`: a full load is hours long, and `kubectl exec` does
not survive a dropped connection.

## What actually costs time

In order, the load is:

1. **Download** (`wget`), network-bound. One connection, ~130G.
2. **Index**, CPU-bound. Solr analyses every synonym as it arrives -- tokenizing and
   lowercasing each name for `names` and again for the `names_exactish` copy field.
   This is where the parallel uploads earn their keep.
3. **Merge**, I/O-bound, overlapping the above.
4. **Optimize**, I/O-bound. One forced merge of the entire index into a single
   segment: read ~130G, write ~130G.
5. **Compress**, CPU-bound, parallelised with `pigz`.

So the two levers are **CPU** (steps 2 and 5) and **disk throughput** (steps 3 and
4). Memory beyond Solr's heap only helps as page cache, and disk *space* buys
nothing but the absence of failure.

## Sizing

**CPU -- the main lever, and the one to try first.** The loader runs one upload per
CPU and `pigz` gets the same number, so raising `cpu` in the pod spec is picked up
automatically; there is no second setting to keep in sync. Note that both numbers
come from the cgroup *limit* via [`../available-cpus.sh`](../available-cpus.sh), not
from `nproc`: inside a container `nproc` reports the node's cores, so on a 64-core
node a pod limited to 8 CPUs would otherwise start 64 uploads and spend the
difference being throttled. If you need to override it, set `LOAD_PARALLELISM`.

**Memory -- less than you would think.** `SOLR_MEM` (default `31G`) becomes
`solr -m`, which sets *both* `-Xms` and `-Xmx`, so it is committed rather than a
ceiling. Indexing does not want a large heap: Lucene buffers documents in
`ramBufferSizeMB` (512 MB, in the configset) and streams merges through the OS page
cache, so heap past a few GB is memory taken away from that cache. Staying under 32G
also keeps compressed object pointers, which a bigger heap silently gives up. The
pod's remaining memory is not wasted -- the kernel uses it to cache index files, and
page cache is charged to the container's limit, so the limit is what decides how much
of the index can be cached during merges.

This is the same lesson as the serving side, where measurements showed 11-13Gi of
Solr RSS against 111Gi of page cache (see `solr.resources` in the chart's
`values.yaml`).

**Disk space** is a floor, not a lever:

- `/var/solr` needs **2-3x the finished index**, because `optimize=true` writes the
  new single segment before deleting the old ones. 400Gi against a ~127Gi index is
  about 3x; if the index passes ~140Gi, raise it.
- `data/` needs the uncompressed synonyms plus the tarball. It no longer needs room
  for an uncompressed copy of the backup -- that staging step is gone.

**Disk speed** is a lever, and possibly the biggest one. Both PVCs use
`storageClassName: basic`. If the cluster offers a faster (SSD/NVMe-backed) class,
the Solr PVC is the one that would benefit: it takes the whole write load of the
indexing run and then the read-and-write of the optimize. That is worth more than
any amount of tuning in the Makefile.

## Watching a load in Grafana

Take a look at these while a load is running; each one tells you what to change next
time.

| What to look at | What it means | What to do |
| --- | --- | --- |
| **CPU throttling** (`container_cpu_cfs_throttled_seconds_total`, or the "throttling" panel) | Sustained throttling means the pod wants more CPU than its limit. | Raise `cpu`. The loader picks it up on its own. |
| **CPU usage vs. limit** | Flat at the limit during the load = CPU-bound, and more will help. Well below it = something else is the constraint, probably disk. | If it is below the limit, look at disk before adding CPU. |
| **Memory usage (RSS/WSS)** | This is roughly the JVM. It should sit near `SOLR_MEM` and be stable. | If it is far below `SOLR_MEM`, lower `SOLR_MEM` and give the memory back as cache. |
| **Memory usage (cache)** | Page cache: the index data the kernel is holding. Rising to fill the headroom is healthy. | If it is pinned at (limit - heap) for the whole run, more memory may speed up merges. |
| **Disk read/write throughput on the Solr PVC** | Flat-topped during merges and the optimize = saturated volume. | A faster storage class, not more CPU. |
| **Wall-clock time of each `make` step** | The logs in `data/logs/` are timestamped per step. | Tells you which of the five phases above to attack at all. |

Two things worth checking specifically, since they are new:

- **Does parallelism help linearly?** Note the load's wall-clock time and the CPU
  usage at the current setting. If CPU sits at the limit and throttling is high, the
  next load with more CPU should be faster in proportion. If it does not improve,
  the bottleneck has moved to disk.
- **Is 31G of heap right?** Watch RSS during the load. If it plateaus well below 31G
  (likely), drop `SOLR_MEM` further -- 16G is probably enough -- and let the page
  cache have the difference. If Solr spends its time in GC or dies with an
  OutOfMemoryError, raise it, but suspect something else first: a bulk load into a
  single core should not need tens of gigabytes of live heap.

## Knobs

All of these can be set on the `make` command line, or as environment variables in
the pod spec (the Makefile uses `?=`).

| Setting | Default | What it does |
| --- | --- | --- |
| `SYNONYMS_URL` | Babel 2025sep1 | Which Babel release to load. |
| `SOLR_MEM` | `31G` | Solr's heap during the load (`-Xms` and `-Xmx`). |
| `LOAD_PARALLELISM` | CPU limit | Concurrent uploads. Only set this to override the cgroup-derived default. |
| `SPLIT_SIZE` / `SPLIT_LINES` | `2G` / 10M | How the big synonym files are split. More, smaller chunks give the parallel loader a shorter tail at the end of the run; fewer, larger ones mean less `split` time up front. |
| `SOLR_STARTUP_TRIES` | 60 | How long the loader waits for Solr (3s each) before giving up. |
