# This file tests the exact-match mode (the `exact` parameter) on the lookup and bulk_lookup
# endpoints. Tests for the default, tokenized search live in test_service.py.
import dataclasses
import json
import logging

import api.server
from api.server import app
from fastapi.testclient import TestClient

# Turn on debugging for tests.
logging.basicConfig(level=logging.DEBUG)

# HP:0001300 has preferred_name="parkinsonian disorder" and names=["Parkinsonian disease"].
# The preferred_name is NOT in names, making it a good test case for label vs. synonyms exact mode.

def test_exact_label_match():
    client = TestClient(app)
    # "parkinsonian disorder" is the preferred label for HP:0001300 — label mode should find it.
    response = client.post("/lookup", params={'string': 'parkinsonian disorder', 'exact': 'label', 'limit': 100})
    results = response.json()
    curies = [r['curie'] for r in results]
    assert 'HP:0001300' in curies

    # "Parkinsonian disease" is only a synonym (names entry), not the preferred label — label mode should NOT find it.
    response = client.post("/lookup", params={'string': 'Parkinsonian disease', 'exact': 'label', 'limit': 100})
    results = response.json()
    curies = [r['curie'] for r in results]
    assert 'HP:0001300' not in curies

def test_exact_synonyms_match():
    client = TestClient(app)
    # "Parkinsonian disease" is a synonym (names entry) for HP:0001300 — synonyms mode should find it.
    response = client.post("/lookup", params={'string': 'Parkinsonian disease', 'exact': 'synonyms', 'limit': 100})
    results = response.json()
    curies = [r['curie'] for r in results]
    assert 'HP:0001300' in curies

    # "parkinsonian disorder" is the preferred_name but NOT in names for HP:0001300 — synonyms mode should NOT find it.
    response = client.post("/lookup", params={'string': 'parkinsonian disorder', 'exact': 'synonyms', 'limit': 100})
    results = response.json()
    curies = [r['curie'] for r in results]
    assert 'HP:0001300' not in curies

def test_exact_any_match():
    client = TestClient(app)
    # exact=any should match on either the preferred label or a synonym.
    response = client.post("/lookup", params={'string': 'parkinsonian disorder', 'exact': 'any', 'limit': 100})
    curies = [r['curie'] for r in response.json()]
    assert 'HP:0001300' in curies

    response = client.post("/lookup", params={'string': 'Parkinsonian disease', 'exact': 'any', 'limit': 100})
    curies = [r['curie'] for r in response.json()]
    assert 'HP:0001300' in curies

def test_exact_no_partial_match():
    client = TestClient(app)
    # "parkinson" is only a substring of known terms — exact mode must return no match for HP:0001300.
    response = client.post("/lookup", params={'string': 'parkinson', 'exact': 'any', 'limit': 100})
    curies = [r['curie'] for r in response.json()]
    assert 'HP:0001300' not in curies

def test_exact_bulk_lookup():
    client = TestClient(app)
    params = {
        'strings': ['parkinsonian disorder', 'Parkinsonian disease', 'no match term xyz'],
        'exact': 'any',
        'limit': 10,
    }
    response = client.post("/bulk-lookup", json=params)
    results = response.json()

    assert set(results.keys()) == {'parkinsonian disorder', 'Parkinsonian disease', 'no match term xyz'}
    assert 'HP:0001300' in [r['curie'] for r in results['parkinsonian disorder']]
    assert 'HP:0001300' in [r['curie'] for r in results['Parkinsonian disease']]
    assert results['no match term xyz'] == []

def test_exact_rejects_autocomplete():
    client = TestClient(app)
    # autocomplete treats the final word as a prefix and exact requires the whole string to match,
    # so the combination has no sensible meaning and must be refused rather than silently resolved.
    response = client.get("/lookup", params={
        'string': 'parkinsonian disorder',
        'exact': 'label',
        'autocomplete': 'true',
    })
    assert response.status_code == 400
    assert 'autocomplete' in response.json()['detail']

    # The same request without autocomplete is fine.
    response = client.get("/lookup", params={'string': 'parkinsonian disorder', 'exact': 'label'})
    assert response.status_code == 200

    # ...and so is autocomplete without exact.
    response = client.get("/lookup", params={'string': 'parkinsonian dis', 'autocomplete': 'true'})
    assert response.status_code == 200

def test_exact_highlighting_returns_the_whole_matched_value():
    client = TestClient(app)

    # HP:0001300 is preferred_name="parkinsonian disorder", names=["Parkinsonian disease"].
    response = client.get("/lookup", params={
        'string': 'parkinsonian disorder',
        'exact': 'label',
        'highlighting': 'true',
        'limit': 100,
    })
    result = next(r for r in response.json() if r['curie'] == 'HP:0001300')
    # The whole value matched, so the whole value comes back marked up.
    assert result['highlighting']['labels'] == ['<strong>parkinsonian disorder</strong>']
    assert result['highlighting']['synonyms'] == []

    # Matching on a synonym highlights the synonym, in its original case rather than the query's.
    response = client.get("/lookup", params={
        'string': 'parkinsonian disease',
        'exact': 'synonyms',
        'highlighting': 'true',
        'limit': 100,
    })
    result = next(r for r in response.json() if r['curie'] == 'HP:0001300')
    assert result['highlighting']['labels'] == []
    assert result['highlighting']['synonyms'] == ['<strong>Parkinsonian disease</strong>']

    # Without highlighting the field stays empty, exactly as in the default search.
    response = client.get("/lookup", params={
        'string': 'parkinsonian disorder',
        'exact': 'label',
        'limit': 100,
    })
    result = next(r for r in response.json() if r['curie'] == 'HP:0001300')
    assert result['highlighting'] == {}

