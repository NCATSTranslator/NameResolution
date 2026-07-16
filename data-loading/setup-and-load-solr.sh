#!/usr/bin/env bash
#
# Load Babel synonym files (JSON, one document per line) into a Solr core that
# already exists. The core is created from the checked-in configset with
# `solr create -c name_lookup -d configsets/name_lookup` (see the Makefile and
# the CI workflow) -- this script does NOT create it, so the schema lives in
# exactly one place: data-loading/configsets/name_lookup/conf.
#
# Speed: files are streamed to Solr in parallel with commit=false, and a single
# commit is issued at the end. There is no per-file commit and no sleeping.
#
# Safety: because the load is parallel and the data is huge, we count the input
# documents (lines) before loading and compare against Solr's document count
# afterwards. Any failed upload (curl --fail) or a count mismatch aborts with a
# non-zero exit code, so a partial/dropped load cannot pass silently.

set -uo pipefail

# Configuration (overridable from the environment).
SOLR_SERVER="${SOLR_SERVER:-http://localhost:8983}"
CORE="${SOLR_CORE:-name_lookup}"
PARALLELISM="${LOAD_PARALLELISM:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"

GLOB="${1:?usage: setup-and-load-solr.sh \"data/synonyms/*.txt*\" (quote the glob!)}"

# Step 1. Wait for the core to be available.
until curl -sf "${SOLR_SERVER}/solr/${CORE}/admin/ping" >/dev/null 2>&1; do
  echo "  -- Solr core '${CORE}' is unavailable - sleeping"
  sleep 3
done
echo "Solr core '${CORE}' is up at ${SOLR_SERVER}."

# Step 2. Expand the glob and count the input documents (one JSON doc per line).
# `grep -c ''` counts every line including a final one with no trailing newline,
# and we count per file so a missing newline never merges two files' records.
shopt -s nullglob
files=( $GLOB )
if [ ${#files[@]} -eq 0 ]; then
  echo "No files matched '${GLOB}'." >&2
  exit 1
fi
echo "Counting documents in ${#files[@]} file(s)..."
expected=0
for f in "${files[@]}"; do
  n=$(grep -c '' "$f")
  echo "  ${n} docs in ${f}"
  expected=$((expected + n))
done
echo "Expecting ${expected} documents in total."

# Step 3. Load the files in parallel, streaming each one, without committing.
# curl -T streams the file rather than buffering it in memory (issue #194), and
# --fail turns any HTTP >=400 (e.g. malformed JSON) into a non-zero exit.
load_one() {
  local f="$1"
  echo "Loading ${f}..."
  curl -sf --show-error -H 'Content-Type: application/json' -X POST -T "$f" \
    "${SOLR_SERVER}/solr/${CORE}/update/json/docs?processor=uuid&uuid.fieldName=id&commit=false" \
    || { echo "FAILED to load ${f}" >&2; return 1; }
}
export -f load_one
export SOLR_SERVER CORE

printf '%s\0' "${files[@]}" \
  | xargs -0 -P "$PARALLELISM" -I{} bash -c 'load_one "$@"' _ {}
load_rc=$?
if [ "$load_rc" -ne 0 ]; then
  echo "One or more files failed to load (xargs exit ${load_rc}). Aborting." >&2
  exit 1
fi

# Step 4. Commit once.
echo "Committing..."
curl -sf --show-error "${SOLR_SERVER}/solr/${CORE}/update?commit=true" >/dev/null \
  || { echo "Commit failed." >&2; exit 1; }

# Step 5. Verify the document count matches.
actual=$(curl -sf "${SOLR_SERVER}/solr/${CORE}/query?q=*:*&rows=0" \
  | grep -oE '"numFound":[0-9]+' | head -1 | grep -oE '[0-9]+')
echo "Solr reports ${actual} documents; expected ${expected}."
if [ "${actual:-0}" != "${expected}" ]; then
  echo "DOCUMENT COUNT MISMATCH: loaded ${actual}, expected ${expected}. Aborting." >&2
  exit 1
fi
echo "Load complete: ${actual} documents."
