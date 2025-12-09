# This Makefile contains all the instructions necessary to
# download Babel synonym files from a web location, create
# and load them into a Solr dataset and generate a Solr backup
# that can be used to start a NameRes instance.
#

# Configuration
SYNONYMS_URL=https://stars.renci.org/var/babel_outputs/2025sep1/synonyms/

# How much memory should Solr use.
SOLR_MEM=220G

# SOLR_DIR should be set up to point to the Solr data directory (usually /var/solr)
# and SOLR_EXEC should be set up to point to the Solr executable.
# These will both be set up by the Dockerfile.

# All and clean targets.

.PHONY: all clean
all: data/setup.done
	echo Solr has now been set up and loaded with the synonym data.
	echo Run 'make start-solr-backup' to start a backup. Run 'make check-solr-backup' to check
	echo if the backup has completed. Once that has completed, run 'make data/backup.done' to
	echo generate a snapshot.backup.tar.gz file that can be used in NameRes.

clean:
	rm -rf data/*
	mkdir data

# This is a three step process.
#
# Step 1. Download an uncompress synonym files.
data/synonyms/done:
	mkdir -p data/synonyms
	wget -c -r -l1 -nd -P data/synonyms ${SYNONYMS_URL}
	gunzip data/synonyms/*.txt.gz
	echo Downloaded synonyms from ${SYNONYMS_URL}
	# split -d -l 10000000 data/synonyms/SmallMolecule.txt data/synonyms/SmallMolecule.txt. && rm data/synonyms/SmallMolecule.txt
	split -d -l 10000000 data/synonyms/DrugChemicalConflated.txt data/synonyms/DrugChemicalConflated.txt. && rm data/synonyms/DrugChemicalConflated.txt
	split -d -l 10000000 data/synonyms/GeneProteinConflated.txt data/synonyms/GeneProteinConflated.txt. && rm data/synonyms/GeneProteinConflated.txt
	echo Split DrugChemicalConflated.txt and GeneProteinConflated.txt, and deleted the original files.
	touch $@

# Step 3. Start Solr server.
data/solr.pid:
	mkdir -p ${SOLR_DIR}/logs
	${SOLR_EXEC} -cloud -p 8983 -v -m ${SOLR_MEM} -s ${SOLR_DIR} >> ${SOLR_DIR}/logs/solr.txt 2>> ${SOLR_DIR}/logs/solr.err.txt
	while [ ! -s $@ ]; do \
		${SOLR_EXEC} status | grep -Po 'Solr process \K([0-9]+)' > $@; \
	done
	$(info Solr started with PID file at $@)
	cat $@

# Step 4. Load JSON files into Solr server.
data/setup.done: data/synonyms/done data/solr.pid
	mkdir -p data/logs
	bash setup-and-load-solr.sh "data/synonyms/*.txt*" >> data/logs/setup-and-load-solr.sh.log 2>> data/logs/setup-and-load-solr.sh.err.log && touch $@

# Step 5. Start a Solr backup.
.PHONY: start-solr-backup
start-solr-backup: data/setup.done
	curl 'http://localhost:8983/solr/name_lookup/replication?command=backup&name=backup'

# Step 6. Wait for the backup to complete.
.PHONY: check-solr-backup
check-solr-backup:
	curl 'http://localhost:8983/solr/name_lookup/replication?command=details'

# Step 6. Shutdown the Solr instance.
### data/stop-solr:
###    	docker exec name_lookup solr stop -p 8983 -verbose

# Step 7. Generate the backup tarball.
data/backup.done:
	mkdir -p data/var/solr/data
	mv /var/solr/name_lookup_shard1_replica_n1/data/snapshot.backup data/var/solr/data
	cd data && tar zcvf snapshot.backup.tar.gz var && touch backup.done

.PHONY: stop-solr
stop-solr:
	rm data/solr.pid
	${SOLR_EXEC} stop
	$(info Solr stopped.)
