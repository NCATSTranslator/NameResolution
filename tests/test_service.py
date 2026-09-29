import dataclasses
import logging

import api.server
from api.server import app
from fastapi.testclient import TestClient

# Turn on debugging for tests.
logging.basicConfig(level=logging.DEBUG)

def test_simple_check():
    client = TestClient(app)
    params = {'string':'alzheimer', 'biolink_type': ''}
    response = client.post("/lookup",params=params)
    syns = response.json()
    #There are more than 10, but it should cut off at 10 if we don't give it a max?
    assert len(syns) == 10

def test_empty():
    """ An empty (too-short) query on /lookup is rejected with HTTP 422. """
    client = TestClient(app)
    response = client.get("/lookup", params={'string':''})
    assert response.status_code == 422


def test_minimum_query_length():
    """
    Queries shorter than config.minimum_query_length (default 2) are rejected on
    /lookup with HTTP 422, but degrade to an empty list per-key on /bulk-lookup so
    one short string doesn't fail the whole batch. The minimum is reported by /status.
    """
    client = TestClient(app)

    # A single character on /lookup is too short -> 422.
    response = client.get("/lookup", params={'string': 'a'})
    assert response.status_code == 422

    # A valid two-character query is not rejected (goes to Solr, returns a 200 list).
    response = client.get("/lookup", params={'string': 'ab'})
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    # On /bulk-lookup, a too-short string yields [] for its key; others resolve; request is 200.
    response = client.post("/bulk-lookup", json={'strings': ['a', 'Parkinson'], 'limit': 100})
    assert response.status_code == 200
    results = response.json()
    assert results['a'] == []
    assert len(results['Parkinson']) == 34

    # /status advertises the configured minimum under a nested config block. Compared against
    # the running config rather than a literal 2, so that this still tests the wiring in a
    # deployment that sets NAMERES_MINIMUM_QUERY_LENGTH, and survives another key being added.
    response = client.get("/status")
    assert response.json()['config']['minimum_query_length'] == api.server.config.minimum_query_length


def test_too_short_is_reported_as_a_validation_error():
    """
    A query rejected for its length must come back in the same shape as one rejected by FastAPI's
    own parameter validation -- `detail` a list of error objects, not a bare string -- because a
    client iterating `detail` should not have to special-case this one rejection. That is also
    what the endpoint's documented 422 schema (HTTPValidationError) promises; see
    test_lookup_documents_the_validation_error_schema.
    """
    client = TestClient(app)
    response = client.get("/lookup", params={'string': 'a'})
    assert response.status_code == 422

    detail = response.json()['detail']
    assert isinstance(detail, list), f"Expected a list of errors, got {detail!r}"
    assert detail[0]['loc'] == ['query', 'string']
    assert detail[0]['type'] == 'string_too_short'
    assert 'msg' in detail[0]

    # An ordinary validation failure on the same endpoint (limit above its maximum) is shaped the
    # same way, which is the point of the comparison.
    response = client.get("/lookup", params={'string': 'Parkinson', 'limit': 100000})
    assert response.status_code == 422
    assert isinstance(response.json()['detail'], list)


def test_lookup_documents_the_validation_error_schema():
    """
    FastAPI only generates the HTTPValidationError body for a 422 that the operation has not
    already declared itself, so a hand-written `responses={422: ...}` on these endpoints would
    silently strip the schema from the OpenAPI document and leave client generators with an
    untyped error. Pin that the schema is there for both /lookup operations.
    """
    client = TestClient(app)
    schema = client.get("/openapi.json").json()

    for method in ('get', 'post'):
        response_422 = schema['paths']['/lookup'][method]['responses']['422']
        ref = response_422['content']['application/json']['schema']['$ref']
        assert ref.endswith('/HTTPValidationError'), \
            f"/lookup {method.upper()} documents 422 as {ref}, not HTTPValidationError"


