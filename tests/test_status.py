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

    solr = status['solr']
    assert solr['version'] != ''
    assert solr['size'] != ''
    assert solr['startTime']

    # Count the specific number of test documents we load.
    assert solr['numDocs'] == 89
    assert solr['maxDoc'] == 89
    assert solr['deletedDocs'] == 0
