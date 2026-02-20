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
    assert status['version'].startswith('9.')
    assert status['size'] != ''
    assert status['startTime']

    # Count the specific number of test documents we load.
    assert status['numDocs'] == 89
    assert status['maxDocs'] == 89
    assert status['deletedDocs'] == 0

