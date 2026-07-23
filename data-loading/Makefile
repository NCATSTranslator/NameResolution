# This Makefile contains all the instructions necessary to
# download Babel synonym files from a web location, load them into a
# standalone Solr core (using the checked-in configset under
# configsets/name_lookup) and generate a self-contained Solr backup that can be
# used to start a NameRes instance.
#
# The backup is the whole Solr core -- config, schema AND index -- so restoring
# it is just "untar into the Solr home and start Solr" (see ../data-loading/README.md).

# Recipes below use pipefail, which /bin/sh (dash on Debian) does not support.
SHELL := /bin/bash

# Configuration. These use ?= so that they can be overridden from the environment
# (e.g. from a Kubernetes pod spec) without editing this file.
SYNONYMS_URL ?= https://stars.renci.org/var/babel_outputs/2026jul22/synonyms/

# Solr's heap during the load. `solr -m` sets BOTH -Xms and -Xmx, so this is
# committed, not just a ceiling.
#
# Indexing does not want a big heap: Lucene buffers documents in ramBufferSizeMB
# (2 GB, set in the configset) and streams merges through the OS page cache, so
# heap beyond a few GB is memory taken away from that cache. Staying under 32G also
# keeps compressed object pointers, which a larger heap silently gives up.
# See data-loading/kubernetes/README.md for how to check this against Grafana.
SOLR_MEM ?= 31G

# How many CPUs we may actually use -- the cgroup limit in a container, not the
# node's core count. Used for the parallel compress below; setup-and-load-solr.sh
# works this out for itself.
NCPU := $(shell bash ./available-cpus.sh 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)

# A 130 GB Lucene index is already largely compressed, and gzip is single-threaded,
# so compressing the backup with gzip takes hours for a few percent. Use pigz
# (parallel gzip, same format) when it is available. -p keeps pigz inside our CPU
# quota; left alone it starts one thread per *node* core and spends the difference
# being throttled.
GZIP_CMD := $(shell command -v pigz > /dev/null 2>&1 && echo "pigz -p $(NCPU)" || echo gzip)

# The checked-in configset used to create the core (contains conf/).
# Absolute, and it has to be: `solr create -d` resolves a *relative* path against
# Solr's own server/solr/configsets directory, not the working directory, so a
# relative path here fails with "Can't find resource 'solrconfig.xml'".
CONFIGSET ?= $(CURDIR)/configsets/name_lookup

# The Solr core name. Do not change without also changing NameRes and the configset.
CORE=name_lookup

# The uid/gid the backup's files are owned by. This is `solr` in the official Solr
# image, which is what serves the backup everywhere: docker-compose, the Helm chart
# and CI. The loading image runs as `nru` (1000) instead, so without this the
# restored core is owned by a user that does not exist on the serving side and Solr
# cannot take its write lock -- a core that fails to load with a permissions error at
# the end of a multi-hour restore. Stamping the ownership at tar time fixes it for
# every restore path at once, rather than asking each one to remember a chown.
BACKUP_UID ?= 8983
BACKUP_GID ?= 8983

# Split any synonym file larger than SPLIT_SIZE into chunks of SPLIT_LINES lines.
# Splitting only helps parallelism, so we only bother with the big files; JSON is
# one document per line, so we must split on line boundaries, never on bytes.
SPLIT_SIZE ?= 2G
SPLIT_LINES ?= 10000000

# SOLR_DIR should be set up to point to the Solr data directory (usually /var/solr,
# which is also the Solr home) and SOLR_EXEC should be set up to point to the Solr
# executable. These will both be set up by the Dockerfile.
SOLR_DIR ?= /var/solr

# Stamp files live on the volume whose state they describe. These three describe
# the Solr volume -- a core exists, it has been loaded, a server is running -- and
# on Kubernetes that volume may be ephemeral NVMe (see kubernetes/README.md), so
# they have to disappear along with it. Keeping them in data/, which is a separate
# and persistent volume, would mean a recreated pod found an empty index and a set
# of stamps swearing it had been loaded.
SOLR_STARTED = ${SOLR_DIR}/solr.started
CORE_DONE = ${SOLR_DIR}/core.done
SETUP_DONE = ${SOLR_DIR}/setup.done

# Logs go to data/, which outlives the pod: they are most useful precisely when the
# run has died and the Solr volume has gone with it.
LOG_DIR = ${CURDIR}/data/logs

# All and clean targets.

.PHONY: all clean
all: ${SETUP_DONE}
	echo Solr has now been set up and loaded with the synonym data.
	echo Run 'make data/backup.done' to optimize the index, shut down Solr and
	echo generate a snapshot.backup.tar.gz file that can be used in NameRes.

