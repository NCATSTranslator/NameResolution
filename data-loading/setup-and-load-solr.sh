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
# documents (lines) before loading and compare against the *increase* in Solr's
# document count afterwards. Any failed upload (curl --fail) or a count mismatch
# aborts with a non-zero exit code, so a partial/dropped load cannot pass silently.
#
# The count is the important half of that: Solr rejects some bad input outright
# (an unknown field is a 400, which --fail catches), but it answers 200 and
# indexes nothing for other junk -- a file of plain text, for instance. Only the
# count notices that.

set -uo pipefail

# Configuration (overridable from the environment).
SOLR_SERVER="${SOLR_SERVER:-http://localhost:8983}"
CORE="${SOLR_CORE:-name_lookup}"
PARALLELISM="${LOAD_PARALLELISM:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"
# How long to wait for Solr to come up, in 3-second increments.
STARTUP_TRIES="${SOLR_STARTUP_TRIES:-60}"

GLOB="${1:?usage: setup-and-load-solr.sh \"data/synonyms/*.txt*\" (quote the glob!)}"

# Number of documents currently in the core.
solr_count() {
  curl -sf "${SOLR_SERVER}/solr/${CORE}/query?q=*:*&rows=0" \
    | grep -oE '"numFound":[0-9]+' | head -1 | grep -oE '[0-9]+'
}

# Step 1. Wait for the core to be available (bounded: a Solr that never comes up
# should fail the build, not hang it).
ready=""
for _ in $(seq "${STARTUP_TRIES}"); do
  if curl -sf "${SOLR_SERVER}/solr/${CORE}/admin/ping" >/dev/null 2>&1; then
    ready="yes"
    break
  fi
  echo "  -- Solr core '${CORE}' is unavailable - sleeping"
  sleep 3
done
if [ -z "$ready" ]; then
  echo "Solr core '${CORE}' at ${SOLR_SERVER} did not come up after $((STARTUP_TRIES * 3))s. Aborting." >&2
  exit 1
fi
echo "Solr core '${CORE}' is up at ${SOLR_SERVER}."

# Step 2. Expand the glob and count the input documents (one JSON doc per line).
# Emptying IFS stops the shell from splitting the glob on spaces, so filenames
# containing spaces survive; pathname expansion still yields one word per file.
saved_ifs="$IFS"
IFS=
shopt -s nullglob
files=( $GLOB )
shopt -u nullglob
IFS="$saved_ifs"
if [ ${#files[@]} -eq 0 ]; then
  echo "No files matched '${GLOB}'." >&2
  exit 1
fi
# `grep -c '[^[:space:]]'` counts every non-blank line, including a final one with
# no trailing newline. Blank lines are skipped because Solr ignores them, and we
# count per file so a missing newline never merges two files' records. This assumes
# one document per line -- pretty-printed JSON would be counted wrong.
echo "Counting documents in ${#files[@]} file(s)..."
expected=0
for f in "${files[@]}"; do
  n=$(grep -c '[^[:space:]]' "$f")
  echo "  ${n} docs in ${f}"
  expected=$((expected + n))
done
echo "Expecting to add ${expected} documents in total."

# The core may already contain documents (every load assigns fresh UUIDs, so
# loading is additive, never idempotent). Compare the delta, not the total.
before=$(solr_count)
if [ -z "${before}" ]; then
  echo "Could not read the current document count from Solr. Aborting." >&2
  exit 1
fi
if [ "${before}" -ne 0 ]; then
  echo "NOTE: core '${CORE}' already contains ${before} documents; this load adds to them."
fi

# Step 3. Load the files in parallel, streaming each one, without committing.
# curl -T streams the file rather than buffering it in memory (issue #194), and
# --fail turns any HTTP >=400 (e.g. an unknown field) into a non-zero exit. Input
# that Solr accepts but does not index is caught by the count check in step 5.
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

# Step 5. Verify that the number of documents added matches the input.
after=$(solr_count)
added=$(( ${after:-0} - before ))
echo "Solr reports ${after:-0} documents (${added} added); expected to add ${expected}."
if [ "${added}" != "${expected}" ]; then
  echo "DOCUMENT COUNT MISMATCH: added ${added}, expected ${expected}. Aborting." >&2
  exit 1
fi
echo "Load complete: ${after} documents in '${CORE}'."
