import json
from pathlib import Path

from sitewatch_agent import __version__


def test_current_agent_version_has_changelog_entry():
    root = Path(__file__).resolve().parents[1]
    changelog = json.loads((root / "CHANGELOG.json").read_text(encoding="utf-8"))
    releases = changelog.get("releases")
    assert isinstance(releases, list) and releases, "CHANGELOG.json must contain releases"
    entry = next((item for item in releases if item.get("version") == __version__), None)
    assert entry is not None, f"Agent version {__version__} is missing from CHANGELOG.json"
    assert entry.get("date")
    assert entry.get("title")
    assert entry.get("summary")
    assert isinstance(entry.get("changes"), list) and entry["changes"]
