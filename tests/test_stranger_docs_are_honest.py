"""The live stranger-facing docs, asserted (MLB-235 rung 4B-2).

WHY A TEST AND NOT A REVIEW. The Quickstart said "open
`matchup_schedule.csv` and add your season's week boundaries" for as long as
that was true, and the moment it stopped being true nothing in the repo
noticed. Documentation drift is invisible to every other test in this suite:
the code can be perfect and the first thing a new user reads can still send
them to a file they no longer need. These are the claims that must not
silently come back.

DELIBERATELY NARROW. This is not a prose linter. Each assertion below
corresponds to a specific promise the rung made about what a stranger is
told, and to a specific way that promise was wrong before -- or, in the
optimistic direction, to a claim that must NOT appear because it is not
true yet.

Archives and shipped release notes are out of scope by rule: they record
what was true when written and must not be rewritten.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

QUICKSTART = REPO_ROOT / "QUICKSTART.md"
SETUP = REPO_ROOT / "SETUP.md"
SEED_README = REPO_ROOT / "dbt_league" / "league_config" / "README.md"
ROADMAP = REPO_ROOT / "ROADMAP.md"

LIVE_DOCS = (QUICKSTART, SETUP, SEED_README)


def _text(path):
    return path.read_text(encoding="utf-8")


def _flat(path):
    """Whitespace-collapsed text.

    Markdown hard-wraps at ~72 columns, so a sentence a reader sees as one
    phrase is several lines in the file. Asserting on the raw text makes a
    test that fails when someone reflows a paragraph, which trains people to
    delete the test rather than fix the doc.
    """
    return " ".join(_text(path).split()).lower()


# ---------------------------------------------------------------------------
# The seed is not required any more
# ---------------------------------------------------------------------------
def test_the_quickstart_no_longer_tells_anyone_to_fill_in_the_schedule():
    """THE REGRESSION THIS EXISTS FOR. The old step 4 read: 'Open
    dbt_league/league_config/matchup_schedule.csv and add your season's week
    boundaries. This is the one file an ESPN league genuinely has to fill
    in.' Every clause of that is now false."""
    text = _text(QUICKSTART)

    assert "add your season's" not in text
    assert "genuinely has to fill in" not in text
    assert "one file an ESPN league" not in text


@pytest.mark.parametrize("path", LIVE_DOCS, ids=lambda p: p.name)
def test_no_live_doc_calls_the_schedule_seed_required(path):
    """Said in several phrasings over time, so several are checked. A doc
    that calls it required sends a stranger to do work the extract now does
    for them -- which is the opposite of what this rung was for."""
    lowered = _flat(path)

    for claim in (
        "matchup_schedule.csv` -- your week boundaries",
        "the one to start with",
        "every weekly surface is empty",
        "is the only thing that knows when your weeks",
    ):
        assert claim.lower() not in lowered, f"{path.name}: {claim!r}"


def test_the_setup_minimum_for_an_espn_league_is_no_files():
    assert "honest minimum for an espn league is now **no files at all**" in _flat(SETUP)


def test_the_seed_readme_moves_the_schedule_out_of_the_required_table():
    """The required table lists what a model reads DIRECTLY. The schedule is
    no longer one of those, and leaving it there would keep telling readers
    that leaving it empty means empty surfaces."""
    text = _text(SEED_README)

    required = text.split("**OPTIONAL")[0]
    assert "| `matchup_schedule.csv` |" not in required
    assert "used to head this table and no longer does" in text


# ---------------------------------------------------------------------------
# What replaced it, said plainly
# ---------------------------------------------------------------------------
def test_the_quickstart_explains_that_membership_is_captured_automatically():
    text = _flat(QUICKSTART)

    assert "asks espn which scoring periods belong to each matchup period" in text


def test_the_quickstart_explains_where_the_dates_come_from():
    """"Dates are unavailable" was the old, and now obsolete, story. A
    stranger has to be told the calendar is derived and from what, without
    being asked to type any of it."""
    text = _text(QUICKSTART).lower()

    assert "scoring periods are **days**" in text
    assert "plus\nn-1 days" in text or "plus n-1 days" in text
    assert "mlb stats api" in text
    assert "nobody types a calendar" in text


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_the_csv_is_described_as_optional_correction_metadata(path):
    lowered = _flat(path)

    assert "can now stay blank" in lowered or "can stay blank" in lowered


def test_setup_describes_the_seed_as_a_correction_and_label_surface():
    lowered = _flat(SETUP)

    assert "correction" in lowered and "override a" in lowered
    assert "matchup_period_overrides.csv" in lowered


# ---------------------------------------------------------------------------
# The three history spellings are different, and said to be
# ---------------------------------------------------------------------------
def test_the_quickstart_separates_the_three_capture_spellings():
    """`--year Y` means Y. `--all-seasons` means the registry range. Neither
    downloads historical box scores, and a reader who assumes otherwise will
    wait a long time for something that is not coming."""
    text = _flat(QUICKSTART)

    assert "`--matchup-schedule-only --year 2025`" in text
    assert "--all-seasons" in text
    assert "no historical box scores" in text


# ---------------------------------------------------------------------------
# What is NOT claimed
# ---------------------------------------------------------------------------
def test_season_long_points_and_roto_are_still_called_unproven():
    """Zero- and one-period shapes are ACCEPTED without fabrication. That is
    not the same as saying they work, and the docs must not blur the two."""
    text = _flat(QUICKSTART)

    assert "not proven" in text or "unproven" in text
    assert "rotisserie" in text


def test_the_quickstart_does_not_claim_the_espn_half_is_simply_done():
    """It said "The ESPN half is done and walked." MLB-207's bootstrap and
    MLB-209's Google delivery are not in this commit, so the flat claim
    would over-promise."""
    text = _text(QUICKSTART)

    assert "The ESPN half is done and walked." not in text
    assert "no single bootstrap command" in text


def test_the_roadmap_records_the_automation_as_landed_with_its_scope():
    """Marked done, but with the two corrections the work actually found --
    settings.matchupPeriods carries no membership, and the dates needed an
    anchor rather than a field."""
    text = _text(ROADMAP)

    assert "**DONE (MLB-235).**" in text
    assert "degenerate identity map" in text
    assert "regularSeasonStartDate" in text
    assert "unproven until a real payload" in text
