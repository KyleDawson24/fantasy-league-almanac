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
ENV_EXAMPLE = REPO_ROOT / ".env.example"

LIVE_DOCS = (QUICKSTART, SETUP, SEED_README)

PUBLIC_ENTRYPOINT = "tools/create_public_almanac.py"


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


def _commands(path):
    """`_flat`, with path separators normalized to forward slashes.

    QUICKSTART is the Windows runbook, so it writes
    `tools\\create_public_almanac.py` -- what a reader actually copies into
    PowerShell. Other prose writes `tools/create_public_almanac.py`. Both
    name the same script, and a guard that cares which slash was typed is
    testing typography rather than truth.
    """
    return _flat(path).replace("\\", "/")


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
    text = _flat(QUICKSTART)

    assert "mlb stats api" in text
    assert "season start" in text
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
def test_season_long_points_is_supported_without_claiming_roto():
    """The measured type-5 path works; roto remains a different unknown."""
    text = _flat(QUICKSTART)

    assert "season-long points is supported" in text
    assert "middle of its first season" in text
    assert "rotisserie" in text
    assert "rotisserie remains unproven" in text
    assert "inventing weekly opponents" in text


def test_live_h2h_is_not_smuggled_into_the_season_points_fix():
    text = _flat(QUICKSTART)

    assert "unfinished current matchup" in text
    assert "separate enhancement" in text


def test_the_quickstart_does_not_claim_the_espn_half_is_simply_done():
    """It said "The ESPN half is done and walked." MLB-209's Google
    delivery has since shipped, but MLB-31/MLB-207's guided onboarding has
    not, so the flat claim would still over-promise.

    This used to prove the point by asserting the words "no single
    bootstrap command" were present. v1.9 ships exactly such a command,
    so that phrasing had to go -- the guard now asks for the limitation
    that is still real instead of the one that stopped being."""
    text = _text(QUICKSTART)
    lowered = _flat(QUICKSTART)

    assert "The ESPN half is done and walked." not in text
    assert "guided setup, but it is not a wizard yet" in lowered
    assert "form-driven setup is planned" in lowered


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
    """Copy-pasteable, interpreter and all.

    This used to require the literal "python tools/create_public_almanac.py".
    The runbook names the venv interpreter explicitly now -- see
    test_the_quickstart_does_not_require_activating_the_venv for why -- so
    the guard asks for the script in a command position behind that
    interpreter rather than for a bare `python` in front of it."""
    text = _text(QUICKSTART)

    assert _commands(QUICKSTART).count(PUBLIC_ENTRYPOINT) >= 1
    assert r".venv\Scripts\python.exe tools\create_public_almanac.py" in text


def test_the_quickstart_installs_with_the_venv_interpreter_too():
    """The install step has the same failure mode as the run step: `pip`
    off PATH is whichever pip the machine happens to have, and there may
    be none. `-m pip` through the venv's own interpreter is the one
    spelling that cannot install into the wrong place."""
    text = _text(QUICKSTART)

    assert r".\.venv\Scripts\python.exe -m pip install -r requirements.txt" in text


def test_the_quickstart_does_not_require_activating_the_venv():
    """THE REGRESSION THIS EXISTS FOR. Step 2 said `.venv\\Scripts\\activate`.
    On Windows PowerShell that resolves to Activate.ps1, and a default
    execution policy blocks it -- so the very first command of the public
    journey failed on the exact machine class that journey targets, and
    the documented recovery would have been `Set-ExecutionPolicy`.

    Naming the interpreter is the same environment with none of that: no
    policy change, and nothing to understand about how activation works.
    Prose may still MENTION activation as an option; what must not come
    back is an activation command standing on its own as a required step,
    so this matches whole command lines rather than searching the text."""
    lines = {line.strip().lower() for line in _text(QUICKSTART).splitlines()}

    for command in (
        r".venv\scripts\activate",
        r".\.venv\scripts\activate",
        r".venv\scripts\activate.ps1",
        r".\.venv\scripts\activate.ps1",
        "source .venv/bin/activate",
    ):
        assert command not in lines, f"QUICKSTART.md: {command!r}"


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
def test_no_live_doc_claims_branding_review_has_passed(path):
    """OWNER RULING, and a release gate. The Cloud project is now In
    Production: it is NOT restricted to a test-user list, and testing
    mode's week-long grant expiry does not apply to it. What is still
    outstanding is Google's BRANDING review, so Google may withhold the
    configured branding and show an unverified-app warning. The docs have
    to say that, because a stranger who meets that screen unwarned reads
    it as this tool being broken or shady.

    This guard used to assert the opposite text, word for word --
    "testing", "test users", "expire after about a week". Those were true
    and have stopped being true, so the guard MOVED WITH THE FACT rather
    than being deleted. A guard that still enforces a retired claim is
    worse than no guard at all: it fails the honest edit and passes the
    dishonest one, and the person who hits it learns to delete tests.
    """
    lowered = _flat(path)

    assert "in production" in lowered
    assert "branding" in lowered
    assert "unverified-app warning" in lowered

    # The retired restriction must not come back as prose.
    for claim in ("only test users", "turned away by google",
                  "google will stop everybody else"):
        assert claim not in lowered, f"{path.name}: {claim!r}"


