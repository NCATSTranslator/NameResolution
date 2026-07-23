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

## Two volumes

- **`/var/solr`, the index.** Takes the entire write load of the indexing run and then
  the read-and-write of the optimize, so its throughput is what decides how long a
  load takes. Pure intermediate state: once the tarball exists the index is worthless.
- **`data/`, the working directory.** Holds the ~130G download and, at the end, the
  backup tarball -- the only thing the whole exercise actually produces.

Both are persistent PVCs today, so **both outlive the pod and both need deleting by
hand** once the tarball is somewhere safe.

The index would be much better off on node-local NVMe: it is the volume that most
wants speed, and it is the one we could afford to lose, since it can be rebuilt from
the synonym files next door. That change is written and waiting in a separate PR,
blocked on [issue #280](https://github.com/NCATSTranslator/NameResolution/issues/280),
which asks for the namespace to support `nvme-ephemeral` volumes.

Being persistent does buy one thing in the meantime. The Makefile's stamp files --
"the core exists", "the data is loaded" -- live on the Solr volume rather than in
`data/`, so a replacement pod skips the steps that already finished instead of
starting from nothing. Logs go the other way, to `data/logs/`, because logs matter
most when a run has died.

That resumption is per *step*, though, and the load is one step. A pod that dies
mid-load leaves a partly-filled core and no stamp, and the load cannot be continued:
it is additive and assigns fresh UUIDs, so re-running it would index the already-
loaded files a second time. The loader refuses a non-empty core for that reason. The
recovery is to throw the index away and load again -- the download, which is the part
worth keeping, is on the other volume and survives:

```shell
$ make stop-solr && rm -rf /var/solr/name_lookup /var/solr/*.done
$ make all
```

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

### Check you have the image you think you have

The Makefile, the loader script and the configset are all **baked into the image**.
Editing them in a checkout does nothing; the `image:` tag in the pod spec is what
decides which version of the pipeline actually runs. The publish workflow only tags
`latest` on a published release, so between merging a change and cutting a release,
`latest` is the *previous* pipeline. Before starting an eight-hour load:

```shell
$ ls available-cpus.sh                                       # missing => pre-2026-07 image
$ grep ramBufferSizeMB configsets/name_lookup/conf/solrconfig.xml
$ grep '^SOLR_MEM' Makefile
```

If the image is close enough but one setting is stale, you can edit these files
inside the pod before running `make`. The configset in particular is only read when
the core is created, so changing `solrconfig.xml` before `make all` takes full
effect.

`make all` downloads and splits the synonym files, starts Solr, creates the
`name_lookup` core from the checked-in configset and loads it. `make
data/backup.done` optimizes the index, stops Solr and writes
`data/snapshot.backup.tar.gz`. Copy that out (`kubectl cp`, or push it straight to
wherever `dataUrl` will point), then delete the pod and **both** PVCs -- they are
persistent, and between them they hold around a terabyte.

Run it under `screen` or `tmux`: a full load is hours long, and `kubectl exec` does
not survive a dropped connection.

If the pod dies partway through, recreate it and run the same commands: both volumes
survive, so make picks up from the last completed step rather than starting over. The
exception is dying *during* the load, which cannot be resumed -- see "Two volumes"
above for how to clear the core and restart it.

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
automatically; there is no second setting to keep in sync. The pod asks for 32,
which is deliberately short of what the namespace has spare: the real limit is that
the request has to fit on a **single node**.
Namespace quota is the easy test to pass and the wrong one to plan against -- check
`kubectl describe node` before raising this. Note that both numbers
come from the cgroup *limit* via [`../available-cpus.sh`](../available-cpus.sh), not
from `nproc`: inside a container `nproc` reports the node's cores, so on a 64-core
node a pod limited to 8 CPUs would otherwise start 64 uploads and spend the
difference being throttled. If you need to override it, set `LOAD_PARALLELISM`.

**Memory -- less than you would think.** `SOLR_MEM` (default `31G`) becomes
`solr -m`, which sets *both* `-Xms` and `-Xmx`, so it is committed rather than a
ceiling. Indexing does not want a large heap: Lucene buffers documents in
`ramBufferSizeMB` (2 GB, in the configset) and streams merges through the OS page
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
  new single segment before deleting the old ones. It is 600Gi, sized well above the
  finished index (~127Gi for Babel 2025nov4, ~111Gi for 2026jul22 -- release size
  moves both ways) to leave room for a larger future one, because running out of room
  happens during the optimize -- the last step of a multi-hour load.
- `data/` needs the uncompressed synonyms plus the tarball. It no longer needs room
  for an uncompressed copy of the backup -- that staging step is gone.

**Disk speed** is a lever, and probably the biggest one left. Both PVCs use
`storageClassName: basic`, so the index sits on network storage and the merge and
optimize phases are bounded by it -- no amount of Makefile tuning substitutes for the
volume being fast.

Moving the index onto a node-local NVMe ephemeral volume is written and ready in a
separate PR, blocked on
[issue #280](https://github.com/NCATSTranslator/NameResolution/issues/280): the
namespace cannot currently create `nvme-ephemeral` volumes. When that lands, the swap
is one block in `nameres-loading.k8s.yaml` and nothing else changes -- the stamp files
follow `SOLR_DIR` either way.

## Watching a load in Grafana

Take a look at these while a load is running; each one tells you what to change next
time.

| What to look at | What it means | What to do |
| --- | --- | --- |
| **CPU throttling** (`container_cpu_cfs_throttled_seconds_total`, or the "throttling" panel) | Sustained throttling means the pod wants more CPU than its limit. | Raise `cpu`. The loader picks it up on its own. |
| **CPU usage vs. limit** | Flat at the limit during the load = CPU-bound, and more will help. Well below it = something else is the constraint, probably disk. | If it is below the limit, look at disk before adding CPU. |
| **Memory usage (RSS/WSS)** | This is roughly the JVM. It should sit near `SOLR_MEM` and be stable. | If it is far below `SOLR_MEM`, lower `SOLR_MEM` and give the memory back as cache. |
| **Memory usage (cache)** | Page cache: the index data the kernel is holding. Rising to fill the headroom is healthy. | If it is pinned at (limit - heap) for the whole run, more memory may speed up merges. |
| **Disk read/write throughput on the Solr volume** | Flat-topped during merges and the optimize = saturated volume. Expected on `basic` storage, and the main argument for issue #280. | Nothing to tune here; it is the storage class. |
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
| `SYNONYMS_URL` | Babel 2026jul22 | Which Babel release to load. |
| `SOLR_MEM` | `31G` | Solr's heap during the load (`-Xms` and `-Xmx`). |
| `LOAD_PARALLELISM` | CPU limit | Concurrent uploads. Only set this to override the cgroup-derived default. |
| `SPLIT_SIZE` / `SPLIT_LINES` | `2G` / 10M | How the big synonym files are split. More, smaller chunks give the parallel loader a shorter tail at the end of the run; fewer, larger ones mean less `split` time up front. |
| `ramBufferSizeMB` | 2048 (in the configset) | Not an environment variable -- it lives in `solrconfig.xml`. It is a budget shared across indexing threads, so the segment size at flush is roughly this divided by the number of concurrent uploads: ~64 MB at 32 uploads. Raise it further if the Grafana disk panels show the run still merging long after the uploads have finished; there is heap to spare. |
| `SOLR_STARTUP_TRIES` | 60 | How long the loader waits for Solr (3s each) before giving up. |
| `LOAD_APPEND` | unset | Set to `1` to load into a core that already has documents. The loader refuses by default, because loading is additive and a re-run would duplicate every document. Only correct when you are deliberately adding a second, distinct set of files. |
| `BACKUP_UID` / `BACKUP_GID` | `8983` | The uid/gid stamped onto the backup's files, so the serving Solr can write to the core it restores without a `chown`. |
