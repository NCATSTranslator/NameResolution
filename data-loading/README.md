# Loading NameResolution data

NameResolution answers queries out of an [Apache Solr](https://solr.apache.org/) index.
This directory builds that index from the synonym files produced by
[Babel](https://github.com/NCATSTranslator/Babel) and packages it as a
**self-contained Solr backup** that a NameRes instance can download and serve directly.

"Self-contained" is the important part: the backup is a whole Solr *core* --
its configuration, its schema **and** its built index -- tarred up together.
Restoring it is just "untar into the Solr home and start Solr". There is no
ZooKeeper, no collection to create, and no schema to re-apply at restore time.
The schema lives in exactly one place, the checked-in configset at
[`configsets/name_lookup/conf`](configsets/name_lookup/conf).

## How it works

Solr runs in **standalone** mode (a single node, one core -- ZooKeeper adds
nothing for a one-shard job). The core is created from the checked-in configset
with `solr create -c name_lookup -d configsets/name_lookup`. Data is loaded by
streaming each Babel synonym file to Solr **in parallel**, with a single commit
at the end; there is no per-file commit and no sleeping. Finally the index is
optimized and the core directory is tarred into `snapshot.backup.tar.gz`.

Because a parallel bulk load of a ~130 GB dataset would be painful to debug if
it silently dropped documents, `setup-and-load-solr.sh` **counts the input
documents before loading and compares against Solr's document count afterward**,
and every upload uses `curl --fail`. A failed upload or a count mismatch aborts
with a non-zero exit code.

## Using the Makefile

The Makefile runs the whole pipeline. It expects `SOLR_DIR` (the Solr home,
usually `/var/solr`) and `SOLR_EXEC` (the `bin/solr` executable) to be set -- the
[`Dockerfile`](Dockerfile) sets both.

1. Edit `SYNONYMS_URL` in the Makefile to point at the Babel synonyms directory
   you want to load.
2. Run `make all`. This downloads and uncompresses the synonym files, splits any
   file larger than `SPLIT_SIZE` (so the parallel loader has more units of work),
   starts Solr, creates the `name_lookup` core from the configset, and loads the
   data with the document-count guard.
3. (Optional) Query the Solr server at <http://localhost:8983/> to confirm the
   data looks right.
4. Run `make data/backup.done`. This optimizes the index, shuts down Solr and
   writes `data/snapshot.backup.tar.gz`.
5. Publish `snapshot.backup.tar.gz` to a publicly-accessible URL and point a
   NameRes instance at it (see below).

Tuning knobs: `SOLR_MEM` (Solr heap for the load), `LOAD_PARALLELISM`
(concurrent uploads; defaults to the number of CPUs), `SPLIT_SIZE` / `SPLIT_LINES`.

## Restoring the backup

The backup extracts to a ready-to-serve `name_lookup/` core, so restoring it is
trivial in every environment:

- **Locally (docker-compose):** extract the tarball into the Solr data directory
  (`./data/solr` by default) so you get `./data/solr/name_lookup/`, then
  `docker-compose up`. Solr auto-discovers the core on startup.
- **In Kubernetes (Helm):** the [`name-lookup` chart](../data/name-lookup) does
  this automatically -- an init container downloads and extracts the backup into
  the Solr volume, and the Solr StatefulSet auto-discovers the core. The restore
  Job no longer creates a collection or re-applies the schema; its only remaining
  job is deleting blocklisted CURIEs.

The Solr version used to serve the backup must be **>=** the version that built
it (an older Solr cannot read a newer Lucene index). Both are 9.10.x today.

## Options considered

We looked at three approaches before settling on the current one.

### A. Status quo (cloud mode, replication backup, schema re-applied on restore)
Solr ran in cloud mode (`-DzkRun`) everywhere. The backup contained only the
Lucene index, so the restore code had to recreate the collection and re-declare
every field/type/copy-field over the Schema API. **Rejected:** ZooKeeper is
overhead for one node; the schema was defined twice (loader *and* restore) and
had already drifted; the backup was not self-contained; and the load committed
after every file with a fixed `sleep` between files.

### B. PR [#249](https://github.com/NCATSTranslator/NameResolution/pull/249) (standalone + config in the backup)
Switched to standalone mode and started shipping `solrconfig.xml` /
`managed-schema.xml` in the backup. A real improvement, but it still created the
core through the API and did not tackle load speed. **Superseded** by C.

### C. Chosen: standalone + checked-in configset + self-contained core backup + parallel load
The backup is the whole core (config + schema + index), so restore is just
"untar and start". The schema has a single source of truth (the checked-in
configset). The load is parallel with a single deferred commit, guarded by a
document count. This is the most maintainable *and* the fastest option.

Sub-choices within C:

- **Parallel load vs. cheap wins only.** We parallelize (the real speed lever)
  and protect against dropped/corrupt data with the pre/post document-count
  check and `curl --fail`, rather than only removing the per-file commit.
- **`tar` the stopped core vs. the replication backup API.** We `tar` the core
  directory after a clean commit -- simpler, and the result is self-contained.
- **Optimize before export.** We run `optimize=true` before tarring: one segment
  makes the index smaller and faster to serve (fewer files to keep in the OS
  page cache), at the cost of a one-time forced merge during the build.

## Issues addressed

- Closes [#238](https://github.com/NCATSTranslator/NameResolution/issues/238) and
  [#185](https://github.com/NCATSTranslator/NameResolution/issues/185): the backup
  now includes the config and schema, so restore no longer recreates fields.
- Closes [#256](https://github.com/NCATSTranslator/NameResolution/issues/256):
  the index is optimized before the snapshot is exported.
- Closes [#266](https://github.com/NCATSTranslator/NameResolution/issues/266):
  `queryResultCache` is sized in the checked-in `solrconfig.xml`.
- Addresses [translator-devops#609](https://github.com/helxplatform/translator-devops/issues/609):
  the download init container is idempotent, so a pod restart no longer wipes a
  good core and leaves Solr empty.
- Makes progress on [#265](https://github.com/NCATSTranslator/NameResolution/issues/265):
  the Helm chart's Solr heap is lowered to leave room for the OS page cache, and
  optimizing shrinks the index. Final heap/memory sizing and the GC-flag rework
  ([#272](https://github.com/NCATSTranslator/NameResolution/issues/272)) still
  need to be validated under query load and are tracked separately.
