# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NameRes (Name Resolver) is a biomedical entity name resolution service that maps lexical strings to CURIEs from vocabularies/ontologies. It is part of the NCATS Translator ecosystem. The API is built with FastAPI and backed by Apache Solr.

## Commands

### Running Tests
```bash
# Start standalone Solr and create the name_lookup core from the checked-in configset
docker run --name name_lookup -d -p 8983:8983 solr:9.10
docker cp data-loading/configsets/name_lookup name_lookup:/tmp/name_lookup
docker exec name_lookup solr create -c name_lookup -d /tmp/name_lookup

# Load test data into the core (parallel load, with a document-count guard)
./data-loading/setup-and-load-solr.sh tests/data/test-synonyms.json

# Run all tests
python -m pytest tests/

# Run a single test
python -m pytest tests/test_service.py::test_function_name
```

### Running Locally
```bash
# Start full stack (Solr + API)
docker-compose up

# Run API directly (requires Solr already running)
uvicorn api.server:app --host 0.0.0.0 --port 2433
```

### Data Loading
```bash
# Download synonyms, start Solr, load data (production)
cd data-loading && make all

# Individual steps
make start-solr-backup
make check-solr-backup
make stop-solr
```

### Dependencies
```bash
pip install -r requirements.txt
```

## Architecture

### Request Flow
1. Client sends query to FastAPI endpoint on port 2433
2. `api/server.py` constructs Solr query parameters
3. `httpx` async client queries the Solr instance (default: `localhost:8983`)
4. Results are scored, normalized, and returned as JSON

### Key Files
- `api/server.py` - Core FastAPI application: all endpoints, Pydantic models, Solr query construction, environment config
- `api/apidocs.py` - Custom OpenAPI schema construction
- `api/resources/openapi.yml` - OpenAPI 3.0.2 spec with service metadata
- `main.py` / `main.sh` - WSGI/ASGI entry points (port 2433)
- `tests/test_service.py` - Integration tests using FastAPI `TestClient`
- `tests/test_exact_mode.py` - Integration tests for the `exact` parameter
- `tests/data/test-synonyms.json` - Test dataset for Solr

### Environment Variables
- `SOLR_HOST` / `SOLR_PORT` - Solr connection (default: `localhost:8983`)
- `SOLR_MAX_CONCURRENT_LOOKUPS` / `SOLR_TIMEOUT_SECONDS` - Bulk-lookup fan-out bound and Solr query timeout (see `documentation/Deployment.md`)
- `NAMERES_MINIMUM_QUERY_LENGTH` - Shortest query `/lookup` and `/bulk-lookup` will search for (default: 2)
- `LOGLEVEL` - Logging level
- `SERVER_ROOT` - API root path prefix
- `MATURITY_VALUE` / `LOCATION_VALUE` - TRAPI metadata fields

### API Endpoints
- `GET/POST /lookup` - Primary name-to-CURIE lookup with scoring
- `POST /bulk-lookup` - Batch queries via `NameResQuery` model
- `GET /reverse-lookup` - CURIE-to-names lookup
- `POST /synonyms` - Get synonyms for a list of CURIEs
- `POST /lookup-curies` - Filter existing CURIEs with type subsetting
- `GET /status` - Health check with Solr document counts

### Data Model
Solr documents contain: `curie`, `preferred_name`, `names` (synonym list), and biolink type information. Lookup results are `LookupResult` objects with scoring fields. Results are conflated using GeneProtein and DrugChemical conflation rules.

### Infrastructure
- **Stateless API container** - Python 3.11.5/FastAPI
- **Persistent Solr 9.10 (standalone)** - Data in volume-mounted `./data/solr`
- **Data loading** - Separate pipeline in `data-loading/` (Makefile-driven, also has Kubernetes configs)
- **CI/CD** - GitHub Actions: runs tests on push, publishes Docker image to GitHub Packages on release

## Gotchas