# ---------------------------------------------------------------------------
# The runbook has to describe the journey that actually ships
# ---------------------------------------------------------------------------
# WHY THESE EXIST. At the v1.9 cut the Quickstart still walked a stranger
# through the RETIRED workflow: "run five commands", Snowflake as the
# default, a six-command manual pipeline as the primary path, and the
# Google workbook demoted to an optional step 6 afterwards. Every one of
# those had been superseded by tools/create_public_almanac.py, which owns
# extract -> parquet -> DuckDB -> dbt -> workbook in a single invocation.
# A runbook that contradicts the shipped entrypoint is worse than no
# runbook: it sends the first real stranger down a path the release does
# not support, and every step they hit will half-work.


def test_quickstart_makes_the_public_entrypoint_the_primary_run_step():
    """The one command is the product. It has to be presented as such."""
    lowered = _commands(QUICKSTART)

    assert PUBLIC_ENTRYPOINT in lowered


def test_quickstart_puts_the_one_command_ahead_of_the_manual_stages():
    """ORDERING IS THE CLAIM. The lower-level extract/load/dbt commands
    are real and still documented, but they are troubleshooting material
    now. If they appear BEFORE the entrypoint, the document is once again
    telling a stranger to run the pipeline by hand first -- which is the
    exact regression this guards."""
    lowered = _commands(QUICKSTART)

    assert lowered.index(PUBLIC_ENTRYPOINT) < lowered.index("extract/extract.py")


def test_quickstart_does_not_claim_there_is_no_bootstrap_command():
    """It said "there is no single bootstrap command and no guided fields
    file yet, so the five commands in step 5 are still five commands".
    Half of that stopped being true; the guided fields file did not."""
    lowered = _flat(QUICKSTART)

    for claim in (
        "no single bootstrap command",
        "run five commands",
        "still five commands",
    ):
        assert claim not in lowered, f"QUICKSTART.md: {claim!r}"


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_no_live_doc_calls_snowflake_required(path):
    """Snowflake is an advanced opt-in reached only by passing
    --advanced-snowflake. The release journey never touches it, and every
    extract the entrypoint runs passes --raw-target local explicitly."""
    lowered = _flat(path)

    for claim in (
        "the snowflake path still exists, is still the default",
        "a snowflake account**. free-tier",
        "requires a snowflake account",
    ):
        assert claim not in lowered, f"{path.name}: {claim!r}"

    assert "--advanced-snowflake" in lowered


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_no_live_doc_calls_a_google_cloud_project_required(path):
    """The release build ships its own identity. A Cloud project is only
    for the BYO-client override in section 10b."""
    lowered = _flat(path)

    for claim in (
        "you need a google cloud project",
        "requires a google cloud project",
        "a google cloud project is required",
    ):
        assert claim not in lowered, f"{path.name}: {claim!r}"


def test_quickstart_sends_release_users_to_the_zip_and_says_why():
    """A clone cannot exercise the shipped identity: the descriptor is
    empty in source on purpose, because a credential in public git history
    is reported to Google and cannot be removed afterwards. A stranger who
    clones and then hits that wall needs to have been told first."""
    lowered = _flat(QUICKSTART)

    assert "releases" in lowered
    assert "unzip" in lowered
    assert "git clone" in lowered
    assert "fantasy-league-almanac-<version>.zip" in lowered
    assert "handles the google sign-in setup for you" in lowered
    assert "source-code clone deliberately does not contain" in lowered


def test_quickstart_tells_the_reader_to_configure_the_season_bounds():
    """first_season/final_season are what the entrypoint asks the registry
    for. A wrong first_season is the most likely way a real run produces a
    short history, so the runbook has to name both fields and the file."""
    lowered = _commands(QUICKSTART)

    assert "config/leagues.yml" in lowered
    assert "first_season" in lowered
    assert "final_season" in lowered
    assert "default_league" in lowered


