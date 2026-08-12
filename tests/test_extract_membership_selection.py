"""Box-score selection driven by ESPN's own membership (MLB-235, rung 4B-1).

WHAT THIS FILE IS FOR. Rung 2 proved the mMatchupScore snapshot could be
CAPTURED without the seed; this proves the extract SELECTS from it. Those are
different claims, and only the second one lets a stranger with an empty
`matchup_schedule.csv` pull a box score. So every test here drives `run()` --
the real CLI body, over a real argparse Namespace -- rather than a helper
that happens to sit next to it.

THE SEED IS BOOBY-TRAPPED IN EVERY TEST, not merely absent. `load_schedule`
and `get_scoring_periods` are replaced with functions that raise, so a path
that reads the CSV fails loudly instead of quietly succeeding on Kyle's real
file. That is what makes the mutation check meaningful: putting
`get_scoring_periods()` back into `extract_matchup_period` fails these tests
rather than passing on a developer machine and failing on a clone.

PURE. No network (the one fetch is substituted), no warehouse (the sinks are
recording fakes and, for the Snowflake seam, the real adapter over a fake
connection), no REAL credentials, no seed.

"No real credentials" is a stronger claim than "no credentials", and the
difference is the whole of MLB-235's CI correction. These tests drive `run()`,
and `run()` validates the registry's declared credentials before it opens a
sink or fetches anything -- correctly, because the real thing is about to talk
to ESPN. So the environment has to satisfy that check, and
`synthetic_espn_credentials` below satisfies it with values that could not
possibly authenticate. It does not merely COVER a missing .env; it OVERRIDES a
present one, so the run under test sees the same environment on a maintainer's
machine and on a bare clone. See that fixture for why this file was green on
Kyle's laptop and red on CI from the day it landed.

EVERY PAYLOAD IS SYNTHETIC. Shapes match what MLB-235 recorded on the wire --
`schedule[]` keyed by `matchupPeriodId`, membership as the KEYS of
`pointsByScoringPeriod`, `status.currentMatchupPeriod` / `latestScoringPeriod`
/ `finalScoringPeriod` -- but the numbers are chosen to exercise boundaries.
Where a test asserts something about the REAL league's shape it says so and
uses the measured values (2026: current 19, latest 140, closed 1..18 with
period 15 ending at scoring period 117 and 16 at 124).
"""

import importlib.util
import os
import sys
from argparse import Namespace
from datetime import date, datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]

# load_dotenv() first, then setdefault, so a fresh clone imports and Kyle's
# real id never leaks into the session -- see test_extract_club_of_game.py for
# the full account of why the order matters.
load_dotenv()
os.environ.setdefault("LEAGUE_ID", "0")

_spec = importlib.util.spec_from_file_location(
    "extract_membership_selection_under_test",
    _REPO_ROOT / "extract" / "extract.py")
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)

sys.path.insert(0, str(_REPO_ROOT / "extract"))
from matchup_membership import (  # noqa: E402
    RECENT, SETTLED, UNKNOWN, classify_recency, parse_matchup_membership)

# Held before the autouse fixture replaces them with booby traps, so the one
# test that wants the REAL seed readers (against a genuinely empty CSV) can
# put them back.
_REAL_LOAD_SCHEDULE = extract.load_schedule
_REAL_GET_SCORING_PERIODS = extract.get_scoring_periods

LEAGUE = "espn-main"
SEASON = 2026
# The season under test is the current one in every default-selection case,
# so `date.today()` cannot decide the answer. Tests that care about the
# historical branch say so.
TODAY = date(SEASON, 8, 11)

TEAM_ID = 987654

# The credentials `run()` demands, answered with values that are self-evidently
# not credentials. Written as PAIRS rather than a dict literal on purpose:
# tools/check_pii.py builds its identifier pattern from the registry's own
# declared env names, so `"LEAGUE_ID": "0"` -- a key, a colon, a value -- is
# exactly the shape that scanner is looking for. A two-tuple has no colon.
#
# The values are deliberately unusable. A real ESPN_S2 is a long URL-encoded
# blob and a real SWID is a braced GUID; nothing here would survive contact
# with espn.com, which is the point -- the fetch is substituted, so what these
# have to satisfy is `require_credentials()` and nothing else.
_SYNTHETIC_CREDENTIALS = (
    ("ESPN_S2", "synthetic-espn-s2-for-tests-not-a-real-cookie"),
    ("SWID", "{SYNTHETIC-SWID-FOR-TESTS-NOT-A-REAL-GUID}"),
    ("LEAGUE_ID", "0"),
)


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------
def _side(scoring_periods):
    return {"teamId": TEAM_ID,
            "pointsByScoringPeriod": {str(sp): 0.0 for sp in scoring_periods}}


def _matchup(matchup_period, scoring_periods):
    return {"matchupPeriodId": matchup_period,
            "home": _side(scoring_periods),
            "away": _side(scoring_periods)}


def _payload(period_lengths=(7, 7, 7, 7), *, current=None, season_year=SEASON,
             latest=None, final=None, first_scoring_period=1):
    """Consecutive periods of the given lengths, plus the status block.

    `current` defaults to one past the last period built, i.e. every built
    period is closed and none is in flight.
    """
    schedule = []
    sp = first_scoring_period
    for index, length in enumerate(period_lengths, start=1):
        schedule.append(_matchup(index, range(sp, sp + length)))
        sp += length

    current = len(period_lengths) + 1 if current is None else current
    status = {"currentMatchupPeriod": current, "isActive": True,
              "currentLeagueType": 0, "createdAsLeagueType": 2}
    if latest is not None:
        status["latestScoringPeriod"] = latest
    if final is not None:
        status["finalScoringPeriod"] = final
    return {"seasonId": season_year, "status": status, "schedule": schedule}


def _parse(payload, season_year=SEASON):
    return parse_matchup_membership(
        payload, league_key=LEAGUE, season_year=season_year)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _RecordingSink:
    """Records every write and every guard read, and does nothing else."""

    name = "recording"

    def __init__(self, loaded=None, backfill_raises=None):
        self.loaded = loaded or {}
        self.backfill_raises = backfill_raises
        self.matchup_schedules = []
        self.season_calendars = []
        self.box_scores = []
        self.backfills = []
        self.other_writes = []

    def describe(self):
        return self.name

    def loaded_box_score_periods(self, year, league_key):
        return dict(self.loaded)

    def write_matchup_schedule(self, payload, year, league_key):
        self.matchup_schedules.append((payload, year, league_key))

    def write_season_calendar(self, payload, year, league_key):
        self.season_calendars.append((payload, year, league_key))

    def write_box_scores(self, records, matchup_period, year, league_key):
        self.box_scores.append((matchup_period, [r["scoring_period"]
                                                 for r in records]))

    def backfill_club_of_game(self, year, league_key, periods):
        if self.backfill_raises:
            raise self.backfill_raises
        self.backfills.append((year, league_key, list(periods)))

    def __getattr__(self, name):
        """Any other write records itself instead of exploding.

        The refusal tests assert `other_writes == []`, so a settings or
        standings write reaching the sink has to be OBSERVABLE rather than an
        AttributeError that reads like a test bug.
        """
        if name.startswith("write_"):
            def _record(*args, **kwargs):
                self.other_writes.append(name)
            return _record
        raise AttributeError(name)


def _exit_status(excinfo):
    """The process status a SystemExit actually produces.

    `SystemExit("a message")` carries the STRING as `.code`, and CPython
    prints it and exits 1. Asserting `.code != 0` would therefore pass on a
    bare `SystemExit(0)` too, which is the thing these tests exist to catch,
    so the interpreter's own rule is reproduced here instead.
    """
    code = excinfo.value.code
    if code is None:
        return 0
    return code if isinstance(code, int) else 1


def _args(**overrides):
    """A default Namespace, i.e. the bare `py extract/extract.py` invocation."""
    base = dict(year=SEASON, periods=[], all=False, include_settings=False,
                settings_only=False, no_standings=True,
                include_matchup_schedule=False, matchup_schedule_only=False,
                all_seasons=False,
                include_transactions=False, transactions_only=False,
                backfill_club_of_game=False,
                overwrite_day_accurate_history=False, league=None,
                raw_target="local", parquet_dir=None)
    base.update(overrides)
    return Namespace(**base)


