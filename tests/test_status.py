import json
import logging
import re
import warnings
from pathlib import Path

from api.apidocs import get_app_info
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
    assert status['version'] > 1
    assert status['size'] != ''
    assert status['startTime']

    # The conflations baked into this index (documentation/Babel.md explains why they
    # are baked in rather than applied per query).
    assert status['conflations'] == ['GeneProtein', 'DrugChemical']
    assert status['conflation_url'].startswith('https://github.com/NCATSTranslator/Babel/')

    # Count the specific number of test documents we load.
    assert status['numDocs'] == 89
    assert status['maxDoc'] == 89
    assert status['deletedDocs'] == 0


def test_status_reports_environment(monkeypatch):
    """The environment variables documented in documentation/Deployment.md are the only
    record of which data an instance is serving, so check they reach /status."""
    monkeypatch.setenv('BABEL_VERSION', '2026jul22')
    monkeypatch.setenv('BABEL_VERSION_URL', 'https://example.org/releases/2026jul22.md')
    monkeypatch.setenv('BIOLINK_MODEL_TAG', 'v4.2.6-rc5')

    status = TestClient(app).get("/status").json()

    assert status['babel_version'] == '2026jul22'
    assert status['babel_version_url'] == 'https://example.org/releases/2026jul22.md'
    assert status['biolink_model']['tag'] == 'v4.2.6-rc5'
    # The other two Biolink URLs are derived from the tag rather than set separately.
    assert status['biolink_model']['url'].endswith('/v4.2.6-rc5')
    assert 'v4.2.6-rc5' in status['biolink_model']['download_url']


def test_conflations_can_be_overridden(monkeypatch):
    """CONFLATIONS is not detected from the data, so a differently-built index needs it
    set. Whitespace and empty entries are tolerated so a trailing comma isn't a bug."""
    monkeypatch.setenv('CONFLATIONS', ' GeneProtein , ,DrugChemical,')

    status = TestClient(app).get("/status").json()

    assert status['conflations'] == ['GeneProtein', 'DrugChemical']


def test_documented_status_example_matches_the_real_response():
    """documentation/API.md shows an example /status payload, which had drifted to a
    nameres_version three releases old.

    A stale patch or minor version in the example is cosmetic, so that only warns. A
    stale *major* version fails: a major release is exactly when someone should re-run
    this example and confirm the endpoint still behaves the way the docs describe.

    The field names are checked strictly either way -- that is what catches a field
    added to /status and never documented, which is how `conflations` was missed."""
    api_md = (Path(__file__).parent.parent / "documentation" / "API.md").read_text()
    # The first JSON block after the "### `/status`" heading is the example payload.
    status_section = api_md.split("### `/status`", 1)[1]
    example = json.loads(re.search(r"```json\n(.*?)```", status_section, re.DOTALL).group(1))

    documented = example["nameres_version"].lstrip("v")
    current = get_app_info()["version"]
    if documented != current:
        message = (
            f"documentation/API.md's /status example shows nameres_version "
            f"{documented}, but this build is {current}."
        )
        assert documented.split(".")[0] == current.split(".")[0], (
            message + " Refresh the example against a real instance for the new major version."
        )
        warnings.warn(message)

    actual = TestClient(app).get("/status").json()
    assert set(example) == set(actual), (
        "The documented /status example and the real response have different fields: "
        f"only in docs={sorted(set(example) - set(actual))}, "
        f"only in response={sorted(set(actual) - set(example))}"
    )
