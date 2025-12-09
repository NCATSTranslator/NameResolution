#!/usr/bin/env bash
#
# restore.sh
#
# Restores a Solr backup located in the Solr data directory (`$SOLR_DATA/var/solr/data/snapshot.backup`).
#
# To do this, it must:
# - Initiate the restore.
# - Wait until the restore has completed.
# - Create the necessary fields (hopefully we can make this unnecessary, see https://github.com/TranslatorSRI/NameResolution/issues/185)
#
# This script should only require the `wget` program.
#
# TODO: This script does not currently implement any Blocklists.
set -xa

# Configuration options
SOLR_SERVER="http://localhost:8983"

# Please don't change these values unless you change NameRes appropriately!
COLLECTION_NAME="name_lookup"
BACKUP_NAME="backup"

# Step 1. Make sure the Solr service is up and running.
HEALTH_ENDPOINT="${SOLR_SERVER}/solr/admin/cores?action=STATUS"
response=$(wget --spider --server-response ${HEALTH_ENDPOINT} 2>&1 | grep "HTTP/" | awk '{ print $2 }') >&2
until [ "$response" = "200" ]; do
  response=$(wget --spider --server-response ${HEALTH_ENDPOINT} 2>&1 | grep "HTTP/" | awk '{ print $2 }') >&2
  echo "  -- SOLR is unavailable - sleeping"
  sleep 3
done
echo "SOLR is up and running at ${SOLR_SERVER}."

# Step 2. Create the COLLECTION_NAME if it doesn't exist.

EXISTS=$(wget -O - ${SOLR_SERVER}/solr/admin/collections?action=LIST | grep ${COLLECTION_NAME})

# create collection / shard if it doesn't exist.
if [ -z "$EXISTS" ]
then
  wget -O- ${SOLR_SERVER}/solr/admin/collections?action=CREATE'&'name=${COLLECTION_NAME}'&'numShards=1'&'replicationFactor=1
  sleep 3
fi

# Step 3. Begin restoring the data.

# Setup fields for search
wget --post-data '{"set-user-property": {"update.autoCreateFields": "false"}}' \
    --header='Content-Type:application/json' \
    -O- ${SOLR_SERVER}/solr/${COLLECTION_NAME}/config
sleep 1

# Restore data
CORE_NAME=${COLLECTION_NAME}_shard1_replica_n1
RESTORE_URL="${SOLR_SERVER}/solr/${CORE_NAME}/replication?command=restore&location=/var/solr/data/var/solr/data/&name=${BACKUP_NAME}"
wget -O - "$RESTORE_URL"
sleep 10
RESTORE_STATUS=$(wget -q -O - ${SOLR_SERVER}/solr/${CORE_NAME}/replication?command=restorestatus 2>&1 | grep "success") >&2
echo "Restore status: ${RESTORE_STATUS}"
until [ ! -z "$RESTORE_STATUS" ] ; do
  echo "Solr restore in progress. Note: if this takes too long please check solr health."
  RESTORE_STATUS=$(wget -O - ${SOLR_SERVER}/solr/${CORE_NAME}/replication?command=restorestatus 2>&1 | grep "success") >&2
  sleep 10
done
echo "Solr restore complete"

# Step 4. Create fields for search.
# (It might be possible to do this before the restore, but I'm going to follow the existing code for now.)
wget --post-data '{
    "add-field-type" : {
        "name": "LowerTextField",
        "class": "solr.TextField",
        "positionIncrementGap": "100",
        "analyzer": {
            "tokenizer": {
                "class": "solr.StandardTokenizerFactory"
            },
            "filters": [{
                "class": "solr.LowerCaseFilterFactory"
            }]
        }
    }}' \
    --header='Content-Type:application/json' \
    -O- ${SOLR_SERVER}/solr/${COLLECTION_NAME}/schema
sleep 1
# exactish type taken from https://stackoverflow.com/a/29105025/27310
wget --post-data '{
    "add-field-type" : {
        "name": "exactish",
        "class": "solr.TextField",
        "analyzer": {
            "tokenizer": {
                "class": "solr.KeywordTokenizerFactory"
            },
            "filters": [{
                "class": "solr.LowerCaseFilterFactory"
            }]
        }
    }}' \
    --header='Content-Type:application/json' \
    -O- ${SOLR_SERVER}/solr/${COLLECTION_NAME}/schema
sleep 1
wget --post-data '{
    "add-field": [
        {
            "name":"names",
            "type":"LowerTextField",
            "stored": true,
            "multiValued": true
        },
        {
            "name":"names_exactish",
            "type":"exactish",
            "indexed":true,
            "stored":true,
            "multiValued":true
        },
        {
            "name":"curie",
            "type":"string",
            "stored":true
        },
        {
            "name": "preferred_name",
            "type": "LowerTextField",
            "stored": true
        },
        {
            "name": "preferred_name_exactish",
            "type": "exactish",
            "indexed": true,
            "stored": false,
            "multiValued": false
        },
        {
            "name": "types",
            "type": "string",
            "stored": true,
            "multiValued": true
        },
        {
            "name": "shortest_name_length",
            "type": "pint",
            "stored": true
        },
        {
            "name": "curie_suffix",
            "type": "plong",
            "docValues": true,
            "stored": true,
            "required": false,
            "sortMissingLast": true
        },
        {
            "name": "taxa",
            "type": "string",
            "stored": true,
            "multiValued": true
        },
        {
            "name": "clique_identifier_count",
            "type": "pint",
            "stored": true
        }
    ]
  }' \
  --header='Content-Type:application/json' \
  -O- ${SOLR_SERVER}/solr/${COLLECTION_NAME}/schema
sleep 1
wget --post-data '{
    "add-copy-field" : {
      "source": "names",
      "dest": "names_exactish"
    }}' \
    --header='Content-Type:application/json' \
    -O- ${SOLR_SERVER}/solr/${COLLECTION_NAME}/schema
wget --post-data '{
    "add-copy-field" : {
      "source": "preferred_name",
      "dest": "preferred_name_exactish"
    }}' \
    --header='Content-Type:application/json' \
    -O- ${SOLR_SERVER}/solr/${COLLECTION_NAME}/schema
sleep 1

echo "Solr restore complete!"
