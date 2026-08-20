"""The public feedback launch stays honest, safe, and executable."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "v2.0-reddit-launch.md"


def _flat() -> str:
    return " ".join(PLAN.read_text(encoding="utf-8").lower().split())


def test_reddit_launch_waits_for_the_real_release_gate():
    text = _flat()

    assert "actual release zip has passed" in text
    assert "final zip and sha-256 are visible" in text
    assert "fantasy-league-almanac-2.0.0.zip.sha256" in text
    assert "open the release link signed out" in text
    assert "issue chooser" in text
    assert "signed out" in text


def test_reddit_body_preserves_the_supported_boundary():
    text = _flat()

    for claim in (
        "windows + espn",
        "head-to-head points and season-long points",
        "head-to-head league needs at least one completed matchup",
        "rotisserie is not proven and fails closed",
        "cbs guided onboarding is not part of v2.0",
        "packaged sample mode and automatic cookie capture",
        "there is no almanac-hosted account, telemetry, analytics, payment, or maintainer database",
    ):
        assert claim in text


def test_reddit_body_explains_cookie_custody_without_requesting_secrets():
    text = _flat()

    assert "session cookies stay local and the league data is processed locally" in text
    assert "only the workbook you ask it to create is written to your own google drive" in text
    assert "link sharing happens only after you explicitly confirm it" in text
    assert "never asks for or sees your espn username, password, 2fa entry" in text
    assert "please do not put those cookies" in text
    assert "do not send credentials or raw league artifacts" in text


def test_reddit_launch_routes_feedback_and_preserves_the_linkedin_gate():
    text = _flat()

    assert "github issue chooser" in text
    assert "private-support route" in text
    assert "a non-kyle completion is not required before posting" in text
    assert "linkedin waits until there is at least one credible non-kyle completion" in text
    assert "safety/privacy" in text
    assert "material correctness" in text


def test_reddit_launch_requires_one_google_status_sentence():
    text = PLAN.read_text(encoding="utf-8")

    assert text.count("KEEP THIS SENTENCE ONLY IF") == 2
    assert "Delete the false Google-status paragraph" in text


def test_release_checklists_point_to_the_reddit_launch_kit():
    releasing = (ROOT / "RELEASING.md").read_text(encoding="utf-8")
    rehearsal = (ROOT / "docs" / "v2.0-clean-machine-rehearsal.md").read_text(
        encoding="utf-8"
    )

    assert "docs/v2.0-reddit-launch.md" in releasing
    assert "(v2.0-reddit-launch.md)" in rehearsal