@pytest.fixture(autouse=True)
def synthetic_espn_credentials(monkeypatch):
    """Satisfy `run()`'s credential gate from the test, not from a .env.

    WHY THIS EXISTS. `run()` calls `target_league.require_credentials()` as
    its third statement, before the sink opens and long before the first
    fetch. That check reads `os.environ` directly, through the registry -- it
    is not behind `connect_espn` or `fetch_league_payload`, so substituting
    those (which `no_seed_no_network` does) cannot reach it. Every test in
    this file therefore needed ESPN_S2 and SWID to be SET in the process
    before it could exercise anything at all.

    WHY NOBODY NOTICED. The module-level `load_dotenv()` above -- there for an
    unrelated and still-good reason, see the LEAGUE_ID note -- loaded Kyle's
    real .env into the pytest process at import. On his machine the gate was
    satisfied by his actual ESPN cookies and every test passed. On a clean
    checkout there is no .env, and all 58 exited on the same line before
    reaching a single assertion. Green locally, red on CI, for the most
    misleading possible reason: the suite was reading a file that is not, and
    must never be, part of the repo.

    WHY setenv AND NOT setdefault. `setdefault` would leave the maintainer's
    run using his real cookies and CI using synthetic ones -- two different
    environments, which is the bug rather than the fix. `monkeypatch.setenv`
    overrides, so both machines run the same test. It also unwinds at
    teardown, so nothing leaks into the rest of the session (the hazard the
    LEAGUE_ID note records the hard way).

    SCOPE. Autouse HERE, in the one module that drives `run()`. Deliberately
    not in tests/conftest.py: `test_league_registry.py` proves that missing
    credentials are reported, and a global fixture would quietly delete that
    coverage by making them never missing.
    """
    for name, value in _SYNTHETIC_CREDENTIALS:
        monkeypatch.setenv(name, value)


@pytest.fixture(autouse=True)
def no_seed_no_network(monkeypatch):
    """The seed raises, the network is absent, the clock is pinned.

    Booby-trapping `load_schedule`/`get_scoring_periods` rather than pointing
    them at an empty file is deliberate: an empty CSV makes `load_schedule`
    raise ValueError, which a bare `except` somewhere could swallow into a
    plausible-looking default. A bespoke AssertionError cannot be mistaken
    for anything but the circular dependency coming back.
    """
    def _seed_is_gone(*args, **kwargs):
        raise AssertionError(
            "the box-score path read matchup_schedule.csv, which is the "
            "circular dependency MLB-235 removed")

    monkeypatch.setattr(extract, "load_schedule", _seed_is_gone)
    monkeypatch.setattr(extract, "get_scoring_periods", _seed_is_gone)
    monkeypatch.setattr(extract, "fetch_league_payload", _seed_is_gone)
    monkeypatch.setattr(extract, "connect_espn", lambda year: object())
    monkeypatch.setattr(extract, "date", _FrozenDate)
    # MLB's public calendar is a second, independent request (rung 4B-2).
    # Substituted rather than left live so no test reaches the network; tests
    # that care about it override this.
    monkeypatch.setattr(
        extract, "fetch_season_calendar",
        lambda year: {"seasons": [{
            "seasonId": str(year),
            "regularSeasonStartDate": f"{year}-03-25",
            "regularSeasonEndDate": f"{year}-09-27"}]})


class _FrozenDate(date):
    """`date` with today() pinned, so selection cannot depend on the clock."""

    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture
def run_extract(monkeypatch):
    """Drive `run()` against a payload, counting mMatchupScore acquisitions."""
    def _run(payload, sink=None, *, serialized=None, **arg_overrides):
        sink = sink if sink is not None else _RecordingSink()
        asked = []

        def _fetch(year, views):
            asked.append((year, tuple(views)))
            return payload

        monkeypatch.setattr(extract, "fetch_league_payload", _fetch)
        monkeypatch.setattr(
            extract, "serialize_box_scores",
            lambda league, sp, mp: (serialized or {"scoring_period": sp}))

        from contextlib import contextmanager

        @contextmanager
        def _open(raw_target, parquet_dir=None):
            yield sink

        monkeypatch.setattr(extract, "open_sink", _open)
        code = extract.run(_args(**arg_overrides))
        return sink, asked, code

    return _run


# ===========================================================================
# 0. The credential boundary: this file runs on a bare clone
# ===========================================================================
# The regression for MLB-235's CI correction. Section 1 below is the headline
# claim -- box scores with no seed -- and it was untestable anywhere but Kyle's
# laptop, because `run()` refused before selection on a checkout with no .env.
# These three hold that door open from both sides.
def test_the_credentials_under_test_did_not_come_from_a_dotenv():
    """THE REGRESSION. Every credential `run()` demands is the synthetic
    sentinel from this module, not whatever a .env happens to hold.

    This is the assertion that fails on the old arrangement in the ONE
    environment that used to look fine. On a bare clone the previous code
    failed everywhere, loudly; on Kyle's machine it passed everywhere, which
    is why it survived. Here his .env is the adversary: if these values ever
    revert to being sourced from it, the ones read back are his real cookies
    and this test says so on the machine where nothing else would.
    """
    from config.league_registry import get_league

    declared = set(get_league(LEAGUE).credential_env)
    supplied = {name for name, _value in _SYNTHETIC_CREDENTIALS}

    # Rot guard: a credential added to leagues.yml with no synthetic answer
    # here would take this file down on CI only, which is the failure mode
    # the whole correction exists to remove.
    assert declared == supplied, (
        "config/leagues.yml and _SYNTHETIC_CREDENTIALS disagree about what "
        f"'{LEAGUE}' requires; declared-not-supplied={sorted(declared - supplied)}, "
        f"supplied-not-declared={sorted(supplied - declared)}")

    for name, value in _SYNTHETIC_CREDENTIALS:
        assert os.environ[name] == value, (
            f"{name} is not this module's synthetic value, so the run under "
            f"test is reading real credentials from somewhere -- almost "
            f"certainly the maintainer's .env, which CI does not have.")


def test_the_headline_path_runs_on_those_synthetic_credentials(run_extract):
    """And the sentinels are sufficient: with nothing real in the
    environment, the box-score path still reaches selection and extracts.

    Section 1 proves this too, but only incidentally. Stated here it is the
    claim itself -- a stranger's clone gets box scores -- rather than a
    precondition some other assertion happens to need.
    """
    payload = _payload((7, 7, 7, 7), current=5, latest=28)

    sink, _asked, code = run_extract(payload)

    assert code == 0
    assert [mp for mp, _sps in sink.box_scores] == [1, 2, 3, 4]


def test_genuinely_missing_credentials_still_refuse_the_run(monkeypatch):
    """THE OTHER SIDE OF THE DOOR, and the reason the fixture above is scoped
    to this file rather than to conftest.

    `require_credentials()` is not being softened, and a private-league run
    with no cookies must still stop before it does anything. Unset them and
    the refusal has to come straight back -- naming the variables, naming the
    file that declares them, and naming where they belong. Asserted at the
    CLI boundary, where the regression actually happened; the unit-level
    version lives in tests/test_league_registry.py.
    """
    monkeypatch.delenv("ESPN_S2", raising=False)
    monkeypatch.delenv("SWID", raising=False)

    with pytest.raises(SystemExit) as exc:
        extract.run(_args())

    message = str(exc.value)
    assert "[league registry]" in message
    assert "ESPN_S2" in message and "SWID" in message
    assert "config/leagues.yml" in message
    assert ".env" in message


# ===========================================================================
# 1. The normal box-score path reaches selection with no seed at all
# ===========================================================================
def test_the_normal_run_selects_and_extracts_with_no_schedule_seed(run_extract):
    """THE HEADLINE. Default invocation, seed booby-trapped, and box scores
    come out anyway -- periods and their scoring-period ids both read from
    ESPN's document."""
    payload = _payload((7, 7, 7, 7), current=5, latest=28)

    sink, _asked, code = run_extract(payload)

    assert code == 0
    assert [mp for mp, _sps in sink.box_scores] == [1, 2, 3, 4]
    assert sink.box_scores[0][1] == list(range(1, 8))
    assert sink.box_scores[3][1] == list(range(22, 29))


