"""The local v2.0.1 cut stays aligned before the tag is created."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_root_carries_exactly_the_current_release_notes():
    notes = sorted(path.name for path in ROOT.glob("RELEASE NOTES v*.md"))

    assert notes == ["RELEASE NOTES v2.0.1.md"]
    assert (ROOT / "docs" / "releases" / "RELEASE NOTES v2.0.0.md").is_file()
    assert (ROOT / "docs" / "releases" / "RELEASE NOTES v1.9.1.md").is_file()


def test_dbt_and_changelog_versions_match_the_cut():
    project = _read("dbt_league/dbt_project.yml")
    changelog = _read("CHANGELOG.md")

    assert re.search(r"^version: ['\"]2\.0\.1['\"]$", project, re.MULTILINE)
    assert "## [2.0.1] - 2026-08-20" in changelog
    assert "[RELEASE NOTES v2.0.1.md](RELEASE%20NOTES%20v2.0.1.md)" in changelog
    assert "## [2.0.0] - 2026-08-20" in changelog
    assert "docs/releases/RELEASE%20NOTES%20v2.0.0.md" in changelog
    assert "docs/releases/RELEASE%20NOTES%20v1.9.1.md" in changelog


def test_readme_names_v2_patch_as_current_before_prior_releases():
    readme = _read("README.md")

    assert "**v2.0.1** -- current, 2026-08-20" in readme
    assert readme.index("**v2.0.1**") < readme.index("**v2.0.0**")
    assert "[RELEASE NOTES v2.0.1.md](RELEASE%20NOTES%20v2.0.1.md)" in readme
    assert "docs/releases/RELEASE%20NOTES%20v2.0.0.md" in readme
    assert "docs/releases/RELEASE%20NOTES%20v1.9.1.md" in readme


def test_patch_notes_preserve_runtime_and_private_process_boundaries():
    lowered = _read("RELEASE NOTES v2.0.1.md").lower()

    for claim in (
        "release-hygiene and documentation patch",
        "guided espn-to-google-workbook journey",
        "strict pre-push guard still refuses",
        "v2.0.0 tag and git history are intentionally not rewritten",
        "14 csv templates",
        "no model reads it today",
        "no extraction, transform, workbook, credential, oauth, sharing",
    ):
        assert claim in lowered


def test_quickstart_is_launcher_first_and_manual_second():
    quickstart = _read("QUICKSTART.md").lower()

    assert quickstart.index("start_almanac.cmd") < quickstart.index(
        "advanced: manual setup fallback"
    )
    assert "you do not open a terminal or edit `.env`" in quickstart
    assert "configuration is still manual" not in quickstart
    assert "one league per extracted folder" in quickstart
    assert "fresh copy of the release zip into a different folder" in quickstart


def test_quickstart_examples_name_the_current_patch_folder():
    quickstart = _read("QUICKSTART.md")

    assert "fantasy-league-almanac-2.0.1" in quickstart
    assert "fantasy-league-almanac-2.0.0" not in quickstart
