"""Maintainer working state stays out of the current public product tree."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_internal_working_files_are_not_in_the_public_tree():
    for relative in (
        "RELEASING.md",
        "docs/v2.0-reddit-launch.md",
        "tools/pii_dispositions.csv",
    ):
        assert not (ROOT / relative).exists(), relative


def test_pii_review_state_lives_with_the_private_inventory():
    spec = importlib.util.spec_from_file_location(
        "check_pii_public_hygiene", ROOT / "tools" / "check_pii.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert Path(module.LEDGER) == (
        ROOT / "archives" / "anonymization" / "pii_dispositions.csv"
    )
    assert "archives/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_public_override_docs_name_the_guided_boundary_and_dead_worklist():
    text = " ".join(
        (ROOT / "dbt_league" / "league_config" / "README.md")
        .read_text(encoding="utf-8")
        .lower()
        .split()
    )

    assert "guided espn setup does not ask for or populate them" in text
    assert "matchup_period_overrides.csv" in text
    assert "no model reads it today" in text