def test_the_normal_run_works_against_a_genuinely_empty_seed(
        run_extract, tmp_path, monkeypatch):
    """THE STRANGER CONDITION, literally.

    Every other test in this file booby-traps the seed readers, which proves
    they are not CALLED. This one leaves them callable and points SEED_PATH
    at a header-only CSV -- exactly what a fresh clone has, since the
    committed league_config templates are blank. Before this rung that file
    made `load_schedule` raise ValueError and took every box-score path down
    with it; now it is simply not consulted.

    This is the test mutation check A is aimed at: restoring
    `get_scoring_periods()` inside `extract_matchup_period` fails it with the
    real-world error a stranger would have seen.
    """
    empty_seed = tmp_path / "matchup_schedule.csv"
    empty_seed.write_text(
        "league_key,season_year,matchup_period,start_date,end_date\n")
    monkeypatch.setattr(extract, "SEED_PATH", str(empty_seed))
    monkeypatch.setattr(extract, "load_schedule", _REAL_LOAD_SCHEDULE)
    monkeypatch.setattr(extract, "get_scoring_periods", _REAL_GET_SCORING_PERIODS)

    # The seed really is unusable -- if this stopped raising, the test below
    # would prove nothing.
    with pytest.raises(ValueError, match="No schedule found"):
        extract.load_schedule(SEASON)

    payload = _payload((7, 7, 7), current=4, latest=21)
    sink, _asked, code = run_extract(payload)

    assert code == 0
    assert dict(sink.box_scores) == {1: list(range(1, 8)),
                                     2: list(range(8, 15)),
                                     3: list(range(15, 22))}


def test_the_scoring_periods_written_are_espns_not_a_seven_day_assumption(run_extract):
    """A 13-day opening week and a 14-day All-Star period are the real shape
    of both seasons on file. Nothing in the path may round them to a week."""
    payload = _payload((13, 7, 14), current=4, latest=34)

    sink, _asked, _code = run_extract(payload)

    lengths = {mp: len(sps) for mp, sps in sink.box_scores}
    assert lengths == {1: 13, 2: 7, 3: 14}
    assert sink.box_scores[2][1] == list(range(21, 35))


# ===========================================================================
# 2. No selection path touches the seed
# ===========================================================================
@pytest.mark.parametrize("mode", [
    {},                                  # default / recent
    {"all": True},                       # --all
    {"periods": [2, 3]},                 # explicit
])
def test_no_selection_path_reads_the_seed(run_extract, mode):
    """The autouse fixture makes `load_schedule`/`get_scoring_periods` raise,
    so reaching either is a failure rather than a silent success."""
    payload = _payload((7, 7, 7, 7), current=5, latest=28)

    sink, _asked, code = run_extract(payload, **mode)

    assert code == 0
    assert sink.box_scores, "nothing was extracted, so nothing was proven"


def test_the_backfill_path_does_not_read_the_seed(run_extract):
    payload = _payload((7, 7, 7, 7), current=5, latest=28)
    sink = _RecordingSink()

    sink, _asked, code = run_extract(
        payload, sink, all=True, backfill_club_of_game=True,
        raw_target="snowflake")

    assert code == 0
    assert sink.backfills == [(SEASON, LEAGUE, [1, 2, 3, 4])]


# ===========================================================================
# 3. One acquisition, and the SAME object feeds RAW and selection
# ===========================================================================
def test_one_season_costs_exactly_one_matchup_score_request(run_extract):
    payload = _payload((7, 7), current=3, latest=14)

    _sink, asked, _code = run_extract(payload)

    matchup_score = [call for call in asked if call[1] == ("mMatchupScore",)]
    assert matchup_score == [(SEASON, ("mMatchupScore",))], (
        "the season's membership was fetched a number of times other than one"
    )


def test_schedule_only_with_a_year_means_that_year_and_one_request(run_extract):
    """`--matchup-schedule-only --year Y` is Y ONLY. It is the cheapest
    history-capture spelling, not an implicit backfill of every season the
    registry knows about."""
    payload = _payload((7, 7), current=3, latest=14, season_year=2025)

    sink, asked, code = run_extract(
        payload, matchup_schedule_only=True, year=2025)

    assert code == 0
    assert asked == [(2025, ("mMatchupScore",))]
    assert [year for _p, year, _k in sink.matchup_schedules] == [2025]
    assert sink.box_scores == [] and sink.other_writes == []


def test_the_stored_snapshot_and_the_selection_are_the_same_object(run_extract):
    """Not "equal": IDENTICAL. A run that fetched twice could store one
    week's schedule and extract against another's, and nothing on either row
    would say so."""
    payload = _payload((7, 7, 7), current=4, latest=21)
    captured = {}

    original = extract.acquire_matchup_membership

    def _spy(sink, year, league_key, payload=None):
        result = original(sink, year, league_key, payload)
        captured["result"] = result
        return result

    extract.acquire_matchup_membership = _spy
    try:
        sink, _asked, _code = run_extract(payload)
    finally:
        extract.acquire_matchup_membership = original

    stored = sink.matchup_schedules[0][0]
    assert stored is captured["result"].snapshot
    # And the parse came out of that same snapshot, not a re-fetch.
    assert captured["result"].parse.closed_periods == (1, 2, 3)
    assert [mp for mp, _sps in sink.box_scores] == [1, 2, 3]


def test_a_settings_run_does_not_pay_for_membership(run_extract):
    """--settings-only is not an excuse to add a request everywhere."""
    payload = _payload((7, 7), current=3, latest=14)
    sink = _RecordingSink()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(extract, "extract_league_settings",
                   lambda sink, year, key: sink.other_writes.append("settings"))
    try:
        sink, asked, code = run_extract(payload, sink, settings_only=True)
    finally:
        monkey.undo()

    assert code == 0
    assert asked == [], "a settings-only run fetched the matchup schedule"
    assert sink.matchup_schedules == []


def test_the_compatibility_flag_still_adds_membership_to_a_settings_run(run_extract):
    payload = _payload((7, 7), current=3, latest=14)
    sink = _RecordingSink()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(extract, "extract_league_settings",
                   lambda sink, year, key: None)
    try:
        sink, asked, _code = run_extract(
            payload, sink, settings_only=True, include_matchup_schedule=True)
    finally:
        monkey.undo()

    assert [call[1] for call in asked] == [("mMatchupScore",)]
    assert len(sink.matchup_schedules) == 1


# ===========================================================================
# 4. Explicit closed periods get exactly their derived scoring periods
# ===========================================================================
def test_explicit_periods_receive_their_derived_scoring_periods(run_extract):
    payload = _payload((13, 7, 14, 7), current=5, latest=41)

    sink, _asked, _code = run_extract(payload, periods=[1, 3])

    assert dict(sink.box_scores) == {
        1: list(range(1, 14)),      # the long opening period
        3: list(range(21, 35)),     # the 14-day All-Star-shaped period
    }


def test_explicit_period_order_is_the_order_requested(run_extract):
    payload = _payload((7, 7, 7, 7), current=5, latest=28)

    sink, _asked, _code = run_extract(payload, periods=[3, 1])

    assert [mp for mp, _sps in sink.box_scores] == [3, 1]


# ===========================================================================
# 5. Explicit current / future / unserved periods refuse before any write
# ===========================================================================
@pytest.mark.parametrize("requested, needle", [
    ([4], "has not shown it CLOSED"),         # == currentMatchupPeriod
    ([6], "has not been played yet"),         # > current, in the schedule
    ([40], "never scheduled"),                # not in the payload at all
])
def test_an_unclosed_explicit_period_refuses_and_writes_no_box_scores(
        run_extract, requested, needle):
    payload = _payload((7, 7, 7, 7, 7, 7), current=4, latest=28)

    with pytest.raises(SystemExit) as excinfo:
        run_extract(payload, periods=requested)

    message = str(excinfo.value)
    assert needle in message
    assert f"period {requested[0]} " in message
    assert "Closed and extractable: [1, 2, 3]" in message


def test_the_unclosed_refusal_writes_nothing_but_the_snapshot(run_extract):
    payload = _payload((7, 7, 7, 7), current=4, latest=28)
    sink = _RecordingSink()

    with pytest.raises(SystemExit):
        run_extract(payload, sink, periods=[4])

    assert sink.box_scores == []
    assert sink.other_writes == []
    assert len(sink.matchup_schedules) == 1, (
        "the snapshot is diagnostic evidence and is kept"
    )


def test_a_mixed_request_refuses_as_a_whole(run_extract):
    """One bad period stops the set. Extracting the good ones and refusing
    the rest half-finishes, which is worse to be handed than a refusal."""
    payload = _payload((7, 7, 7, 7), current=4, latest=28)
    sink = _RecordingSink()

    with pytest.raises(SystemExit):
        run_extract(payload, sink, periods=[1, 2, 4])

    assert sink.box_scores == []


# ===========================================================================
# 6. --all takes every closed period, promotion included, live period out
# ===========================================================================
def test_all_selects_every_closed_period(run_extract):
    payload = _payload((13, 7, 7, 7, 7), current=6, latest=41)

    sink, _asked, _code = run_extract(payload, all=True)

    assert [mp for mp, _sps in sink.box_scores] == [1, 2, 3, 4, 5]