# Leaves the index itself alone -- to start over from an empty index, delete
# ${SOLR_DIR}/${CORE} (or, on Kubernetes, just recreate the pod).
clean:
	rm -rf data/*
	mkdir -p data
	rm -f ${SOLR_STARTED} ${CORE_DONE} ${SETUP_DONE}

# Step 1. Download and uncompress synonym files, then split the large ones so the
# parallel loader has more units of work.
data/synonyms/done:
	mkdir -p data/synonyms
	# -A restricts what is saved to the synonym files themselves. Everything in
	# data/synonyms is loaded into Solr (the glob in step 4 is *.txt*), so anything
	# else wget leaves behind -- a robots.txt, most obviously -- becomes a file the
	# loader tries to index. Solr answers 200 and indexes nothing for it, so the
	# document-count guard catches it, but only at the end of a multi-hour load.
	wget -c -r -l1 -nd -A '*.txt,*.txt.gz' -P data/synonyms ${SYNONYMS_URL}
	# -exec (rather than a glob) so that a rerun where everything is already
	# uncompressed is a no-op instead of a "no such file" failure.
	find data/synonyms -maxdepth 1 -type f -name '*.txt.gz' -exec gunzip {} +
	echo Downloaded synonyms from ${SYNONYMS_URL}
	# pipefail plus `|| exit 1` so that a failed split fails the target. Without both,
	# the loop's status is whichever iteration ran last and a file that failed to
	# split halfway through the set is silently loaded whole (or not at all).
	set -o pipefail; find data/synonyms -maxdepth 1 -type f -name '*.txt' -size +${SPLIT_SIZE} -print | while read -r f; do \
		echo "Splitting large file $$f (larger than ${SPLIT_SIZE})..."; \
		split -d -l ${SPLIT_LINES} "$$f" "$$f." && rm "$$f" || exit 1; \
	done
	touch $@

# Step 2. Start the Solr server (standalone -- no ZooKeeper needed for one node).
# --user-managed keeps standalone mode explicit (Solr 10 will default to cloud).
${SOLR_STARTED}:
	mkdir -p ${LOG_DIR}
	SOLR_LOGS_DIR=${LOG_DIR} ${SOLR_EXEC} start -p 8983 --user-managed -v -m ${SOLR_MEM} -s ${SOLR_DIR} >> ${LOG_DIR}/solr.txt 2>> ${LOG_DIR}/solr.err.txt
	# Wait for Solr to actually answer, then stamp. This used to parse `solr status`
	# for a PID, which reports "No Solr nodes are running" against a perfectly healthy
	# Solr in a container -- the stock solr image does it too -- so the loop never
	# terminated. Nothing ever used the PID (stop-solr stops Solr by port), so asking
	# the admin endpoint whether Solr is up is both simpler and the actual question.
	# Bounded, so a Solr that fails to start stops the build instead of hanging it.
	@tries=0; until curl -sf 'http://localhost:8983/solr/admin/cores?action=STATUS' > /dev/null 2>&1; do \
		tries=$$((tries + 1)); \
		if [ $$tries -gt 60 ]; then \
			echo "Solr did not start after two minutes; see ${LOG_DIR}/solr.err.txt" >&2; \
			exit 1; \
		fi; \
		sleep 2; \
	done
	@date -u '+Solr started at %Y-%m-%dT%H:%M:%SZ on port 8983' > $@
	@cat $@

# Step 3. Create the core from the checked-in configset (schema lives there).
# solr.pid is order-only (after the |): the core needs a running Solr, but restarting
# Solr must not make the core look stale -- re-running `solr create` on a core that
# already exists is an error.
${CORE_DONE}: | ${SOLR_STARTED}
	${SOLR_EXEC} create -c ${CORE} -d ${CONFIGSET}
	touch $@

# Step 4. Load the synonym files into the core (parallel; with a doc-count guard).
${SETUP_DONE}: data/synonyms/done ${CORE_DONE}
	mkdir -p ${LOG_DIR}
	bash setup-and-load-solr.sh "data/synonyms/*.txt*" >> ${LOG_DIR}/setup-and-load-solr.sh.log 2>> ${LOG_DIR}/setup-and-load-solr.sh.err.log && touch $@

# Step 5. Optimize the index, shut down Solr, and tar the whole core into a
# self-contained backup. Extracting snapshot.backup.tar.gz into the Solr home
# yields a ready-to-serve name_lookup/ core -- no restore-time schema setup.
# The PID file is an order-only prerequisite because optimizing needs a *running*
# Solr: the stamps survive a container restart, but the server does not, so a
# restarted container should start Solr again rather than fail here.
data/backup.done: ${SETUP_DONE} | ${SOLR_STARTED}
	@echo "Optimizing the index before export (this can take a while on a large index)..."
	curl -sf --show-error 'http://localhost:8983/solr/${CORE}/update?optimize=true'
	# The optimize above commits, so the on-disk index is consistent. `solr stop`
	# can exit non-zero under Docker even on a clean stop (SOLR-16463), so ignore it.
	${SOLR_EXEC} stop -p 8983 || true
	rm -f ${SOLR_STARTED}
	# --owner/--group stamp the serving Solr's uid onto every member (see BACKUP_UID
	# above); --numeric-owner makes the restoring tar use those numbers rather than
	# looking up whatever "solr" means on the restoring host.
	#
	# tlog is left out: the optimize above committed, so there is nothing uncommitted
	# to replay, and Solr recreates the directory on startup. It is dead weight in a
	# tarball this size.
	set -o pipefail; tar -C ${SOLR_DIR} \
		--numeric-owner --owner=${BACKUP_UID} --group=${BACKUP_GID} \
		--exclude='${CORE}/data/tlog' \
		-cf - ${CORE} | ${GZIP_CMD} > data/snapshot.backup.tar.gz
	touch $@

.PHONY: stop-solr
stop-solr:
	rm -f ${SOLR_STARTED}
	${SOLR_EXEC} stop -p 8983 || true
	$(info Solr stopped.)