def test_empty_query_is_rejected_however_low_the_minimum_is(monkeypatch):
    """
    An empty query must never reach Solr: it builds the query `"" OR ()`, which Solr rejects as a
    parse error, turning an empty search box into an HTTP 500. The length check is what stops it,
    so it has to hold even when minimum_query_length is set to 0 -- the obvious way to turn the
    minimum off, and the same thing SOLR_TIMEOUT_SECONDS=0 means one setting above it.
    """
    client = TestClient(app)
    monkeypatch.setattr(api.server, "config",
                        dataclasses.replace(api.server.config, minimum_query_length=0))

    # A single character is now allowed through to Solr...
    response = client.get("/lookup", params={'string': 'a'})
    assert response.status_code == 200

    # ...but an empty string, and one that is empty once stripped, are still rejected.
    for string in ('', '   '):
        response = client.get("/lookup", params={'string': string})
        assert response.status_code == 422, \
            f"Expected {string!r} to be rejected, got HTTP {response.status_code}"

    # And on /bulk-lookup it degrades to [] for that key rather than failing the whole batch.
    response = client.post("/bulk-lookup", json={'strings': ['', 'Parkinson'], 'limit': 100})
    assert response.status_code == 200
    results = response.json()
    assert results[''] == []
    assert len(results['Parkinson']) > 0

def test_limit():
    client = TestClient(app)
    params = {'string': 'alzheimer', 'limit': 1}
    response = client.post("/lookup", params=params)
    syns = response.json()
    assert len(syns) == 1
    params2 = {'string': 'alzheimer', 'limit': 100}
    response = client.post("/lookup", params=params2)
    syns = response.json()
    assert len(syns) == 30

def test_type_subsetting():
    client = TestClient(app)
    #Get everything with Parkinson (57)
    params = {'string': 'Parkinson', "limit": 100}
    response = client.post("/lookup", params=params)
    syns = response.json()
    assert len(syns) == 34
    #Now limit to Disease (just 53)
    params = {'string': 'Parkinson', "limit": 100, "biolink_type": "biolink:Disease"}
    response = client.post("/lookup", params=params)
    syns = response.json()
    assert len(syns) == 33
    #Now verify that NamedThing is everything
    params = {'string': 'Parkinson', "limit": 100, "biolink_type": "biolink:NamedThing"}
    response = client.post("/lookup", params=params)
    syns = response.json()
    assert len(syns) == 34

def test_offset():
    client = TestClient(app)
    #There are 31 total.  If we say, start at 20 and give me then next 100 , we should get 11
    params = {'string': 'alzheimer', 'limit': 100, 'offset': 20}
    response = client.post("/lookup", params=params)
    syns = response.json()
    assert len(syns) == 10

def test_hyphens():
    """The test data contains CHEBI:74925 with name 'beta-secretase inhibitor.
    Show that we can find it with or without the hyphen"""
    client = TestClient(app)
    #with hyphen
    params = {'string': 'beta-secretase'}
    response = client.post("/lookup", params=params)
    syns = response.json()

    assert len(syns) == 2
    assert syns[0]["curie"] == 'CHEBI:74925'
    assert syns[1]["curie"] == 'MONDO:0011561'

    #no hyphen
    params = {'string': 'beta secretase'}
    response = client.post("/lookup", params=params)
    syns = response.json()
    assert len(syns) == 2
    assert syns[0]["curie"] == 'CHEBI:74925'
    assert syns[1]["curie"] == 'MONDO:0011561'

def test_structure():
    client = TestClient(app)
    params = {'string': 'beta-secretase'}
    response = client.post("/lookup", params=params)
    syns = response.json()
    #do we get a preferred name and type?
    assert syns[0]["label"] == 'BACE1 inhibitor'
    assert syns[0]["types"] == ["biolink:NamedThing"]

