import logging

from api.server import app
from fastapi.testclient import TestClient

# Turn on debugging for tests.
logging.basicConfig(level=logging.DEBUG)

def test_status():
    client = TestClient(app)
    response = client.get("/status")
    status = response.json()

    assert status['status'] == 'ok'
    assert status['message'] != ''
    assert 'babel_version' in status
    assert 'babel_version_url' in status
    assert 'biolink_model' in status
    assert 'tag' in status['biolink_model']
    assert 'nameres_version' in status

    # Issue #296: `backend` tells this Solr implementation apart from the
    # Elasticsearch-backed NameRes (biothings/NameResolutionAPI), which reports
    # 'elasticsearch' here.
    assert status['backend'] == 'solr'

    # `version` is the API version, and must be identical to the version in the served
    # OpenAPI document -- that identity is the whole point of the field, so it is pinned
    # against /openapi.json rather than against a literal that would need editing on
    # every version bump.
    assert status['version'] == client.get("/openapi.json").json()['info']['version']
    assert status['nameres_version'] == 'v' + status['version']

    # Everything read from Solr is grouped under 'solr'. Until #296 these fields sat at
    # the top level, where the index version was 'version'; make sure none of them has
    # crept back out.
    for key in ('numDocs', 'maxDoc', 'deletedDocs', 'segmentCount', 'size', 'startTime',
                'lastModified', 'index_version'):
        assert key not in status
    solr = status['solr']

    assert solr['core'] == 'name_lookup'
    assert solr['version'] > 1
    assert solr['size'] != ''
    assert solr['startTime']

    # Count the specific number of test documents we load.
    assert solr['numDocs'] == 89
    assert solr['maxDoc'] == 89
    assert solr['deletedDocs'] == 0