def test_all_excludes_the_live_current_period(run_extract):
    """Period 5 is in the payload and is the current one. Its membership is
    still filling in, and a short in-flight period is indistinguishable from
    a real abnormality."""
    payload = _payload((7, 7, 7, 7, 2), current=5, latest=30)

    sink, _asked, _code = run_extract(payload, all=True)

    assert [mp for mp, _sps in sink.box_scores] == [1, 2, 3, 4]


def test_all_includes_the_promoted_final_period_of_a_finished_season(run_extract):
    """A finished season pins currentMatchupPeriod ON its last period rather
    than past it, so the strict rule alone discards the final completed week.
    The completion proofs promote it: latest > final, well-formed, and its
    membership ends exactly at finalScoringPeriod."""
    payload = _payload((7, 7, 7), current=3, latest=22, final=21,
                       season_year=2025)

    sink, _asked, _code = run_extract(
        payload, all=True, year=2025, overwrite_day_accurate_history=True)

    assert [mp for mp, _sps in sink.box_scores] == [1, 2, 3]
    assert sink.box_scores[-1][1] == list(range(15, 22))


def test_a_final_period_that_stops_short_is_not_promoted(run_extract):
    """The season is over by the status block, but this period does not
    reach finalScoringPeriod -- so it is not the period that ended it."""
    payload = _payload((7, 7, 4), current=3, latest=22, final=21,
                       season_year=2025)

    sink, _asked, _code = run_extract(
        payload, all=True, year=2025, overwrite_day_accurate_history=True)

    assert [mp for mp, _sps in sink.box_scores] == [1, 2]


# ===========================================================================
# 7. The default window, both sides of the boundary
# ===========================================================================
def _default_selection(run_extract, *, latest, lengths, current):
    sink, _asked, _code = run_extract(
        _payload(lengths, current=current, latest=latest))
    return [mp for mp, _sps in sink.box_scores]


def test_default_selection_takes_only_periods_inside_the_window(run_extract):
    """Four 7-day periods ending at scoring periods 7/14/21/28, latest 28,
    window 21. Cutoff is 7, so every period is in -- period 1 sits exactly
    ON the boundary."""
    assert extract.LIVE_CAPTURE_WINDOW_DAYS == 21
    assert _default_selection(run_extract, latest=28,
                              lengths=(7, 7, 7, 7), current=5) == [1, 2, 3, 4]


def test_the_window_boundary_is_inclusive_and_one_past_it_is_not(run_extract):
    """Both sides of the same edge, explicitly.

    latest=29 puts the cutoff at 8: period 1 ends at scoring period 7, one
    short, and drops out. latest=28 puts it at 7 and period 1 survives. One
    scoring period of difference has to flip exactly one period.
    """
    inside = _default_selection(run_extract, latest=28,
                                lengths=(7, 7, 7, 7), current=5)
    outside = _default_selection(run_extract, latest=29,
                                 lengths=(7, 7, 7, 7), current=5)

    assert inside == [1, 2, 3, 4]
    assert outside == [2, 3, 4]


def test_the_default_window_reproduces_the_real_2026_selection(run_extract):
    """The measured payload, as a sanity anchor on the unit change.

    2026 on 2026-08-11: current period 19, latestScoringPeriod 140, closed
    periods 1..18 with period 15 ending at scoring period 117 and 16 at 124.
    The old calendar rule (end_date >= today - 21d, opener 2026-03-25)
    selected 16, 17 and 18. Scoring-period ids must select the same three:
    140 - 21 = 119, so 124 is in and 117 is out.
    """
    lengths = (12,) + (7,) * 13 + (14,) + (7,) * 3 + (2,)
    sink, _asked, _code = run_extract(
        _payload(lengths, current=19, latest=140, final=187))

    assert [mp for mp, _sps in sink.box_scores] == [16, 17, 18]


def test_no_recent_period_is_a_clean_exit_not_a_refusal(run_extract):
    """The offseason case. Closed periods exist; none is recent. That is a
    normal answer and must stay distinguishable from zero closed periods."""
    payload = _payload((7, 7), current=3, latest=200)
    sink = _RecordingSink()

    sink, _asked, code = run_extract(payload, sink)

    assert code == 0
    assert sink.box_scores == []
    assert len(sink.matchup_schedules) == 1


# ===========================================================================
# 8. The settled-history guard on the shared policy
# ===========================================================================
def _guard(parse, loaded, requested, *, year=SEASON, today=TODAY):
    return extract.settled_loaded_periods(
        _RecordingSink(loaded={mp: datetime(2026, 5, 3) for mp in loaded}),
        year, LEAGUE, requested, parse, today=today)


def test_the_guard_blocks_a_loaded_settled_period(run_extract):
    parse = _parse(_payload((7, 7, 7, 7), current=5, latest=100))

    settled = _guard(parse, loaded=[1, 2], requested=[1, 2])

    assert [mp for mp, _sp, _at in settled] == [1, 2]
    assert [sp for _mp, sp, _at in settled] == [7, 14], (
        "the guard reports the scoring period a settled week closed on"
    )


def test_the_guard_permits_an_unloaded_period(run_extract):
    """A first extract invents no history, however old the period is."""
    parse = _parse(_payload((7, 7, 7, 7), current=5, latest=100))

    assert _guard(parse, loaded=[], requested=[1, 2]) == []


def test_the_guard_permits_a_loaded_period_inside_the_window(run_extract):
    """The weekly run revisits these on purpose -- it is the mechanism that
    captures club labels and FA rows at all."""
    parse = _parse(_payload((7, 7, 7, 7), current=5, latest=28))

    assert _guard(parse, loaded=[3, 4], requested=[3, 4]) == []


def test_the_guard_fails_closed_when_age_evidence_is_missing(run_extract):
    """No latestScoringPeriod means no knowable age. Absent evidence is
    protection, not permission."""
    parse = _parse(_payload((7, 7, 7, 7), current=5))
    assert parse.latest_scoring_period is None

    settled = _guard(parse, loaded=[4], requested=[4])

    assert [mp for mp, _sp, _at in settled] == [4]


def test_the_guard_fails_closed_for_a_period_outside_the_membership(run_extract):
    """The platform-evidence twin of "no schedule row". A loaded period ESPN
    does not carry has no placeable age, so it is protected and reported as
    unknown rather than dated."""
    parse = _parse(_payload((7, 7), current=3, latest=14))

    settled = _guard(parse, loaded=[42], requested=[42])

    assert settled == [(42, None, datetime(2026, 5, 3))]


def test_an_earlier_season_is_settled_however_close_its_final_period_is(run_extract):
    """2025's last week is as unreachable as its first -- kona answers about
    TODAY's player universe. An in-season distance says nothing about it."""
    parse = _parse(_payload((7, 7, 7), current=4, latest=21, season_year=2025),
                   season_year=2025)

    settled = _guard(parse, loaded=[3], requested=[3], year=2025)

    assert [mp for mp, _sp, _at in settled] == [3]


def test_the_guard_refusal_names_the_scoring_period_not_a_date():
    """The message no longer consults a calendar, so it must not print one."""
    message = extract.refuse_settled_overwrite(
        [(14, 104, datetime(2026, 5, 3, 20, 24, 13))], SEASON,
        "--overwrite-day-accurate-history")

    message.encode("ascii")
    assert "scoring period 104" in message
    assert "--overwrite-day-accurate-history" in message
    assert "Nothing was written" in message


def test_the_guard_refusal_says_unknown_for_an_unplaceable_period():
    message = extract.refuse_settled_overwrite(
        [(42, None, datetime(2026, 5, 3))], SEASON, "--flag")

    assert "UNKNOWN (not in ESPN's membership)" in message


def test_selection_and_the_guard_share_one_definition_of_recent(run_extract):
    """MLB-175's scar, restated for the new policy: a second copy of this
    arithmetic would silently widen or narrow the guard the day someone
    tuned one of them. Both callers must agree period by period."""
    parse = _parse(_payload((7,) * 6, current=7, latest=42))

    verdicts = classify_recency(parse, window=extract.LIVE_CAPTURE_WINDOW_DAYS,
                                is_current_season=True)
    default = set(extract.select_matchup_periods(
        parse, requested=[], want_all=False, year=SEASON, today=TODAY)[0])
    exempt = {mp for mp in parse.closed_periods
              if not _guard(parse, loaded=[mp], requested=[mp])}

    assert default == exempt
    assert default == {mp for mp, v in verdicts.items() if v == RECENT}