def test_autocomplete():
    client = TestClient(app)
    params = {'string': 'beta-secretase', 'autocomplete': 'true'}
    response = client.post("/lookup", params=params)
    syns = response.json()
    assert len(syns) == 1
    #do we get a preferred name and type?
    assert syns[0]["label"] == 'BACE1 inhibitor'
    assert syns[0]["types"] == ["biolink:NamedThing"]

    # Should also work with an incomplete search.
    params = {'string': 'beta-secretase', 'autocomplete': 'false'}
    response = client.post("/lookup", params=params)
    syns = response.json()
    assert len(syns) == 2
    #do we get a preferred name and type?
    assert syns[0]['curie'] == 'CHEBI:74925'
    assert syns[0]["label"] == 'BACE1 inhibitor'
    assert syns[0]["types"] == ["biolink:NamedThing"]
    assert syns[1]['curie'] == 'MONDO:0011561'
    assert syns[1]["label"] == 'Alzheimer disease 6'
    assert syns[1]["types"][0] == "biolink:Disease"

    # Or even an incomplete query.
    params = {'string': 'beta-secreta', 'autocomplete': 'false'}
    response = client.post("/lookup", params=params)
    syns = response.json()
    assert len(syns) == 2
    #do we get a preferred name and type?
    assert syns[0]['curie'] == 'CHEBI:74925'
    assert syns[0]["label"] == 'BACE1 inhibitor'
    assert syns[0]["types"] == ["biolink:NamedThing"]
    assert syns[1]['curie'] == 'MONDO:0011561'
    assert syns[1]["label"] == 'Alzheimer disease 6'
    assert syns[1]["types"][0] == "biolink:Disease"

    # Previously, searching for an autocomplete query ending in whitespace
    # would trigger a blank search (e.g. `abc ` would be expanded into `abc *`).
    params = {'string': 'beta-secretase ', 'autocomplete': 'true'}
    response = client.post("/lookup", params=params)
    syns = response.json()

    # When this bug was around, it would result in the following:
    # assert len(syns) == 10
    # assert syns[0]['curie'] == 'CHEBI:48407'
    # assert syns[0]["label"] == 'antiparkinson agent'
    # assert syns[0]["types"] == ["biolink:NamedThing"]

    # But now we only get beta-secretase.
    assert len(syns) == 1
    assert syns[0]['curie'] == 'CHEBI:74925'
    assert syns[0]["label"] == 'BACE1 inhibitor'
    assert syns[0]["types"] == ["biolink:NamedThing"]

def test_windows_smartquotes():
    client = TestClient(app)

    # Query with Windows Smart Quote (’), but this should match against our database which uses Unicode quotes.
    response = client.get("/lookup", params={'string': "Alzheimer’s disease", 'biolink_type': 'Disease'})
    syns = response.json()

    assert len(syns) > 1
    assert syns[0]['curie'] == 'MONDO:0004975'
    assert syns[0]['label'] == 'Alzheimer disease'
    assert syns[0]['types'][0] == 'biolink:Disease'

def test_bulk_lookup():
    client = TestClient(app)
    params = {
        'strings': ['beta-secretase', 'Parkinson'],
        'limit': 100,
    }
    response = client.post("/bulk-lookup", json=params)
    results = response.json()
    assert len(results) == 2
    assert len(results['beta-secretase']) == 2
    assert results['beta-secretase'][0]['curie'] == 'CHEBI:74925'
    assert results['beta-secretase'][0]['label'] == 'BACE1 inhibitor'
    assert len(results['Parkinson']) == 34

    assert results['Parkinson'][0]['curie'] == 'MONDO:0005180'
    assert results['Parkinson'][0]['label'] == "Parkinson disease"

    # Try it again with the biolink_types set.
    params['biolink_types'] = ['biolink:Disease']
    response = client.post("/bulk-lookup", json=params)
    results = response.json()
    assert len(results) == 2
    assert len(results['beta-secretase']) == 1
    # We match MONDO:0011561 "Alzheimer disease 6" because it contains the word "beta".
    assert results['beta-secretase'][0]['curie'] == 'MONDO:0011561'
    assert results['beta-secretase'][0]['label'] == 'Alzheimer disease 6'

    assert len(results['Parkinson']) == 33
    assert results['Parkinson'][0]['curie'] == 'MONDO:0005180'
    assert results['Parkinson'][0]['label'] == "Parkinson disease"

