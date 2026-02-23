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
	echo Run 'make data/backup.done' to stop Solr and create the solr-data.tar.gz backup.
	echo Copy data/solr-data.tar.gz to a web server for NameRes deployment.

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

# Step 3. Start Solr server (standalone mode, no ZooKeeper).
data/solr.pid:
	mkdir -p ${SOLR_DIR}/logs
	${SOLR_EXEC} -p 8983 -v -m ${SOLR_MEM} -s ${SOLR_DIR} >> ${SOLR_DIR}/logs/solr.txt 2>> ${SOLR_DIR}/logs/solr.err.txt
	while [ ! -s $@ ]; do \
		${SOLR_EXEC} status | grep -Po 'Solr process \K([0-9]+)' > $@; \
	done
	$(info Solr started with PID file at $@)
	cat $@

# Step 4. Load JSON files into Solr server.
data/setup.done: data/synonyms/done data/solr.pid
	mkdir -p data/logs
	bash setup-and-load-solr.sh "data/synonyms/*.txt*" >> data/logs/setup-and-load-solr.sh.log 2>> data/logs/setup-and-load-solr.sh.err.log && touch $@

# Step 5. Create backup: stop Solr cleanly, install read-only config, tar core directory.
# The tarball contains name_lookup/ with conf/ and data/index/ (no tlog).
# To restore: extract into ./data/solr/ and run docker compose up.
data/backup.done: data/setup.done
	$(info Stopping Solr before backup...)
	${SOLR_EXEC} stop
	rm -f data/solr.pid
	$(info Installing read-only Solr config for deployment...)
	cp conf/solrconfig.xml ${SOLR_DIR}/name_lookup/conf/solrconfig.xml
	$(info Creating backup tarball at data/solr-data.tar.gz ...)
	cd ${SOLR_DIR} && tar zcvf $(CURDIR)/data/solr-data.tar.gz --exclude='name_lookup/data/tlog' name_lookup
	touch data/backup.done
	$(info Backup complete. Copy data/solr-data.tar.gz to a web server for deployment.)

.PHONY: stop-solr
stop-solr:
	rm -f data/solr.pid
	${SOLR_EXEC} stop
	$(info Solr stopped.)