def test_exact_does_not_rewrite_smart_quotes():
    client = TestClient(app)

    # The default search folds typographic quotes to ASCII, because the input may have been
    # mangled by Windows (issue #176) and StandardTokenizer discards the punctuation anyway.
    response = client.get("/lookup", params={'string': '‘parkinson’', 'limit': 100})
    assert response.status_code == 200

    # Exact mode must not do that folding: the *_exactish fields keep whatever characters Babel
    # emitted, so rewriting the query would search for a string the caller never typed. MONDO:0005180
    # carries the ASCII "Parkinson's disease", which the ASCII query finds...
    response = client.get("/lookup", params={
        'string': "Parkinson's disease", 'exact': 'synonyms', 'limit': 100,
    })
    assert 'MONDO:0005180' in [r['curie'] for r in response.json()]

    # ...and the typographic-apostrophe query does not, since that is a different string.
    response = client.get("/lookup", params={
        'string': 'Parkinson’s disease', 'exact': 'synonyms', 'limit': 100,
    })
    assert 'MONDO:0005180' not in [r['curie'] for r in response.json()]


def test_exact_filter_query_is_not_cached():
    """
    The exactish clause must stay marked uncached.

    It is one distinct filterCache entry per distinct search string, and the filterCache is bounded
    by entry count (512), so caching it would evict the shared types:/taxa:/curie: filters that the
    ordinary search path relies on -- for a hit rate near zero on exactly the workload exact mode
    exists to serve. See the comment in lookup().
    """
    client = TestClient(app)
    response = client.get("/lookup", params={
        'string': 'parkinsonian disorder',
        'exact': 'label',
        'debug': 'query',
        'limit': 100,
    })
    assert response.status_code == 200
    results = response.json()
    assert results, "Expected at least one result to carry the debug information."

    # Dump the whole debug structure rather than reaching for a particular Solr key, since the
    # question is only whether the filter went to Solr marked uncached.
    debug_info = json.dumps(results[0]['debug'])
    assert 'preferred_name_exactish' in debug_info, \
        f"Expected the exactish filter query in the debug output, got: {debug_info}"
    assert 'cache=false' in debug_info, \
        f"Expected the exactish filter query to be marked uncached, got: {debug_info}"


def test_exact_combines_with_the_other_filters():
    """
    The exactish clause is appended to the same filter list as biolink_type, only_taxa and the
    prefix filters, so they have to keep working together -- filtering an exact lookup to a type is
    exactly what an NER pipeline resolving into a known category would do.
    """
    client = TestClient(app)

    base = {'string': 'parkinsonian disorder', 'exact': 'label', 'limit': 100}
    assert 'HP:0001300' in [r['curie'] for r in client.get("/lookup", params=base).json()]

    # HP:0001300 is typed Disease -- not PhenotypicFeature, despite the HP prefix -- so filtering
    # to that type keeps the result...
    response = client.get("/lookup", params={**base, 'biolink_type': 'biolink:Disease'})
    assert 'HP:0001300' in [r['curie'] for r in response.json()]

    # ...and filtering to an unrelated type removes it without disturbing the exact match itself.
    response = client.get("/lookup", params={**base, 'biolink_type': 'biolink:Gene'})
    assert 'HP:0001300' not in [r['curie'] for r in response.json()]

    # The prefix filters apply the same way.
    response = client.get("/lookup", params={**base, 'only_prefixes': 'MONDO'})
    assert 'HP:0001300' not in [r['curie'] for r in response.json()]

    response = client.get("/lookup", params={**base, 'exclude_prefixes': 'HP'})
    assert 'HP:0001300' not in [r['curie'] for r in response.json()]


def test_exact_rejects_autocomplete_in_bulk_lookup():
    """
    The 400 is raised inside lookup(), which bulk_lookup() now runs under asyncio.gather(), so
    check the exception still surfaces as a 400 rather than being swallowed into a 500.
    """
    client = TestClient(app)
    response = client.post("/bulk-lookup", json={
        'strings': ['parkinsonian disorder', 'Parkinson disease'],
        'exact': 'any',
        'autocomplete': True,
    })
    assert response.status_code == 400
    assert 'autocomplete' in response.json()['detail']


def test_exact_is_not_subject_to_the_minimum_query_length(monkeypatch):
    """
    config.minimum_query_length guards the default tokenized search, where a very short query is
    slow and useless. Exact mode is a single filter query against an untokenized field, and short
    labels are real -- the gene T, the element symbols, and "AD" for Alzheimer disease here -- so
    the minimum must not apply to it.

    The minimum is raised well above the query length rather than relying on the default of 2,
    so that the test says something whatever the deployment default becomes.
    """
    client = TestClient(app)
    monkeypatch.setattr(api.server, "config",
                        dataclasses.replace(api.server.config, minimum_query_length=5))

    # "AD" is a synonym of MONDO:0004975 (Alzheimer disease), and shorter than the minimum.
    response = client.get("/lookup", params={'string': 'AD', 'exact': 'synonyms', 'limit': 100})
    assert response.status_code == 200
    assert 'MONDO:0004975' in [r['curie'] for r in response.json()]

    # The same query without exact mode is still rejected, which is what makes the case above a
    # property of exact mode rather than of the minimum being unset.
    response = client.get("/lookup", params={'string': 'AD', 'limit': 100})
    assert response.status_code == 422

    # Exemption from the minimum is not exemption from the empty check: an empty exact query
    # would still reach Solr as an unparseable query and come back as a 500.
    for string in ('', '   '):
        response = client.get("/lookup", params={'string': string, 'exact': 'any'})
        assert response.status_code == 422, \
            f"Expected exact lookup of {string!r} to be rejected, got HTTP {response.status_code}"