def test_synonyms():
    """
    Test the /synonyms endpoints -- these are used to look up all the information we know about a preferred CURIE.
    """
    client = TestClient(app)
    response = client.get("/synonyms", params={'preferred_curies': ['CHEBI:74925', 'NONE:1234', 'MONDO:0000828']})

    results = response.json()
    chebi_74925_results = results['CHEBI:74925']
    assert chebi_74925_results['curie'] == 'CHEBI:74925'
    assert chebi_74925_results['preferred_name'] == 'BACE1 inhibitor'

    none_1234_results = results['NONE:1234']
    assert none_1234_results == {}

    mondo_0000828_results = results['MONDO:0000828']
    assert mondo_0000828_results['curie'] == 'MONDO:0000828'
    assert mondo_0000828_results['preferred_name'] == 'juvenile-onset Parkinson disease'

    response = client.post("/synonyms", json={'preferred_curies': ['MONDO:0000828', 'NONE:1234', 'CHEBI:74925']})

    results = response.json()
    chebi_74925_results = results['CHEBI:74925']
    assert chebi_74925_results['curie'] == 'CHEBI:74925'
    assert chebi_74925_results['preferred_name'] == 'BACE1 inhibitor'

    none_1234_results = results['NONE:1234']
    assert none_1234_results == {}

    mondo_0000828_results = results['MONDO:0000828']
    assert mondo_0000828_results['curie'] == 'MONDO:0000828'
    assert mondo_0000828_results['preferred_name'] == 'juvenile-onset Parkinson disease'

def test_only_taxa_queries():
    client = TestClient(app)
    response = client.get("/lookup", params={
        'string': 'FTD',
    })
    results_all_ftd = response.json()
    assert len(results_all_ftd) == 2
    assert results_all_ftd[0]['curie'] == 'NCBIGene:378899'
    assert results_all_ftd[1]['curie'] == 'MONDO:0010857'

    response = client.get("/lookup", params={
        'string': 'FTD',
        'only_taxa': 'NCBITaxon:9031',
    })
    results_ftd_with_only_taxon = response.json()
    assert len(results_ftd_with_only_taxon) == 2
    assert results_ftd_with_only_taxon[0]['curie'] == 'NCBIGene:378899'
    assert results_ftd_with_only_taxon[1]['curie'] == 'MONDO:0010857'

    response = client.get("/lookup", params={
        'string': 'FTD',
        'only_taxa': 'NCBITaxon:9031',
        'biolink_type': 'biolink:Gene'
    })
    results_ftd_gene_with_only_taxon = response.json()
    assert len(results_ftd_gene_with_only_taxon) == 1
    assert results_ftd_gene_with_only_taxon[0]['curie'] == 'NCBIGene:378899'

    response = client.get("/lookup", params={
        'string': 'FTD',
        'only_taxa': 'NCBITaxon:9031',
        'biolink_type': 'biolink:Disease'
    })
    results_ftd_disease_with_only_taxon = response.json()
    assert len(results_ftd_disease_with_only_taxon) == 1
    assert results_ftd_disease_with_only_taxon[0]['curie'] == 'MONDO:0010857'

