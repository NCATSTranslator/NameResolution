# This file tests the exact-match mode (the `exact` parameter) on the lookup and bulk_lookup
# endpoints. Tests for the default, tokenized search live in test_service.py.
import logging

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
