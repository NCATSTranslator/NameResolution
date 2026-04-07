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

# We don't use set -e because the loop test relies on failures being ignored.
set -uo pipefail

# Configuration options
SOLR_SERVER="http://localhost:8983"
SLEEP_INTERVAL=60

# Please don't change these values unless you change NameRes appropriately!
COLLECTION_NAME="name_lookup"
BACKUP_NAME="backup"

# Step 0. Make sure the Solr data directory looks like it contains the uncompressed backup.
if [ ! -d "./data/solr/var" ]; then
  echo 'WARNING: No ./data/solr/var directory found; are you sure you uncompressed the NameRes backup into the Solr data directory?' >&2
fi

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
echo Solr database has been set up.

# Step 3. Restore the data
CORE_NAME="${COLLECTION_NAME}_shard1_replica_n1"
echo "Starting Solr restore on core ${CORE_NAME}, with status at ${SOLR_SERVER}/solr/${CORE_NAME}/replication?command=restorestatus"
RESTORE_URL="${SOLR_SERVER}/solr/${CORE_NAME}/replication?command=restore&location=/var/solr/data/var/solr/data/&name=${BACKUP_NAME}"
wget -O - "$RESTORE_URL"
sleep "$SLEEP_INTERVAL"
RESTORE_STATUS_URL="${SOLR_SERVER}/solr/${CORE_NAME}/replication?command=restorestatus"
RESTORE_STATUS=$(wget -q -O - "$RESTORE_STATUS_URL" 2>&1 | grep "success")
RESTORE_STATUS=""
until [ -n "$RESTORE_STATUS" ] ; do
  echo "Solr restore in progress. If this takes longer than 30 minutes, please visit ${SOLR_SERVER} with your browser to check Solr."
  RESTORE_STATUS=$(wget -q -O - "$RESTORE_STATUS_URL" 2>&1 | grep "success")
  sleep "$SLEEP_INTERVAL"
done
echo "Solr restore complete!"

echo "Solr contents:"
curl -s --negotiate -u: "$SOLR_SERVER/solr/name_lookup/query?q=*:*&rows=0"