def test_bulk_lookup_beyond_concurrency_limit(monkeypatch):
    """
    bulk_lookup() runs its per-string lookups concurrently behind a semaphore, so exercise it with
    more strings than the semaphore allows in flight at once: every string must still come back
    keyed to its own results, however the lookups interleave. This is a property of bulk lookup
    rather than of exact matching; exact=label is used only to make each string's result definite.

    The limit is patched down rather than sending a whole config.solr_max_concurrent_lookups worth
    of real strings, because the default (100) is larger than the number of distinct labels in the
    test data. Swapping in a replacement Config works because bulk_lookup() reads the limit and
    builds its semaphore per request, at call time.
    """
    client = TestClient(app)
    monkeypatch.setattr(api.server, "config",
                        dataclasses.replace(api.server.config, solr_max_concurrent_lookups=3))

    expected = {
        'parkinsonian disorder': 'HP:0001300',
        'Resting tremor': 'HP:0002322',
        'juvenile-onset Parkinson disease': 'MONDO:0000828',
        'postencephalitic Parkinson disease': 'MONDO:0001945',
        'Parkinson disease': 'MONDO:0005180',
        'secondary Parkinson disease': 'MONDO:0006966',
        'Alzheimer disease type 1': 'MONDO:0007088',
        'Alzheimer disease 2': 'MONDO:0007089',
        'Lewy body dementia': 'MONDO:0007488',
        'dystonia 5': 'MONDO:0007495',
        'dystonia 12': 'MONDO:0007496',
        'antiparkinson agent': 'CHEBI:48407',
        'BACE1 inhibitor': 'CHEBI:74925',
    }
    assert len(expected) > api.server.config.solr_max_concurrent_lookups, \
        "This test is only meaningful with more strings than can be looked up concurrently."

    response = client.post("/bulk-lookup", json={
        'strings': list(expected.keys()),
        'exact': 'label',
        'limit': 100,
    })
    results = response.json()

    assert set(results.keys()) == set(expected.keys())
    for string, curie in expected.items():
        assert curie in [r['curie'] for r in results[string]], \
            f"Expected {curie} in the results for {string!r}, got {results[string]}"


# The default (non-exact) search is tokenized, not fuzzy. These two tests pin down that distinction,
# since documentation/API.md makes a promise about it that is easy to break by changing the query or
# the schema's field types: word order is not required, but misspellings are not tolerated either.

def test_default_search_ignores_word_order():
    client = TestClient(app)
    # MONDO:0005180 is "Parkinson disease". The tokens may arrive in any order.
    response = client.get("/lookup", params={'string': 'disease Parkinson', 'limit': 100})
    curies = [r['curie'] for r in response.json()]
    assert 'MONDO:0005180' in curies

def test_default_search_does_not_tolerate_misspellings():
    client = TestClient(app)
    # No document contains the token "parkinsen", and there is no edit-distance matching to bridge
    # the gap to "parkinson", so this must find nothing at all.
    response = client.get("/lookup", params={'string': 'parkinsen', 'limit': 100})
    assert response.json() == []

    # The same string with the typo corrected does match, so the miss above is the spelling and not
    # some other property of the query.
    response = client.get("/lookup", params={'string': 'parkinson', 'limit': 100})
    assert len(response.json()) > 0

def test_solr_settings_are_sane():
    # A concurrency limit of 0 would make every bulk lookup wait on a semaphore nobody can acquire.
    assert api.server.config.solr_max_concurrent_lookups >= 1
    # A stalled Solr connection must not be able to pin a bulk request forever by default.
    assert api.server.config.solr_timeout is None or api.server.config.solr_timeout > 0


def test_concurrency_limit_is_clamped_to_at_least_one(monkeypatch):
    """
    SOLR_MAX_CONCURRENT_LOOKUPS=0 would build a semaphore nobody can ever acquire, hanging every
    bulk lookup with no error and no log line. The clamp runs when Config is instantiated at import,
    so this has to reload the module to exercise it.
    """
    import importlib

    try:
        monkeypatch.setenv("SOLR_MAX_CONCURRENT_LOOKUPS", "0")
        importlib.reload(api.server)
        assert api.server.config.solr_max_concurrent_lookups == 1

        monkeypatch.setenv("SOLR_MAX_CONCURRENT_LOOKUPS", "25")
        importlib.reload(api.server)
        assert api.server.config.solr_max_concurrent_lookups == 25
    finally:
        # Restore the module for whatever runs next, since reload mutates it in place.
        monkeypatch.delenv("SOLR_MAX_CONCURRENT_LOOKUPS", raising=False)
        importlib.reload(api.server)
