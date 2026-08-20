"""The local v2.0 cut stays aligned before the tag is created."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_root_carries_exactly_the_current_release_notes():
    notes = sorted(path.name for path in ROOT.glob("RELEASE NOTES v*.md"))

    assert notes == ["RELEASE NOTES v2.0.0.md"]
    assert (ROOT / "docs" / "releases" / "RELEASE NOTES v1.9.1.md").is_file()


def test_dbt_and_changelog_versions_match_the_cut():
    project = _read("dbt_league/dbt_project.yml")
    changelog = _read("CHANGELOG.md")

    assert re.search(r"^version: ['\"]2\.0\.0['\"]$", project, re.MULTILINE)
    assert "## [2.0.0] - 2026-08-21" in changelog
    assert "[RELEASE NOTES v2.0.0.md](RELEASE%20NOTES%20v2.0.0.md)" in changelog
    assert "docs/releases/RELEASE%20NOTES%20v1.9.1.md" in changelog


def test_readme_names_v2_as_current_before_prior_releases():
    readme = _read("README.md")

    assert "**v2.0.0** -- current, 2026-08-21" in readme
    assert readme.index("**v2.0.0**") < readme.index("**v1.9.1**")
    assert "[RELEASE NOTES v2.0.0.md](RELEASE%20NOTES%20v2.0.0.md)" in readme
    assert "docs/releases/RELEASE%20NOTES%20v1.9.1.md" in readme


def test_release_notes_preserve_scope_and_publication_gates():
    lowered = _read("RELEASE NOTES v2.0.0.md").lower()

    for claim in (
        "espn-first and windows-first",
        "cbs guided onboarding is an urgent follow",
        "packaged sample mode is explicitly deferred",
        "automated cookie acquisition",
        "pending branding alone is not the safety boundary",
        "actual oauth-bearing candidate zip",
        "reddit is intentionally the first broad stranger-validation event",
        "48–72 hour triage window",
    ):
        assert claim in lowered


def test_quickstart_is_launcher_first_and_manual_second():
    quickstart = _read("QUICKSTART.md").lower()

    assert quickstart.index("start_almanac.cmd") < quickstart.index(
        "advanced: manual setup fallback"
    )
    assert "you do not open a terminal or edit `.env`" in quickstart
    assert "configuration is still manual" not in quickstart