@pytest.mark.parametrize("path", LIVE_DOCS, ids=lambda p: p.name)
def test_no_live_doc_repeats_the_retired_cbs_dbt_build_failure(path):
    """`dbt build` used to trip assert_cbs_scoring_feed_matches_seed on an
    ESPN-only install, so the docs said to run `dbt run` instead. That is
    fixed -- the all-empty ESPN installation passes an unscoped build, and
    the public entrypoint deliberately runs `dbt build`. A doc still
    telling people to avoid it now contradicts the shipped command."""
    lowered = _flat(path)

    for claim in (
        "assert_cbs_scoring_feed_matches_seed",
        "`dbt run` rather than `dbt build`",
        "trips one cbs data test",
        "dbt run` and says why",
    ):
        assert claim not in lowered, f"{path.name}: {claim!r}"


def test_env_example_does_not_say_duckdb_cannot_skip_snowflake():
    """Both claims were true before v1.8 and are load-bearing now: the
    supported public command lands RAW locally as parquet and builds
    DuckDB from it."""
    lowered = " ".join(ENV_EXAMPLE.read_text(encoding="utf-8").split()).lower()

    for claim in (
        "nothing lands raw there",
        "not a way to skip snowflake",
        "rather than a way to skip",
    ):
        assert claim not in lowered, f".env.example: {claim!r}"


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


def test_the_quickstart_scopes_its_one_command_to_post_configuration():
    """v1.9 DOES chain extract -> parquet -> DuckDB -> dbt -> workbook in
    one invocation, so the old form of this guard -- asserting the words
    "no single bootstrap command" and "still five commands" were present --
    now enforces a retired claim. What must still not be claimed is that
    the INPUTS are handled: `.env` and the registry are hand-edited, and
    MLB-31/MLB-207 own fixing that."""
    lowered = _commands(QUICKSTART)

    assert PUBLIC_ENTRYPOINT in lowered
    assert "edit two supplied text files" in lowered
    assert "configuration is still manual" in lowered

    for claim in (
        "no configuration required",
        "nothing to configure",
        "zero configuration",
        "no setup required",
    ):
        assert claim not in lowered, f"QUICKSTART.md: {claim!r}"


def test_the_quickstart_still_does_not_claim_cbs_stranger_support():
    lowered = _flat(QUICKSTART)

    assert "espn leagues only" in lowered
    assert "no scripted setup" in lowered
    assert "not part of this journey" in lowered


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
    assert ("shipped no identity" in lowered
            or "does not contain the google sign-in configuration" in lowered)


def test_quickstart_contains_no_internal_ticket_shorthand():
    """A stranger cannot interpret an internal tracker key. Future work is
    described by outcome and release timing, not by PM database ids."""
    lowered = _flat(QUICKSTART)

    assert "mlb-" not in lowered


def test_quickstart_limits_registry_edits_to_three_values():
    lowered = _flat(QUICKSTART)

    assert "change only these three values" in lowered
    for name in ("display_name", "first_season", "final_season"):
        assert name in lowered
    assert "entire `sinks` section exactly as supplied" in lowered
    assert "do not put your league id or cookies anywhere in this file" in lowered


def test_release_env_template_is_blank_and_quickstart_first():
    """A copied template must not look preconfigured for Snowflake or ask
    a new user whether placeholder strings should be retained or erased."""
    text = _text(ENV_EXAMPLE)
    lowered = _flat(ENV_EXAMPLE)

    for line in (
        "LEAGUE_ID=", "ESPN_S2=", "SWID=",
        "SNOWFLAKE_ACCOUNT=", "SNOWFLAKE_USER=",
        "SNOWFLAKE_DATABASE=", "SNOWFLAKE_SCHEMA=",
        "SNOWFLAKE_WAREHOUSE=", "SNOWFLAKE_PRIVATE_KEY_PATH=",
        "GOOGLE_OAUTH_CLIENT_PATH=", "GOOGLE_PUBLIC_OAUTH_CLIENT_PATH=",
        "SHEETS_DEV_ID=", "SHEETS_PROD_ID=",
    ):
        assert line in text.splitlines()
    assert "leave this entire section blank when following quickstart.md" in lowered
    assert "public command requires both" in lowered


def test_quickstart_does_not_tell_users_to_run_the_app_as_administrator():
    lowered = _flat(QUICKSTART)

    assert "almanac commands" in lowered
    assert "do not need administrator access" in lowered


@pytest.mark.parametrize("path", (QUICKSTART, SETUP), ids=lambda p: p.name)
def test_neither_doc_claims_a_clone_carries_the_identity(path):
    """The claim that was true for exactly one unpushed commit."""
    lowered = _flat(path)

    assert "this tool ships its own google identity" not in lowered
    assert "a plain clone receives" not in lowered