def test_the_guard_still_refuses_when_it_cannot_be_evaluated(run_extract):
    """MLB-199 survives the rewrite: an unanswerable guard is not a safe one."""
    class _Broken(_RecordingSink):
        def loaded_box_score_periods(self, year, league_key):
            raise RuntimeError("Insufficient privileges on BOX_SCORES")

    parse = _parse(_payload((7, 7), current=3, latest=100))

    with pytest.raises(SystemExit) as excinfo:
        extract.settled_loaded_periods(
            _Broken(), SEASON, LEAGUE, [1], parse, today=TODAY)

    assert "cannot verify settled history" in str(excinfo.value)
    assert "Insufficient privileges" in str(excinfo.value)


def test_the_guard_stops_the_run_before_any_box_score_is_written(run_extract):
    payload = _payload((7, 7, 7, 7), current=5, latest=100)
    sink = _RecordingSink(loaded={1: datetime(2026, 5, 3)})

    with pytest.raises(SystemExit) as excinfo:
        run_extract(payload, sink, periods=[1])

    assert "REFUSING TO EXTRACT" in str(excinfo.value)
    assert sink.box_scores == []


# ===========================================================================
# 9. --backfill-club-of-game keeps its non-destructive semantics
# ===========================================================================
def test_the_backfill_receives_derived_periods_and_deletes_nothing(run_extract):
    payload = _payload((7, 7, 7, 7), current=5, latest=28)
    sink = _RecordingSink()

    sink, _asked, code = run_extract(
        payload, sink, all=True, backfill_club_of_game=True,
        raw_target="snowflake")

    assert code == 0
    assert sink.backfills == [(SEASON, LEAGUE, [1, 2, 3, 4])]
    assert sink.box_scores == [], (
        "the backfill is enrichment; it must not re-extract anything"
    )


def test_the_backfill_does_not_trip_the_settled_guard(run_extract):
    """It updates in place and deletes nothing, so it is not what the guard
    is guarding -- that asymmetry is the whole argument for its existence."""
    payload = _payload((7, 7), current=3, latest=200)
    sink = _RecordingSink(loaded={1: datetime(2025, 5, 3),
                                  2: datetime(2025, 5, 3)})

    sink, _asked, code = run_extract(
        payload, sink, all=True, backfill_club_of_game=True,
        raw_target="snowflake")

    assert code == 0
    assert sink.backfills == [(SEASON, LEAGUE, [1, 2])]


def test_the_backfill_honours_explicit_derived_periods(run_extract):
    payload = _payload((7, 7, 7, 7), current=5, latest=28)
    sink = _RecordingSink()

    sink, _asked, _code = run_extract(
        payload, sink, periods=[2, 4], backfill_club_of_game=True,
        raw_target="snowflake")

    assert sink.backfills == [(SEASON, LEAGUE, [2, 4])]


def test_the_backfill_refuses_an_unclosed_period_like_any_other_selection(
        run_extract):
    payload = _payload((7, 7, 7, 7), current=4, latest=28)
    sink = _RecordingSink()

    with pytest.raises(SystemExit):
        run_extract(payload, sink, periods=[4], backfill_club_of_game=True,
                    raw_target="snowflake")

    assert sink.backfills == []


# ===========================================================================
# 11. The two tiers: structure vs membership
# ===========================================================================
def test_a_structurally_invalid_snapshot_writes_nothing_at_all(run_extract):
    """Tier one. No seasonId means the only season label on the row would be
    the one the loader stamped itself, so the document is not storable."""
    payload = _payload((7, 7), current=3, latest=14)
    del payload["seasonId"]
    sink = _RecordingSink()

    with pytest.raises(SystemExit) as excinfo:
        run_extract(payload, sink)

    assert "REFUSING to capture" in str(excinfo.value)
    assert sink.matchup_schedules == [], "an unstorable document was stored"
    assert sink.box_scores == []
    assert sink.other_writes == []


def test_a_membership_invalid_snapshot_is_preserved_then_refused(run_extract):
    """Tier two. Structurally fine -- all three blocks, right shapes, right
    season -- but the sides of period 2 disagree about which scoring periods
    it contains, so membership cannot be derived from it."""
    payload = _payload((7, 7, 7), current=4, latest=21)
    payload["schedule"][1]["away"] = _side(range(8, 14))   # one day short
    sink = _RecordingSink()

    with pytest.raises(SystemExit) as excinfo:
        run_extract(payload, sink)

    message = str(excinfo.value)
    assert "membership could not be derived" in message
    assert "disagree" in message, "the underlying reason must be surfaced"
    assert "WAS preserved" in message
    assert len(sink.matchup_schedules) == 1, (
        "structurally valid evidence was discarded; ESPN will not re-serve it"
    )
    assert sink.box_scores == []
    assert sink.other_writes == []


def test_the_membership_refusal_lands_before_settings_and_standings(run_extract):
    """"Refuse before any box-score, settings, standings or transaction
    write" is an ORDERING claim, so it is asserted against a run that asked
    for all of them."""
    payload = _payload((7, 7, 7), current=4, latest=21)
    payload["schedule"][1]["away"] = _side(range(8, 14))
    sink = _RecordingSink()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(extract, "extract_league_settings",
                   lambda s, y, k: s.other_writes.append("settings"))
    monkey.setattr(extract, "extract_team_standings",
                   lambda s, y, k: s.other_writes.append("standings"))
    monkey.setattr(extract, "extract_transactions",
                   lambda s, y, k: s.other_writes.append("transactions"))
    try:
        with pytest.raises(SystemExit):
            run_extract(payload, sink, include_settings=True,
                        include_transactions=True, no_standings=False)
    finally:
        monkey.undo()

    assert sink.other_writes == []


@pytest.mark.parametrize("mode", [
    pytest.param({}, id="ordinary-box-score-run"),
    pytest.param({"matchup_schedule_only": True}, id="schedule-only"),
    pytest.param({"settings_only": True, "include_matchup_schedule": True},
                 id="settings-only-plus-compat-flag"),
    pytest.param({"transactions_only": True, "include_matchup_schedule": True},
                 id="transactions-only-plus-compat-flag"),
    pytest.param({"include_settings": True, "include_transactions": True,
                  "no_standings": False}, id="everything-at-once"),
])
def test_underivable_membership_refuses_nonzero_in_every_acquisition_mode(
        run_extract, mode):
    """KYLE'S RULING, rung 4B-1.

    --matchup-schedule-only briefly stored the unusable document and exited
    0, on the reasoning that its job was to capture and it had. That is
    wrong, and the exit code is exactly where it is wrong: capturing USABLE
    matchup membership is what the command is for, and a zero status tells a
    script, a cron job and a stranger alike that the season's membership was
    captured. Preserving evidence of an unusable response is not the same as
    succeeding at it.

    So every acquisition mode refuses, the snapshot is the only surface that
    writes, and the message says so.
    """
    payload = _payload((7, 7, 7), current=4, latest=21)
    payload["schedule"][1]["away"] = _side(range(8, 14))   # sides disagree
    sink = _RecordingSink()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(extract, "extract_league_settings",
                   lambda s, y, k: s.other_writes.append("settings"))
    monkey.setattr(extract, "extract_team_standings",
                   lambda s, y, k: s.other_writes.append("standings"))
    monkey.setattr(extract, "extract_transactions",
                   lambda s, y, k: s.other_writes.append("transactions"))
    try:
        with pytest.raises(SystemExit) as excinfo:
            run_extract(payload, sink, **mode)
    finally:
        monkey.undo()

    assert _exit_status(excinfo) != 0, (
        "an unusable membership response exited successfully"
    )
    message = str(excinfo.value)
    assert "membership could not be derived" in message
    assert "WAS preserved" in message
    assert "ONLY THING THIS RUN WROTE" in message
    assert "disagree" in message, "the underlying reason must be surfaced"

    assert len(sink.matchup_schedules) == 1, (
        "the diagnostic snapshot is the one surface allowed to write"
    )
    assert sink.box_scores == []
    assert sink.other_writes == [], (
        "settings, standings or transactions wrote despite the refusal"
    )


def test_the_zero_period_shape_is_not_reported_as_underivable(run_extract):
    """The distinction the ruling explicitly preserves. Zero closed periods
    parses CLEANLY -- it is a cardinality, not malformed membership -- so it
    must never reach the underivable refusal, in any mode."""
    payload = {"seasonId": SEASON,
               "status": {"currentMatchupPeriod": 1}, "schedule": []}

    # Schedule-only: succeeds outright.
    sink, _asked, code = run_extract(payload, matchup_schedule_only=True)
    assert code == 0

    # Box-score run: refuses, but for the OTHER reason.
    with pytest.raises(SystemExit) as excinfo:
        run_extract(payload)
    assert "no closed matchup periods" in str(excinfo.value)
    assert "could not be derived" not in str(excinfo.value)


