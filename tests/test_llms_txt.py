"""Tests for the agent instructions served at /llms.txt.

None of these need Solr: the route only reads a file off disk.
"""

import re

import yaml
from fastapi.testclient import TestClient

from api.server import SKILL_PATH, app

client = TestClient(app)


def test_skill_file_exists():
    """If this fails, /llms.txt 404s everywhere."""
    assert SKILL_PATH.is_file(), f"No skill file at {SKILL_PATH}"


def test_skill_frontmatter_is_valid():
    """Claude Code silently ignores a skill whose frontmatter is malformed or whose name does
    not match its directory, so that failure mode is invisible without a test."""
    frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", SKILL_PATH.read_text(), re.DOTALL)
    assert frontmatter, "SKILL.md must open with a YAML frontmatter block"

    parsed = yaml.safe_load(frontmatter.group(1))
    assert set(parsed) == {"name", "description"}, f"Unexpected frontmatter keys: {sorted(parsed)}"
    assert parsed["name"] == SKILL_PATH.parent.name == "nameres"
    # The description is what the model matches on to decide whether to load the skill, so it
    # needs to name trigger situations rather than just being a title.
    assert len(parsed["description"]) > 100


def test_llms_txt_is_served_as_plain_text():
    response = client.get("/llms.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert len(response.text) > 500


def test_llms_txt_strips_frontmatter():
    # Check the premise first: if SKILL.md stopped having frontmatter on purpose, this test and
    # the stripping in server.llms_txt() are both pointless and should go.
    assert SKILL_PATH.read_text().startswith("---"), "SKILL.md no longer starts with frontmatter"

    body = client.get("/llms.txt").text

    assert not body.startswith("---")
    assert "name: nameres" not in body
    assert body.startswith("# ")


def test_llms_txt_404s_when_the_skill_is_missing(monkeypatch, tmp_path):
    """Only reachable in a mis-built image -- which is exactly why it needs a test, since that is
    the one failure mode that passes locally and fails in every deployment."""
    monkeypatch.setattr("api.server.SKILL_PATH", tmp_path / "absent.md")

    response = client.get("/llms.txt")

    assert response.status_code == 404
    assert "github.com/NCATSTranslator/NameResolution" in response.json()["detail"]


def test_llms_txt_keeps_the_traps():
    """The specific facts an agent gets wrong without being told. If a rewrite drops one of these,
    that is a regression rather than an edit."""
    body = client.get("/llms.txt").text

    for trap in ["bulk-lookup", "biolink_types", "autocomplete", "only_taxa", "conflat"]:
        assert trap in body, f"The skill no longer mentions {trap!r}"


def test_skill_links_are_absolute():
    """SKILL.md is served raw at /llms.txt and pasted into other agents, where a relative link
    resolves against the API host and 404s. test_docs_links.py cannot catch this -- a relative
    link that resolves on disk passes there."""
    relative = [
        target for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", SKILL_PATH.read_text())
        if not target.startswith(("https://", "http://", "#"))
    ]
    assert not relative, f"Links in SKILL.md must be absolute URLs: {relative}"

