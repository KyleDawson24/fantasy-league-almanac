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


# ---------------------------------------------------------------------------
# The Google path (MLB-209): what it no longer needs, and what it is not yet
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_no_live_doc_still_says_you_need_your_own_oauth_client(path):
    """THE REGRESSION THIS EXISTS FOR. Both docs told a stranger that a
    live Google Sheet 'needs your own OAuth client' / a GCP project. The
    tool ships its own identity now, and that sentence would send someone
    to do five console steps they do not need."""
    lowered = _flat(path)

    for claim in (
        "still needs your own oauth client",
        "needs a google oauth client",
        "optional and needs a google oauth client",
    ):
        assert claim not in lowered, f"{path.name}: {claim!r}"


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_both_docs_say_no_google_cloud_project_is_required(path):
    """Both phrasings accepted. The two docs address different readers --
    the Quickstart says it to you, SETUP says it about the route -- and
    forcing identical prose is how a doc test turns into something people
    game rather than satisfy. The CLAIM is what must be present."""
    lowered = _flat(path)

    assert ("no google cloud project" in lowered
            or "do not need a google cloud project" in lowered)
    assert "oauth client of your own" in lowered


def test_the_quickstart_gives_the_exact_command(path=None):
    text = _text(QUICKSTART)

    assert ("python output/generate_almanac_sheet.py --duckdb "
            "--new-public-workbook") in text


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_both_docs_state_the_scope_and_the_sharing_that_follows(path):
    """A stranger has to know two things before consenting: what is being
    asked for, and that the result becomes readable to anyone with the
    link. Neither may be left implied."""
    lowered = _flat(path)

    assert "drive.file" in lowered
    assert "anyone-with-the-link" in lowered
    assert "viewer" in lowered


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_both_docs_describe_first_run_consent_and_the_cached_token(path):
    lowered = _flat(path)

    assert "consent" in lowered
    assert "output/.sheets_public_oauth_token.json" in lowered


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_both_docs_are_honest_about_where_local_state_lives(path):
    """No encryption, no server, no upload. Ordinary files behind
    filesystem permissions and gitignore -- said plainly, because a
    reader who assumes more than that is being misled by omission."""
    lowered = _flat(path)

    assert "gitignore" in lowered
    assert "permissions" in lowered


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_both_docs_say_revocation_is_the_users_and_nothing_expires_for_them(path):
    lowered = _flat(path)

    assert "myaccount.google.com" in lowered
    assert ("nothing here expires on your behalf" in lowered
            or "since nothing here expires it for you" in lowered)


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_the_byo_client_route_is_described_as_an_advanced_override(path):
    lowered = _flat(path)

    assert "override" in lowered
    assert "not a prerequisite" in lowered


# ---------------------------------------------------------------------------
# What the Google path must NOT claim yet
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_no_live_doc_claims_the_shipped_identity_is_open_to_the_public(path):
    """OWNER RULING, and a release gate. The Cloud project is External
    and in Google's TESTING mode: only test users get through consent,
    grants expire in about a week, and the screen says unverified. Saying
    or implying otherwise would send a stranger at a door Google will
    shut in their face."""
    lowered = _flat(path)

    assert "testing" in lowered
    assert "test users" in lowered
    assert ("expire after about a week" in lowered
            or "expire after roughly a week" in lowered)


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_no_live_doc_promises_production_readiness(path):
    lowered = _flat(path)

    for claim in (
        "ready for anyone to use",
        "generally available",
        "verified by google",
        "anyone can now run this",
    ):
        assert claim not in lowered, f"{path.name}: {claim!r}"


def test_the_quickstart_still_does_not_claim_one_command_orchestration():
    """MLB-31/MLB-207 own chaining extract -> load -> dbt -> render. The
    Google step is one more command, not a replacement for the other
    five."""
    text = _text(QUICKSTART)

    assert "no single bootstrap command" in text
    assert "still five commands" in text


def test_the_quickstart_still_does_not_claim_cbs_stranger_support():
    lowered = _flat(QUICKSTART)

    assert "espn leagues only" in lowered
    assert "the local path is espn-only" in lowered


def test_the_roadmap_records_the_automation_as_landed_with_its_scope():
    """Marked done, but with the two corrections the work actually found --
    settings.matchupPeriods carries no membership, and the dates needed an
    anchor rather than a field."""
    text = _text(ROADMAP)

    assert "**DONE (MLB-235).**" in text
    assert "degenerate identity map" in text
    assert "regularSeasonStartDate" in text
    assert "unproven until a real payload" in text


# ---------------------------------------------------------------------------
# The credential ships in the release bundle, not in the repository
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_both_docs_send_consumers_to_the_release_bundle(path):
    """The Google identity is injected into the release archive and is
    deliberately absent from tracked source. A doc that tells a consumer
    to clone is telling them to install the one build that cannot do the
    thing the section promises."""
    lowered = _flat(path)

    assert "releases page" in lowered or "release archive" in lowered
    assert "fantasy-league-almanac-<version>.zip" in lowered


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_both_docs_call_a_clone_the_developer_path(path):
    """And say plainly that the live Sheets step stops in a clone, so a
    developer meets the fail-closed message as expected behaviour rather
    than as a bug report."""
    lowered = _flat(path)

    assert "developer path" in lowered
    assert "shipped no identity" in lowered


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_neither_doc_claims_a_clone_carries_the_identity(path):
    """The claim that was true for exactly one unpushed commit."""
    lowered = _flat(path)

    assert "this tool ships its own google identity" not in lowered
    assert "a plain clone receives" not in lowered
