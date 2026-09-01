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

    # The Solr index version, called 'version' until #296.
    assert status['index_version'] > 1

    assert status['size'] != ''
    assert status['startTime']

    # Count the specific number of test documents we load.
    assert status['numDocs'] == 89
    assert status['maxDoc'] == 89
    assert status['deletedDocs'] == 0

