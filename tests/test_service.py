import logging

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
    """ Checks that calling NameRes without an input string return an empty list. """
    client = TestClient(app)
    response = client.get("/lookup", params={'string':''})
    syns = response.json()
    assert len(syns) == 0


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