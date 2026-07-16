# This Makefile contains all the instructions necessary to
# download Babel synonym files from a web location, load them into a
# standalone Solr core (using the checked-in configset under
# configsets/name_lookup) and generate a self-contained Solr backup that can be
# used to start a NameRes instance.
#
# The backup is the whole Solr core -- config, schema AND index -- so restoring
# it is just "untar into the Solr home and start Solr" (see ../data-loading/README.md).

# Configuration
SYNONYMS_URL=https://stars.renci.org/var/babel_outputs/2025sep1/synonyms/

# How much memory should Solr use.
SOLR_MEM=220G

# The checked-in configset used to create the core (contains conf/).
CONFIGSET=configsets/name_lookup

# The Solr core name. Do not change without also changing NameRes and the configset.
CORE=name_lookup

# Split any synonym file larger than SPLIT_SIZE into chunks of SPLIT_LINES lines.
# Splitting only helps parallelism, so we only bother with the big files; JSON is
# one document per line, so we must split on line boundaries, never on bytes.
SPLIT_SIZE=2G
SPLIT_LINES=10000000

# SOLR_DIR should be set up to point to the Solr data directory (usually /var/solr,
# which is also the Solr home) and SOLR_EXEC should be set up to point to the Solr
# executable. These will both be set up by the Dockerfile.

# All and clean targets.

.PHONY: all clean
all: data/setup.done
	echo Solr has now been set up and loaded with the synonym data.
	echo Run 'make data/backup.done' to optimize the index, shut down Solr and
	echo generate a snapshot.backup.tar.gz file that can be used in NameRes.

clean:
	rm -rf data/*
	mkdir data

# Step 1. Download and uncompress synonym files, then split the large ones so the
# parallel loader has more units of work.
data/synonyms/done:
	mkdir -p data/synonyms
	wget -c -r -l1 -nd -P data/synonyms ${SYNONYMS_URL}
	gunzip data/synonyms/*.txt.gz
	echo Downloaded synonyms from ${SYNONYMS_URL}
	find data/synonyms -maxdepth 1 -type f -name '*.txt' -size +${SPLIT_SIZE} -print | while read -r f; do \
		echo "Splitting large file $$f (larger than ${SPLIT_SIZE})..."; \
		split -d -l ${SPLIT_LINES} "$$f" "$$f." && rm "$$f"; \
	done
	touch $@

# Step 2. Start the Solr server (standalone -- no ZooKeeper needed for one node).
# --user-managed keeps standalone mode explicit (Solr 10 will default to cloud).
data/solr.pid:
	mkdir -p ${SOLR_DIR}/logs
	${SOLR_EXEC} start -p 8983 --user-managed -v -m ${SOLR_MEM} -s ${SOLR_DIR} >> ${SOLR_DIR}/logs/solr.txt 2>> ${SOLR_DIR}/logs/solr.err.txt
	while [ ! -s $@ ]; do \
		${SOLR_EXEC} status | grep -Po 'Solr process \K([0-9]+)' > $@; \
	done
	$(info Solr started with PID file at $@)
	cat $@

# Step 3. Create the core from the checked-in configset (schema lives there).
data/core.done: data/solr.pid
	${SOLR_EXEC} create -c ${CORE} -d ${CONFIGSET}
	touch $@

# Step 4. Load the synonym files into the core (parallel; with a doc-count guard).
data/setup.done: data/synonyms/done data/core.done
	mkdir -p data/logs
	bash setup-and-load-solr.sh "data/synonyms/*.txt*" >> data/logs/setup-and-load-solr.sh.log 2>> data/logs/setup-and-load-solr.sh.err.log && touch $@

# Step 5. Optimize the index, shut down Solr, and tar the whole core into a
# self-contained backup. Extracting snapshot.backup.tar.gz into the Solr home
# yields a ready-to-serve name_lookup/ core -- no restore-time schema setup.
data/backup.done: data/setup.done
	@echo "Optimizing the index before export (this can take a while on a large index)..."
	curl -sf --show-error 'http://localhost:8983/solr/${CORE}/update?optimize=true'
	# The optimize above commits, so the on-disk index is consistent. `solr stop`
	# can exit non-zero under Docker even on a clean stop (SOLR-16463), so ignore it.
	${SOLR_EXEC} stop -p 8983 || true
	rm -f data/solr.pid
	tar -C ${SOLR_DIR} -czvf data/snapshot.backup.tar.gz ${CORE}
	touch $@

.PHONY: stop-solr
stop-solr:
	rm -f data/solr.pid
	${SOLR_EXEC} stop -p 8983 || true
	$(info Solr stopped.)
