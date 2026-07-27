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
- `tests/data/test-synonyms.json` - Test dataset for Solr

### Environment Variables
See `documentation/Deployment.md` for the full list. The main ones:
- `SOLR_HOST` / `SOLR_PORT` / `SOLR_CORE` - Solr connection (default: `localhost:8983`, core `name_lookup`)
- `LOGLEVEL` - Logging level
- `SERVER_NAME` / `SERVER_ROOT` - Infores ID and API root path prefix
- `MATURITY_VALUE` / `LOCATION_VALUE` - TRAPI metadata fields
- `BABEL_VERSION` / `BABEL_VERSION_URL` / `BIOLINK_MODEL_TAG` - reported by `/status`; describe the
  data the index was built from
- `OTEL_ENABLED` / `JAEGER_*` - OpenTelemetry

### API Endpoints
- `GET/POST /lookup` - Primary name-to-CURIE lookup with scoring
- `POST /bulk-lookup` - Batch queries via `NameResQuery` model
- `GET/POST /synonyms` - Get synonyms for a list of preferred CURIEs
- `GET/POST /reverse_lookup` - Deprecated alias for `/synonyms`
- `GET /status` - Health check with Solr document counts, plus the Babel and Biolink versions

### Data Model
Solr documents contain: `curie`, `preferred_name`, `names` (synonym list), and biolink type information. Lookup results are `LookupResult` objects with scoring fields. Results are conflated using GeneProtein and DrugChemical conflation rules.

### Infrastructure
- **Stateless API container** - Python 3.11.5/FastAPI
- **Persistent Solr 9.10 (standalone)** - Data in volume-mounted `./data/solr`
- **Data loading** - Separate pipeline in `data-loading/` (Makefile-driven, also has Kubernetes configs)
- **CI/CD** - GitHub Actions: runs tests on push, publishes Docker image to GitHub Packages on release

## Documentation
- `documentation/API.md` - Endpoint reference
- `documentation/Babel.md` - Where the data comes from, and the Babel behaviour visible through this
  API. This is the only file that should link to a *specific file* inside the Babel repository; keep
  new cross-repo links here so a reorganization there is a one-file fix.
- `documentation/Deployment.md` - Docker/Kubernetes deployment guide
- `documentation/Scoring.md` - Scoring algorithm details
- `documentation/NameResolution.ipynb` - Interactive usage examples
- `documentation/TranslatorGuide.md` - Translator-specific usage guidance
- `documentation/LLMs.md` - how to install the agent skill and fetch it from a running instance
- `skills/nameres/SKILL.md` - agent-facing usage instructions, **also served at `GET /llms.txt`**.
  The Dockerfile's `COPY .` and the absence of `skills/` from `.dockerignore` are what put it in a
  built image; narrowing either 404s that route in production while every test still passes locally.

### Writing for agents

`skills/nameres/SKILL.md` carries **how to call the API, how to read the result, and the traps that
silently produce a plausible wrong answer**. It does not carry exhaustive parameter tables, scoring
internals, or any number that a Babel rebuild or a tuning change could falsify — those live in
`documentation/` and the skill links to them. If a fact in the skill can go stale without a test
failing, either delete it or add the test.

Links in `SKILL.md` must be **absolute** `https://github.com/NCATSTranslator/NameResolution/blob/main/...`
URLs. The file is served raw at `/llms.txt` and pasted into other agents, where a relative link
resolves against the API host and 404s. `tests/test_llms_txt.py` enforces this.

### Linking to GitHub

NameResolution, Babel and NodeNormalization all use `main` as their default branch. Babel's was
renamed from `master` fairly recently and NameResolution has no `master` branch at all, so
`/blob/master/` URLs resolve only through GitHub's post-rename redirect — they look fine until
that redirect goes away. Always write `/blob/main/`.

Do not check this against a local clone's `origin/HEAD`: that ref is cached at clone time and does
not follow a remote rename, so it will still say `master` long after the rename. Use
`gh repo view <owner>/<repo> --json defaultBranchRef`.

`gh pr view --json commits,changedFiles` serves a cached summary that can be badly stale — it has
reported 49 commits / 38 files for a PR that was really 12 and 20. To check what a PR actually
contains, use the compare API:
`gh api repos/<owner>/<repo>/compare/<base>...<head> --jq '.ahead_by, .behind_by, (.files|length)'`.

### Deployed instances are not this code

Do not verify behaviour against a deployed NameRes and assume it matches the repo. In July 2026 the
ITRB CI, test and prod instances, RENCI dev and this branch all disagreed about `/status` alone —
prod predated `babel_version` entirely, CI nested the Solr fields under `solr`. Check
`/status`'s `nameres_version` before trusting a live response as ground truth for repo behaviour,
and say in the PR which instance an example came from.