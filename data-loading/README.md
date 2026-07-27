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

## The configset

Two files, ~220 lines: [`solrconfig.xml`](configsets/name_lookup/conf/solrconfig.xml)
and [`managed-schema.xml`](configsets/name_lookup/conf/managed-schema.xml). They are
written for NameRes rather than copied from Solr's `_default` configset, and that is
the point: **everything in them is a decision we made**, so there is nothing to
reconcile against upstream when Solr is upgraded, and nothing pinned that we never
chose. Solr supplies the defaults for everything we leave out, and the handlers we
do not declare (`/update`, `/update/json/docs`, `/admin/ping`, `/admin/*`) are
registered implicitly.

This replaced a wholesale copy of `_default`: 44 files, 70 field types and 69 dynamic
fields, of which NameRes used 6 field types and 13 fields. 42 of those files were
language-specific stopword and contraction lists, which could never have done
anything here -- Babel synonyms carry no language, and neither of our two field types
stems, removes stopwords or applies synonyms.

Two consequences worth knowing:

- **The schema is immutable.** `schemaFactory` declares `mutable=false`, so the Schema
  API refuses to write and this file cannot drift from a live core. (Left undeclared,
  Solr defaults to a *mutable* managed schema -- the same trap as the
  `configoverlay.json` that used to live here.) The only thing that modifies a live
  index is the Helm blocklist step, which deletes documents by query and needs no
  schema change.
- **An unexpected Babel key is now an error.** With no dynamic fields and no
  schemaless mode, a synonym file containing a key we did not declare fails the load
  with `unknown field` instead of having a field invented for it. If Babel's output
  gains a field, add it to the schema deliberately.

### Upgrading Solr

Bump the image tag and let CI do the work: it creates a core from this configset,
loads it, queries it and round-trips a backup, so anything removed or renamed upstream
turns CI red at core creation. If you want to see what changed in Solr's own defaults,
diff against the `_default` configset inside the image:

```shell
$ docker run --rm --entrypoint cat solr:9.10 \
    /opt/solr/server/solr/configsets/_default/conf/solrconfig.xml > /tmp/upstream.xml
$ diff /tmp/upstream.xml configsets/name_lookup/conf/solrconfig.xml
```

Expect that diff to be enormous and mostly uninteresting -- it is the 900 lines of
upstream commentary and examples we are deliberately not carrying. It is useful for
spotting a renamed element, not as a routine check.

`luceneMatchVersion` controls analyzer back-compatibility, not which Solr this runs
on. It does not need to track the release, and changing it can change how text is
analysed, so it should only move together with a reindex.

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
(concurrent uploads; defaults to the cgroup CPU limit, which inside a container is
the pod's limit rather than the node's core count — see
[`available-cpus.sh`](available-cpus.sh)), `SPLIT_SIZE` / `SPLIT_LINES`.

Loading is **additive**, not idempotent: every document is given a fresh UUID, so
loading the same files twice puts every document in twice. The loader therefore
refuses to load into a core that already has documents. If a load dies partway
through, do not just re-run it — delete the core and start it again:

```shell
$ make stop-solr && rm -rf "${SOLR_DIR:-/var/solr}/name_lookup" "${SOLR_DIR:-/var/solr}"/*.done
$ make all
```

(`LOAD_APPEND=1` overrides the refusal, for the genuine case of loading a second,
distinct set of files into an existing core.)

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

Solr needs to *write* to the core it serves — it takes a write lock, and refuses to
load the core if it cannot. The loading image runs as uid 1000 and the official Solr
image runs as uid 8983, so the Makefile stamps 8983 onto every file as it builds the
tarball (`BACKUP_UID` / `BACKUP_GID`) and the restore needs no `chown`. Extract as
root, or as a user that can set ownership; extracting as an unprivileged user gives
the files to whoever extracted them, and Solr will not be able to write.

A backup built before this (anything whose files come out owned by uid 1000) needs
`chown -R 8983:8983` on the extracted core.

The Solr version used to serve the backup must be **>=** the version that built
it (an older Solr cannot read a newer Lucene index). The builder is pinned exactly
(`SOLR_VERSION` in the [`Dockerfile`](Dockerfile), 9.10.1 today) because it names a
release tarball; the servers (`docker-compose.yml`, CI, the Helm chart) track the
`9.10` line, so they float forward onto patch releases and stay at or above the
builder without anyone having to remember to bump them.

## Why it is built this way

Three decisions here are easy to undo by accident, so they are worth stating:

- **Standalone, not cloud mode.** Solr used to run with `-DzkRun` everywhere.
  ZooKeeper buys nothing for a single node, and cloud mode is what forced the
  restore to recreate the collection and re-declare the schema over the Schema
  API -- which is how the schema came to be defined in two places and drift.
- **`tar` the stopped core, not the replication backup API.** Tarring the core
  directory after a clean commit is simpler, and the result is self-contained:
  config, schema and index in one artifact, so restore is "untar and start".
- **Optimize before export.** `optimize=true` before tarring merges the index to
  one segment, which makes it smaller and faster to serve (fewer files to hold
  in the OS page cache), at the cost of a one-time forced merge during the build.