# ===========================================================================
# 12. Both sink seams get the same orchestration
# ===========================================================================
def test_the_snowflake_seam_receives_the_same_snapshot_and_selection(
        run_extract, monkeypatch):
    """The real SnowflakeSink over a fake connection -- no live account. The
    orchestration is engine-neutral, so the periods and the stored snapshot
    must match the local sink's exactly."""
    payload = _payload((7, 7, 7), current=4, latest=21)

    local = _RecordingSink()
    local, _asked, _code = run_extract(payload, local)

    class _Cursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((" ".join(sql.split()), params))

        def fetchone(self):
            return (1,)

        def fetchall(self):
            return []

        def close(self):
            pass

    class _Conn:
        def __init__(self):
            self.cursors = []

        def cursor(self):
            c = _Cursor()
            self.cursors.append(c)
            return c

        def commit(self):
            pass

    conn = _Conn()
    warehouse = extract.SnowflakeSink(conn)
    written = []
    monkeypatch.setattr(
        extract, "load_box_scores_to_snowflake",
        lambda c, records, mp, y, k: written.append(
            (mp, [r["scoring_period"] for r in records])))

    warehouse_run, _asked, code = run_extract(
        payload, warehouse, raw_target="snowflake")

    assert code == 0
    assert written == local.box_scores

    inserts = [params for c in conn.cursors for sql, params in c.executed
               if sql.startswith("INSERT INTO MATCHUP_SCHEDULE")]
    assert len(inserts) == 1
    import json
    assert json.loads(inserts[0][1]) == local.matchup_schedules[0][0]


def test_both_sinks_preserve_the_snapshot_before_the_membership_refusal(
        run_extract):
    """"Preserved, then refused" must not be engine-dependent. Both writes
    are single-shot -- INSERT + commit, and an atomic parquet rename -- so
    the behaviour is the same on either target."""
    payload = _payload((7, 7, 7), current=4, latest=21)
    payload["schedule"][1]["away"] = _side(range(8, 14))

    for target in ("local", "snowflake"):
        sink = _RecordingSink()
        with pytest.raises(SystemExit) as excinfo:
            run_extract(payload, sink, raw_target=target)
        assert len(sink.matchup_schedules) == 1, target
        assert "WAS preserved" in str(excinfo.value), target


# ===========================================================================
# 16-17. Zero closed periods is a cardinality, not an error
# ===========================================================================
def test_a_current_period_of_one_parses_as_zero_closed_periods(run_extract):
    """`currentMatchupPeriod = 1` with an empty schedule. Nothing is closed,
    nothing is malformed, and no period 0 or 1 is invented."""
    payload = {"seasonId": SEASON,
               "status": {"currentMatchupPeriod": 1, "isActive": True},
               "schedule": []}

    parse = _parse(payload)

    assert parse.closed == ()
    assert parse.closed_periods == ()
    assert parse.rows == ()
    assert parse.current_matchup_period == 1


def test_the_zero_period_shape_captures_and_reports_zero(run_extract, capsys):
    """--matchup-schedule-only succeeds on it and says so."""
    payload = {"seasonId": SEASON,
               "status": {"currentMatchupPeriod": 1, "isActive": True},
               "schedule": []}
    sink = _RecordingSink()

    sink, _asked, code = run_extract(payload, sink, matchup_schedule_only=True)

    assert code == 0
    assert len(sink.matchup_schedules) == 1
    assert sink.matchup_schedules[0][0]["schedule"] == []
    assert "0 closed matchup period(s)" in capsys.readouterr().out


def test_the_zero_period_shape_refuses_an_ordinary_box_score_run(run_extract):
    """THE FAILURE THIS PREVENTS: printing "Done." after selecting nothing
    and writing no player rows, which is indistinguishable from a successful
    weekly pull."""
    payload = {"seasonId": SEASON,
               "status": {"currentMatchupPeriod": 1, "isActive": True,
                          "currentLeagueType": 0, "createdAsLeagueType": 2},
               "schedule": []}
    sink = _RecordingSink()

    with pytest.raises(SystemExit) as excinfo:
        run_extract(payload, sink)

    message = str(excinfo.value)
    message.encode("ascii")
    assert "no closed matchup periods" in message
    assert "nothing was fabricated" in message.lower()
    assert "rotisserie" in message or "roto" in message
    assert "--matchup-schedule-only" in message
    assert sink.box_scores == []
    assert len(sink.matchup_schedules) == 1


def test_the_zero_period_refusal_reports_measured_format_evidence(run_extract):
    """The status block's own league-type fields, verbatim and uninterpreted.
    Hard-coding "0 means H2H" from one league is the unverified format map
    the standing rule forbids; reporting the measurement is not."""
    payload = {"seasonId": SEASON,
               "status": {"currentMatchupPeriod": 1,
                          "currentLeagueType": 3, "createdAsLeagueType": 3},
               "schedule": []}

    with pytest.raises(SystemExit) as excinfo:
        run_extract(payload)

    message = str(excinfo.value)
    assert "status.currentLeagueType = 3" in message
    assert "status.createdAsLeagueType = 3" in message
    assert "NOT established what these values mean" in message


def test_the_zero_period_refusal_names_no_identity(run_extract):
    """Identity-free, and asserted rather than assumed: the payload carries a
    team id on every side and none of it may reach the message."""
    payload = _payload((7,), current=1, latest=7)
    sink = _RecordingSink()

    with pytest.raises(SystemExit) as excinfo:
        run_extract(payload, sink)

    message = str(excinfo.value)
    assert str(TEAM_ID) not in message
    assert LEAGUE not in message


def test_the_zero_period_refusal_lands_before_settings_and_standings(run_extract):
    payload = {"seasonId": SEASON,
               "status": {"currentMatchupPeriod": 1}, "schedule": []}
    sink = _RecordingSink()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(extract, "extract_league_settings",
                   lambda s, y, k: s.other_writes.append("settings"))
    monkey.setattr(extract, "extract_team_standings",
                   lambda s, y, k: s.other_writes.append("standings"))
    try:
        with pytest.raises(SystemExit):
            run_extract(payload, sink, include_settings=True,
                        no_standings=False)
    finally:
        monkey.undo()

    assert sink.other_writes == []


# ===========================================================================
# 18. A genuine one-period season
# ===========================================================================
def test_a_current_season_spanning_period_is_not_promoted_early(run_extract):
    """One period, still current, season not over. It is not treated as a
    normal seven-day week and it is not extracted."""
    payload = _payload((40,), current=1, latest=40, final=187)

    parse = _parse(payload)

    assert parse.closed_periods == ()
    assert parse.excluded == (1,)
    assert parse.promoted_period is None

    with pytest.raises(SystemExit) as excinfo:
        run_extract(payload)
    assert "no closed matchup periods" in str(excinfo.value)


def test_a_one_period_season_is_extracted_once_the_completion_proof_holds(
        run_extract):
    """Same league, season over. latest > final, the period is well-formed,
    and its membership ends exactly at finalScoringPeriod -- so the existing
    promotion rule includes it, and it is extracted with ITS OWN 40 scoring
    periods rather than a fabricated week."""
    payload = _payload((40,), current=1, latest=41, final=40)

    parse = _parse(payload)
    assert parse.closed_periods == (1,)
    assert parse.promoted_period == 1

    sink, _asked, code = run_extract(payload, all=True)

    assert code == 0
    assert sink.box_scores == [(1, list(range(1, 41)))]


def test_a_one_period_season_that_stops_short_stays_excluded(run_extract):
    """The season is over, but this period does not reach finalScoringPeriod,
    so it is not the period that ended it and nothing promotes it."""
    payload = _payload((30,), current=1, latest=41, final=40)

    assert _parse(payload).closed_periods == ()

    with pytest.raises(SystemExit) as excinfo:
        run_extract(payload)
    assert "no closed matchup periods" in str(excinfo.value)


# ===========================================================================
# The recency policy, unit-level
# ===========================================================================
def test_recency_verdicts_are_the_three_distinct_answers():
    parse = _parse(_payload((7, 7, 7), current=4, latest=21))

    assert classify_recency(parse, window=21, is_current_season=True) == {
        1: RECENT, 2: RECENT, 3: RECENT}
    assert classify_recency(parse, window=0, is_current_season=True) == {
        1: SETTLED, 2: SETTLED, 3: RECENT}
    assert classify_recency(parse, window=21, is_current_season=False) == {
        1: SETTLED, 2: SETTLED, 3: SETTLED}


