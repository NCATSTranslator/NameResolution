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
set -euo pipefail

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

# Step 2. Create fields for search.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../data-loading/setup_solr.sh"

# Step 3. Restore the data
CORE_NAME="${COLLECTION_NAME}_shard1_replica_n1"
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
