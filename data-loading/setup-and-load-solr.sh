#!/usr/bin/env bash

SOLR_SERVER="http://localhost:8983"

# Step 1. Make sure the Solr service is up and running.
HEALTH_ENDPOINT="${SOLR_SERVER}/solr/admin/cores?action=STATUS"
response=$(wget --spider --server-response ${HEALTH_ENDPOINT} 2>&1 | grep "HTTP/" | awk '{ print $2 }') >&2
until [ "$response" = "200" ]; do
  response=$(wget --spider --server-response ${HEALTH_ENDPOINT} 2>&1 | grep "HTTP/" | awk '{ print $2 }') >&2
  echo "  -- SOLR is unavailable - sleeping"
  sleep 3
done
echo "SOLR is up and running at ${SOLR_SERVER}."

# Step 2. Create fields for search.
source "setup_solr.sh"

# Step 3. Load specified files.
for f in $1; do
	echo "Loading $f..."
	# curl -d @$f needs to load the entire file into memory before uploading it, whereas
	# curl -X POST -T $f will stream it. See https://github.com/TranslatorSRI/NameResolution/issues/194
	curl -H 'Content-Type: application/json' -X POST -T $f \
	    "$SOLR_SERVER/solr/name_lookup/update/json/docs?processor=uuid&uuid.fieldName=id&commit=true"
	sleep 30
done
echo "Check solr"
curl -s --negotiate -u: "$SOLR_SERVER/solr/name_lookup/query?q=*:*&rows=0"