- **The default search is *tokenized*, not *fuzzy*.** It matches the query's tokens in any order (order and adjacency are rewarded by the phrase-field boost, not required), and `autocomplete=true` makes the final token a prefix. There is no edit-distance matching, and `lookup()` escapes Solr's `~` out of the query so callers cannot request it. Do not describe it as "fuzzy" in docs or parameter descriptions -- that promises typo tolerance the service has never had. `tests/test_service.py` pins both halves of this.
- **Solr's `filterCache` is bounded by entry count (512), not by memory.** It earns its keep on shared, reusable filters (`types:`, `taxa:`, `curie:`). A filter whose value varies per query -- as exact mode's does -- must be marked `{!cache=false}`, or one bulk request evicts the whole cache and slows the ordinary search path down as collateral damage. See the comment in `lookup()`.
- **The empty-query rejection is not the same rule as `minimum_query_length`.** An empty string reaches Solr as `"" OR ()`, which is a parse error and therefore an HTTP 500 for what is really an empty search box, so `lookup()` floors its length check at 1 (`max(1, config.minimum_query_length)`) rather than deriving it from the setting alone. Folding the two together breaks the moment someone sets `NAMERES_MINIMUM_QUERY_LENGTH=0` to turn the minimum off. Exact mode is exempt from the setting but not from the floor.
- **Do not declare a custom `responses={422: ...}` on an endpoint.** FastAPI adds the `HTTPValidationError` body only when the operation has not already declared a 422 of its own, so a hand-written one silently strips the schema and leaves client generators with an untyped error. For the same reason, `lookup()` reports a too-short query with `RequestValidationError`, not `HTTPException(422)`: the latter returns `{"detail": "<string>"}` where FastAPI's own validation returns `{"detail": [...]}`. `tests/test_service.py` pins both.
- **Query-side string normalization must not be applied to exact matching.** The `*_exactish` fields are a KeywordTokenizer plus a LowerCaseFilter and fold nothing else, so the smart-quote rewrite (and anything like it) would search for a string the caller never typed. The default path is unaffected because StandardTokenizer discards the punctuation anyway.
- **The custom OpenAPI document must be installed by overriding `app.openapi`, not by assigning `app.openapi_schema`.** Since FastAPI 0.137.0, `openapi()` rebuilds the schema whenever the app's recorded routes version doesn't match the router's current one, and a schema assigned directly to the attribute never carries that stamp -- so FastAPI quietly overwrites it on the first request to `/openapi.json` and serves its default document, losing `info.x-translator` (which is what SmartAPI registration keys off), `contact`, `termsOfService`, `tags` and `servers`. It fails open, so nothing but the served spec shows it: that is how v1.7.0 shipped it (issue #294). `tests/test_openapi.py` pins this, and has to go through `TestClient` -- asserting on `construct_open_api_schema()` directly passes throughout the bug. `fastapi` is pinned in `requirements.txt` for the same reason.
- **`/status`'s `version` is the API version, not the Solr index version.** The Solr index version is `index_version`; it was called `version` until #296, which handed that key to the OpenAPI `info.version` so that NameRes and NodeNorm spell the API version the same way. `backend` is always `"solr"` here, and exists only to tell this implementation apart from the Elasticsearch-backed NameRes at biothings/NameResolutionAPI -- before it, the two differed only by a word of prose in `message`, which is a worse thing for a downstream check to depend on. Both branches of `status()` must report `backend` and `version` (a mis-pointed deployment returning `Expected core not found.` is exactly when a caller needs them), which is why the shared keys are built once as `common` and spread into each return.
- **Declaring metadata in `openapi.yml` is not enough to serve it.** `construct_open_api_schema()` copies an explicit allowlist of `info` keys into the document (and `get_app_info()` a narrower one for the `FastAPI()` constructor); anything not named there is dropped without a word. That is how `info.contact` and `info.license` sat declared-but-unserved for years. Adding a key means adding it to the copy list *and* asserting it in `tests/test_openapi.py`.

## Documentation
- `documentation/API.md` - Endpoint reference
- `documentation/Deployment.md` - Docker/Kubernetes deployment guide
- `documentation/Scoring.md` - Scoring algorithm details
- `documentation/NameResolution.ipynb` - Interactive usage examples