def test_recency_is_unknown_without_a_latest_scoring_period():
    parse = _parse(_payload((7, 7), current=3))

    assert classify_recency(parse, window=21, is_current_season=True) == {
        1: UNKNOWN, 2: UNKNOWN}


def test_a_malformed_latest_scoring_period_is_unknown_not_a_guess():
    """`_optional_scoring_period` rules apply: a boolean, a string or a
    non-positive value is absent evidence, not a clock."""
    for bad in (True, "140", 0, -1, 1.5):
        payload = _payload((7, 7), current=3)
        payload["status"]["latestScoringPeriod"] = bad
        parse = _parse(payload)
        assert set(classify_recency(
            parse, window=21, is_current_season=True).values()) == {UNKNOWN}, bad


# ===========================================================================
# CLI contradictions
# ===========================================================================
# ===========================================================================
# 10. Registry-bounded schedule history (--all-seasons)
# ===========================================================================
class _Registry:
    """A league registry entry with explicit season bounds."""

    def __init__(self, first_season, final_season=None):
        self.key = LEAGUE
        self.display_name = "Test league"
        self.platform = "espn"
        self.first_season = first_season
        self.final_season = final_season

    def require_credentials(self):
        pass


@pytest.fixture
def registry(monkeypatch):
    def _set(first_season, final_season=None):
        monkeypatch.setattr(
            extract, "get_league",
            lambda key=None: _Registry(first_season, final_season))
    return _set


def _history_run(monkeypatch, sink, payload_for, calendar_for=None,
                 **arg_overrides):
    """Drive --all-seasons, recording every document requested, in order.

    Both fetches are counted separately so "exactly once per season" is an
    assertion rather than an intention, and `sink.opened` records whether the
    sink was opened at all -- which is the difference between "nothing was
    written" and "nothing was written but a warehouse connection and a
    manifest were created anyway".
    """
    asked, calendars_asked = [], []
    # Hung on the sink so a test that expects SystemExit can still read them:
    # the return value below never arrives when run() refuses.
    sink.asked, sink.calendars_asked = asked, calendars_asked

    def _fetch(year, views):
        asked.append((year, tuple(views)))
        return payload_for(year)

    def _fetch_calendar(year):
        calendars_asked.append(year)
        if calendar_for is not None:
            return calendar_for(year)
        return {"seasons": [{"seasonId": str(year),
                             "regularSeasonStartDate": f"{year}-03-25",
                             "regularSeasonEndDate": f"{year}-09-27"}]}

    monkeypatch.setattr(extract, "fetch_league_payload", _fetch)
    monkeypatch.setattr(extract, "fetch_season_calendar", _fetch_calendar)

    from contextlib import contextmanager

    @contextmanager
    def _open(raw_target, parquet_dir=None):
        sink.opened = True
        yield sink

    monkeypatch.setattr(extract, "open_sink", _open)
    code = extract.run(_args(matchup_schedule_only=True, all_seasons=True,
                             **arg_overrides))
    return asked, calendars_asked, code


def _ok(year):
    return _payload((7, 7, 7), current=4, latest=21, season_year=year)


def test_all_seasons_requests_the_registry_range_ascending_once_each(
        monkeypatch, registry):
    registry(2023)
    sink = _RecordingSink()

    asked, calendars, code = _history_run(monkeypatch, sink, _ok, year=2026)

    assert code == 0
    assert asked == [(y, ("mMatchupScore",)) for y in (2023, 2024, 2025, 2026)]
    assert calendars == [2023, 2024, 2025, 2026]


def test_the_successful_path_writes_every_season_ascending(
        monkeypatch, registry):
    registry(2023)
    sink = _RecordingSink()

    _asked, _calendars, code = _history_run(monkeypatch, sink, _ok, year=2026)

    assert code == 0
    assert [year for _p, year, _k in sink.matchup_schedules] == \
        [2023, 2024, 2025, 2026]
    assert [year for _p, year, _k in sink.season_calendars] == \
        [2023, 2024, 2025, 2026]
    assert all(payload["regularSeasonStartDate"] == f"{year}-03-25"
               for payload, year, _k in sink.season_calendars)


def test_the_successful_path_does_not_refetch_what_it_prefetched(
        monkeypatch, registry):
    """Validate-then-write must not mean fetch-twice: the documents written
    are the ones the preflight already checked."""
    registry(2025)
    sink = _RecordingSink()

    asked, calendars, _code = _history_run(monkeypatch, sink, _ok, year=2026)

    assert len(asked) == 2 and len(calendars) == 2
    assert len(sink.matchup_schedules) == 2
    assert len(sink.season_calendars) == 2


def test_all_seasons_respects_a_closed_registry_upper_bound(
        monkeypatch, registry):
    """A folded league stops at final_season, not at the year requested."""
    registry(2023, final_season=2024)
    sink = _RecordingSink()

    asked, _calendars, _code = _history_run(monkeypatch, sink, _ok, year=2026)

    assert [year for year, _v in asked] == [2023, 2024]


def test_all_seasons_is_capped_by_the_year_requested(monkeypatch, registry):
    registry(2023)
    sink = _RecordingSink()

    asked, _calendars, _code = _history_run(monkeypatch, sink, _ok, year=2024)

    assert [year for year, _v in asked] == [2023, 2024]


# --- nothing is written until the WHOLE range has passed --------------------

def test_a_structurally_invalid_season_writes_nothing_and_opens_no_sink(
        monkeypatch, registry):
    """THE HALF-BACKFILL THIS PREVENTS. 2025 is malformed. A loop that
    fetched-then-wrote each season in turn would leave a warehouse holding an
    apparently complete history through 2024, with nothing on any row saying
    the run stopped."""
    registry(2023)
    sink = _RecordingSink()
    sink.opened = False

    def _payload_for(year):
        if year != 2025:
            return _ok(year)
        broken = _ok(year)
        del broken["seasonId"]
        return broken

    with pytest.raises(SystemExit) as excinfo:
        _history_run(monkeypatch, sink, _payload_for, year=2026)

    assert "REFUSING to capture 2025" in str(excinfo.value)
    assert sink.matchup_schedules == []
    assert sink.season_calendars == []
    assert sink.opened is False, (
        "a sink was opened for a range that wrote nothing"
    )


def test_an_underivable_season_preserves_only_its_own_snapshot(
        monkeypatch, registry):
    """Structurally valid, membership-invalid. The settled universal rule
    applies -- diagnostic snapshot preserved, non-zero exit -- and the ONLY
    row written is the failing season's.

    This is the correction: the first implementation wrote 2023 and 2024
    successfully before reaching 2025, which made the refusal message's
    "the snapshot is the only thing this run wrote" literally false.
    """
    registry(2023)
    sink = _RecordingSink()

    def _payload_for(year):
        payload = _ok(year)
        if year == 2025:
            payload["schedule"][1]["away"] = _side(range(8, 14))
        return payload

    with pytest.raises(SystemExit) as excinfo:
        _history_run(monkeypatch, sink, _payload_for, year=2026)

    assert _exit_status(excinfo) != 0
    message = str(excinfo.value)
    assert "membership could not be derived" in message
    assert "ONLY THING THIS RUN WROTE" in message

    assert [year for _p, year, _k in sink.matchup_schedules] == [2025], (
        "an earlier season was written before the range had passed"
    )
    assert sink.season_calendars == [], (
        "a calendar was written for a range that refused"
    )


def test_an_underivable_season_never_reaches_the_calendar_api(
        monkeypatch, registry):
    """A range that is going to refuse must not spend requests on MLB's API
    -- the calendars are only worth having if the schedules survive."""
    registry(2023)
    sink = _RecordingSink()

    def _payload_for(year):
        payload = _ok(year)
        if year == 2024:
            payload["schedule"][1]["away"] = _side(range(8, 14))
        return payload

    with pytest.raises(SystemExit):
        _history_run(monkeypatch, sink, _payload_for, year=2026)

    assert sink.calendars_asked == [], (
        "MLB's API was called for a range that then refused"
    )
    # Every ESPN document was still fetched exactly once -- membership is
    # parsed over the whole structurally valid set, not abandoned mid-scan.
    assert sink.asked == [(y, ("mMatchupScore",))
                          for y in (2023, 2024, 2025, 2026)]


