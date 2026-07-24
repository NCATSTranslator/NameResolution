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
- `api/server.py` - Core FastAPI application (~717 lines): all endpoints, Pydantic models, Solr query construction, environment config
- `api/apidocs.py` - Custom OpenAPI schema construction
- `api/resources/.openapi.yml` - OpenAPI 3.0.2 spec with service metadata
- `main.py` / `main.sh` - WSGI/ASGI entry points (port 2433)
- `tests/test_service.py` - Integration tests using FastAPI `TestClient`
- `tests/data/test-synonyms.json` - Test dataset for Solr

### Environment Variables
- `SOLR_HOST` / `SOLR_PORT` - Solr connection (default: `localhost:8983`)
- `LOGLEVEL` - Logging level
- `SERVER_ROOT` - API root path prefix
- `MATURITY_VALUE` / `LOCATION_VALUE` - TRAPI metadata fields

### API Endpoints
- `GET/POST /lookup` - Primary name-to-CURIE lookup with scoring
- `POST /bulk-lookup` - Batch queries via `NameResQuery` model
- `GET /reverse-lookup` - CURIE-to-names lookup
- `POST /synonyms` - Get synonyms for a list of CURIEs
- `POST /lookup-curies` - Filter existing CURIEs with type subsetting
- `GET /status` - Health check with Solr document counts, plus recent-query latency. Pass `?full=true` for Solr/JVM/host metrics (adds a Solr round-trip, so the default path stays cheap for k8s probes).

When adding a field from Solr's `/admin/metrics`, check the response shape against a live Solr first: the encoding varies by metric type, and a wrong guess silently yields `null` rather than an error. Counters (`QUERY./select.requests`) are scalars, timers (`requestTimes`) and meters (`errors`/`timeouts`) are nested objects, and JVM gauges (`memory.heap.used`) come back as *flat dotted keys*, not a nested `memory.heap` map.

### Data Model
Solr documents contain: `curie`, `preferred_name`, `names` (synonym list), and biolink type information. Lookup results are `LookupResult` objects with scoring fields. Results are conflated using GeneProtein and DrugChemical conflation rules.

### Infrastructure
- **Stateless API container** - Python 3.11.5/FastAPI
- **Persistent Solr 9.10 (standalone)** - Data in volume-mounted `./data/solr`
- **Data loading** - Separate pipeline in `data-loading/` (Makefile-driven, also has Kubernetes configs)
- **CI/CD** - GitHub Actions: runs tests on push, publishes Docker image to GitHub Packages on release

## Documentation
- `documentation/API.md` - Endpoint reference
- `documentation/Performance.md` - Reading the `/status` metrics; diagnosing Solr CPU/memory/load
- `documentation/Deployment.md` - Docker/Kubernetes deployment guide
- `documentation/Scoring.md` - Scoring algorithm details
- `documentation/NameResolution.ipynb` - Interactive usage examples