def test_each_document_is_requested_exactly_once_on_the_failing_path(
        monkeypatch, registry):
    """Preflight-then-write must not become fetch-twice on the way to a
    refusal either."""
    registry(2024)
    sink = _RecordingSink()

    def _payload_for(year):
        payload = _ok(year)
        if year == 2026:
            payload["schedule"][1]["away"] = _side(range(8, 14))
        return payload

    with pytest.raises(SystemExit):
        _history_run(monkeypatch, sink, _payload_for, year=2026)

    assert sorted(y for y, _v in sink.asked) == [2024, 2025, 2026]
    assert len(sink.asked) == len(set(sink.asked))


def test_a_calendar_network_failure_writes_no_history(monkeypatch, registry):
    """A history command must not report success with only some anchors. The
    ordinary weekly run is deliberately NOT like this -- see the next test."""
    registry(2023)
    sink = _RecordingSink()
    sink.opened = False

    def _calendar_for(year):
        if year == 2025:
            raise ConnectionError("statsapi unreachable")
        return {"seasons": [{"seasonId": str(year),
                             "regularSeasonStartDate": f"{year}-03-25",
                             "regularSeasonEndDate": f"{year}-09-27"}]}

    with pytest.raises(SystemExit) as excinfo:
        _history_run(monkeypatch, sink, _ok, calendar_for=_calendar_for,
                     year=2026)

    assert _exit_status(excinfo) != 0
    message = str(excinfo.value)
    assert "2025's season calendar could not be captured" in message
    assert "NOTHING WAS WRITTEN" in message
    assert sink.matchup_schedules == []
    assert sink.season_calendars == []
    assert sink.opened is False


def test_a_calendar_shape_failure_writes_no_history(monkeypatch, registry):
    registry(2023)
    sink = _RecordingSink()
    sink.opened = False

    def _calendar_for(year):
        start = "not-a-date" if year == 2024 else f"{year}-03-25"
        return {"seasons": [{"seasonId": str(year),
                             "regularSeasonStartDate": start,
                             "regularSeasonEndDate": f"{year}-09-27"}]}

    with pytest.raises(SystemExit) as excinfo:
        _history_run(monkeypatch, sink, _ok, calendar_for=_calendar_for,
                     year=2026)

    assert "2024's season calendar could not be captured" in str(excinfo.value)
    assert sink.matchup_schedules == []
    assert sink.season_calendars == []
    assert sink.opened is False


def test_an_ordinary_run_is_not_blocked_by_a_calendar_failure(
        run_extract, monkeypatch):
    """The asymmetry, asserted next to the rule it differs from. A weekly
    box-score extract needs no dates, so a briefly unreachable public API is
    a warning there; --all-seasons promises a range, so it refuses."""
    monkeypatch.setattr(
        extract, "fetch_season_calendar",
        lambda year: (_ for _ in ()).throw(ConnectionError("unreachable")))
    payload = _payload((7, 7, 7), current=4, latest=21)

    sink, _asked, code = run_extract(payload)

    assert code == 0
    assert [mp for mp, _sps in sink.box_scores] == [1, 2, 3]
    assert sink.season_calendars == []


def test_an_empty_registry_range_exits_successfully_and_opens_nothing(
        monkeypatch, registry):
    """A league whose first season is after the one requested has no history
    to pull. That is an answer, not an error -- and it must not leave a
    warehouse connection, a parquet directory or a manifest behind."""
    registry(2030)
    sink = _RecordingSink()
    sink.opened = False

    asked, calendars, code = _history_run(monkeypatch, sink, _ok, year=2026)

    assert code == 0
    assert asked == [] and calendars == []
    assert sink.opened is False, "a sink was opened for an empty range"
    assert sink.matchup_schedules == []


# --- the plan itself, without a sink in scope -------------------------------

def test_the_plan_cannot_write_because_it_holds_no_sink(monkeypatch):
    """Structural, not checked: `plan_matchup_history` takes no sink, so a
    caller cannot half-write a range however it is edited later."""
    import inspect

    assert "sink" not in inspect.signature(
        extract.plan_matchup_history).parameters


def test_a_validated_plan_carries_every_season_and_no_failure(monkeypatch):
    monkeypatch.setattr(extract, "fetch_league_payload",
                        lambda year, views: _ok(year))
    monkeypatch.setattr(
        extract, "fetch_season_calendar",
        lambda year: {"seasons": [{"seasonId": str(year),
                                   "regularSeasonStartDate": f"{year}-03-25",
                                   "regularSeasonEndDate": f"{year}-09-27"}]})

    plan = extract.plan_matchup_history([2024, 2025], LEAGUE)

    assert plan.failure is None
    assert [year for year, _s, _c in plan.seasons] == [2024, 2025]
    assert all(calendar["seasonId"] == year
               for year, _s, calendar in plan.seasons)


def test_all_seasons_requires_the_schedule_only_spelling(run_extract):
    """It captures schedules and nothing else, so pairing it with a
    box-score run would run something other than what was typed."""
    payload = _payload((7, 7), current=3, latest=14)

    with pytest.raises(SystemExit) as excinfo:
        run_extract(payload, all_seasons=True)

    message = str(excinfo.value)
    assert "--matchup-schedule-only" in message
    assert "not download historical box scores" in message.lower()


def test_a_single_season_schedule_run_never_walks_the_registry(run_extract):
    """--year Y stays Y. The history spelling is explicit or it does not
    happen."""
    payload = _payload((7, 7), current=3, latest=14, season_year=2025)

    _sink, asked, _code = run_extract(payload, matchup_schedule_only=True,
                                      year=2025)

    assert asked == [(2025, ("mMatchupScore",))]


# ===========================================================================
# The opener anchor rides the same run
# ===========================================================================
def test_a_box_score_run_captures_the_season_opener(run_extract):
    payload = _payload((7, 7, 7), current=4, latest=21)

    sink, _asked, code = run_extract(payload)

    assert code == 0
    assert len(sink.season_calendars) == 1
    stored, year, league_key = sink.season_calendars[0]
    assert (year, league_key) == (SEASON, LEAGUE)
    assert stored["regularSeasonStartDate"] == f"{SEASON}-03-25"
    assert stored["anchor_field"] == "regularSeasonStartDate"


def test_an_unreachable_calendar_warns_and_the_run_still_extracts(
        run_extract, monkeypatch, capsys):
    """Box scores need no dates at all (rung 4B-1), so a public API being
    briefly unreachable must not block the weekly pull. The consequence is
    visible rather than silent: no anchor stored, so no derived dates."""
    def _boom(year):
        raise ConnectionError("statsapi unreachable")

    monkeypatch.setattr(extract, "fetch_season_calendar", _boom)
    payload = _payload((7, 7, 7), current=4, latest=21)

    sink, _asked, code = run_extract(payload)

    assert code == 0
    assert sink.season_calendars == []
    assert [mp for mp, _sps in sink.box_scores] == [1, 2, 3]
    assert "stay unresolved" in capsys.readouterr().out


def test_a_malformed_calendar_stores_no_anchor(run_extract, monkeypatch,
                                               capsys):
    """A wrong opener does not produce missing dates, it produces confident
    wrong ones for every period in the season. So it is refused, not stored."""
    monkeypatch.setattr(
        extract, "fetch_season_calendar",
        lambda year: {"seasons": [{"seasonId": str(year),
                                   "regularSeasonStartDate": "not-a-date",
                                   "regularSeasonEndDate": f"{year}-09-27"}]})
    payload = _payload((7, 7, 7), current=4, latest=21)

    sink, _asked, code = run_extract(payload)

    assert code == 0
    assert sink.season_calendars == []
    assert "not usable" in capsys.readouterr().out


def test_a_settings_only_run_does_not_pay_for_the_calendar_either(run_extract):
    payload = _payload((7, 7), current=3, latest=14)
    sink = _RecordingSink()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(extract, "extract_league_settings", lambda s, y, k: None)
    try:
        sink, _asked, _code = run_extract(payload, sink, settings_only=True)
    finally:
        monkey.undo()

    assert sink.season_calendars == []


@pytest.mark.parametrize("overrides, needle", [
    ({"periods": [3]}, "positional"),
    ({"all": True}, "--all"),
    ({"backfill_club_of_game": True}, "--backfill-club-of-game"),
    ({"overwrite_day_accurate_history": True},
     "--overwrite-day-accurate-history"),
])
def test_schedule_only_refuses_box_score_flags_rather_than_ignoring_them(
        run_extract, overrides, needle):
    payload = _payload((7, 7), current=3, latest=14)

    with pytest.raises(SystemExit) as excinfo:
        run_extract(payload, matchup_schedule_only=True, **overrides)

    assert needle in str(excinfo.value)
    assert "--matchup-schedule-only" in str(excinfo.value)
