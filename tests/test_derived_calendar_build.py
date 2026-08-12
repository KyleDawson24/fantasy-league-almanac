"""The derived calendar, built for real with an EMPTY legacy seed (MLB-235 4B-2).

WHY THIS FILE EXISTS SEPARATELY from tests/test_dim_matchup_period_contract.py.
That one measures the pioneer installation: the real league_config seed is on
disk, so its calendar answers come from the legacy rows and the derived dates
ride alongside. This one measures the STRANGER: every league_config seed is a
header-only template, exactly as committed, and there is no calendar anywhere
except the one the platform evidence produces.

Before this rung that installation had no dates at all -- `matchup_schedule.csv`
was the only source of them, and QUICKSTART told a new user to type it in
before anything would work. The single assertion this file exists for is that
`dim_matchup_period` now comes out non-empty AND DATED from RAW alone.

WHAT IT PROVES AND WHAT IT DOES NOT. It builds `+dim_matchup_period`, the
weekly calendar dimension every weekly surface joins to, from an empty seed
plus captured RAW. It does NOT build a weekly player FACT: those sit on the
whole box-score/kona chain, which no fixture synthesizes cheaply, and the
existing suite covers them elsewhere. The claim here is precisely "the weekly
calendar dimension is non-empty and dated with an empty seed", which is the
thing that used to be impossible.

Isolated: a throwaway DuckDB under pytest's temp root, a throwaway seed
directory, and nothing from data/duckdb/, data/parquet/raw/ or the private
league_config is read or written.
"""

import csv
import json
import os
import subprocess
import sys
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = REPO_ROOT / "dbt_league"
PROFILES_DIR = PROJECT_DIR / "profiles"
LEAGUE_CONFIG = PROJECT_DIR / "league_config"
STRANGER = "espn-stranger"

# The measured anchors and period shapes, same fixtures as
# tests/test_season_calendar.py and for the same reason: MLB's own published
# regular-season start, and ESPN's own membership sizes.
OPENER = {2025: date(2025, 3, 18), 2026: date(2026, 3, 25)}
PERIOD_LENGTHS = {
    2025: [13] + [7] * 14 + [14] + [7] * 10,
    2026: [12] + [7] * 13 + [14] + [7] * 3,
}
ALL_STAR = {2025: 16, 2026: 15}


def _skip_loudly(reason):
    banner = f"[SKIPPED - NOT VERIFIED] {reason}"
    warnings.warn(banner, UserWarning, stacklevel=2)
    print(f"\n!! {banner}", file=sys.stderr)
    pytest.skip(reason)


def _side(scoring_periods):
    return {"teamId": 987654,
            "pointsByScoringPeriod": {str(sp): 0.0 for sp in scoring_periods}}


def _payload(season_year):
    """Every period CLOSED: current sits one past the last, so the whole
    season is eligible and every period earns a derived date."""
    schedule, scoring_period = [], 1
    for period, length in enumerate(PERIOD_LENGTHS[season_year], start=1):
        members = list(range(scoring_period, scoring_period + length))
        scoring_period += length
        schedule.append({"matchupPeriodId": period,
                         "home": _side(members), "away": _side(members)})
    return {"seasonId": season_year,
            "status": {"currentMatchupPeriod": len(schedule) + 1},
            "schedule": schedule}


def _calendar_snapshot(season_year):
    """What extract/season_calendar.py stores, built through the real code."""
    sys.path.insert(0, str(REPO_ROOT / "extract"))
    from season_calendar import season_calendar_snapshot

    return season_calendar_snapshot(
        {"seasons": [{"seasonId": str(season_year),
                      "regularSeasonStartDate": OPENER[season_year].isoformat(),
                      "regularSeasonEndDate": f"{season_year}-09-27"}]},
        season_year=season_year)


def _empty_seed_dir(root):
    """Header-only copies of every league_config seed -- the COMMITTED state.

    Copied from the working tree's headers rather than from `git show` so the
    column list cannot drift from what dbt expects, and header-only so not one
    row of the maintainer's real league data is read. That distinction is the
    whole point of the fixture: this is the file a stranger clones.
    """
    seed_dir = root / "empty_league_config"
    seed_dir.mkdir()
    for source in sorted(LEAGUE_CONFIG.glob("*.csv")):
        with source.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
        with (seed_dir / source.name).open("w", newline="",
                                           encoding="utf-8") as out:
            csv.writer(out).writerow(header)
    return seed_dir


@pytest.fixture(scope="module")
def stranger(tmp_path_factory):
    """A warehouse holding captured RAW and an empty seed directory."""
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        _skip_loudly(f"duckdb not importable ({exc}); the calendar was NOT built")

    root = tmp_path_factory.mktemp("mlb235_4b2")
    db = root / "ESPN_FANTASY.duckdb"
    seed_dir = _empty_seed_dir(root)

    con = duckdb.connect(str(db))
    con.execute("create schema if not exists RAW")
    for table in ("MATCHUP_SCHEDULE", "SCHEDULE_SETTINGS", "MLB_SEASON_CALENDAR"):
        con.execute(f"""
            create table RAW.{table} (
                SEASON_YEAR decimal(38,0), RAW_JSON json,
                EXTRACTED_AT timestamp, LEAGUE_KEY varchar)
        """)
    stamped = datetime(2026, 8, 11, 12, 0, 0)
    for season_year in sorted(OPENER):
        con.execute("insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)",
                    [season_year, json.dumps(_payload(season_year)), stamped,
                     STRANGER])
        con.execute("insert into RAW.MLB_SEASON_CALENDAR values (?, ?, ?, ?)",
                    [season_year, json.dumps(_calendar_snapshot(season_year)),
                     stamped, STRANGER])
    con.close()

    env = dict(os.environ,
               DBT_DUCKDB_PATH=str(db),
               DBT_LEAGUE_CONFIG=str(seed_dir))
    result = subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "build",
         "--select", "+dim_matchup_period",
         # CAUTIOUS indirect selection -- see the same flag and reasoning in
         # tests/test_dim_matchup_period_contract.py (MLB-229). `+model`
         # selects ancestors; dbt's default eager rule additionally pulls in
         # any test with one selected parent, including tests that span into
         # subgraphs this fixture does not build.
         "--indirect-selection", "cautious",
         "--project-dir", str(PROJECT_DIR), "--profiles-dir", str(PROFILES_DIR),
         "--target", "duckdb"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(
            "the calendar build over an EMPTY legacy seed failed, so a "
            "stranger's install still cannot produce weekly dates:\n"
            f"{result.stdout[-5000:]}\n{result.stderr[-1500:]}")

    con = duckdb.connect(str(db))
    query = lambda sql, p=None: con.execute(sql, p or []).fetchall()
    query.build_output = result.stdout
    yield query
    con.close()


def _row(stranger, season_year, period):
    return stranger("""
        select start_date, end_date, calendar_source, scoring_period_count
        from ANALYTICS.dim_matchup_period
        where league_key = ? and season_year = ? and matchup_period = ?
    """, [STRANGER, season_year, period])[0]


# ===========================================================================
# 13. Empty seed + derived RAW produces a non-empty, DATED calendar
# ===========================================================================
def test_the_legacy_seed_really_is_empty_in_this_build(stranger):
    """The control. If the seed had rows, everything below would be measuring
    the pioneer installation rather than a stranger's."""
    assert stranger("select count(*) from ANALYTICS.matchup_schedule")[0][0] == 0


def test_a_blank_seed_still_produces_matchup_period_rows(stranger):
    rows = stranger("""
        select season_year, count(*) from ANALYTICS.dim_matchup_period
        where league_key = ? group by 1 order by 1""", [STRANGER])

    assert [(int(y), int(n)) for y, n in rows] == [(2025, 26), (2026, 18)]


def test_every_one_of_those_periods_is_dated_from_the_platform(stranger):
    """The claim the Quickstart now makes. Before this rung an empty seed
    meant no dates at all, which is why it told a new user to type one in."""
    undated, total, sources = stranger("""
        select
            count(*) filter (where start_date is null),
            count(*),
            count(*) filter (where calendar_source = 'derived')
        from ANALYTICS.dim_matchup_period where league_key = ?
    """, [STRANGER])[0]

    assert int(undated) == 0
    assert int(total) == 44
    assert int(sources) == 44


def test_no_row_claims_a_legacy_date_it_does_not_have(stranger):
    assert stranger("""
        select count(*) from ANALYTICS.dim_matchup_period
        where league_key = ? and legacy_start_date is not null
    """, [STRANGER])[0][0] == 0


# ===========================================================================
# 19. Scoring period 1 is the opener; period N is opener + N - 1
# ===========================================================================
@pytest.mark.parametrize("season_year", sorted(OPENER))
def test_the_first_period_starts_on_the_season_opener(stranger, season_year):
    start, _end, source, _count = _row(stranger, season_year, 1)

    assert start == OPENER[season_year]
    assert source == "derived"


@pytest.mark.parametrize("season_year", sorted(OPENER))
def test_every_period_maps_its_membership_bounds_to_days(stranger, season_year):
    """The SQL side of the arithmetic, checked against the same day offsets
    the pure module computes -- two implementations, one specification."""
    opener, scoring_period = OPENER[season_year], 1
    for period, length in enumerate(PERIOD_LENGTHS[season_year], start=1):
        first, last = scoring_period, scoring_period + length - 1
        scoring_period += length

        start, end, _source, count = _row(stranger, season_year, period)

        assert start == opener + timedelta(days=first - 1), (season_year, period)
        assert end == opener + timedelta(days=last - 1), (season_year, period)
        assert int(count) == length


# ===========================================================================
# 20. The built calendar equals the committed demo calendar
# ===========================================================================
def test_the_built_calendar_matches_the_hand_maintained_one(stranger):
    """The end-to-end version of the pure comparison: RAW payloads through
    dbt land on the same dates a human maintained by hand."""
    legacy = {}
    with (REPO_ROOT / "demo" / "league_config"
          / "matchup_schedule.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            legacy[(int(row["season_year"]), int(row["matchup_period"]))] = (
                date.fromisoformat(row["start_date"]),
                date.fromisoformat(row["end_date"]))

    built = stranger("""
        select season_year, matchup_period, start_date, end_date
        from ANALYTICS.dim_matchup_period where league_key = ?
        order by season_year, matchup_period""", [STRANGER])

    assert len(built) == 44
    for season_year, period, start, end in built:
        assert (start, end) == legacy[(int(season_year), int(period))], (
            f"{int(season_year)} matchup period {int(period)}")


def test_the_standing_agreement_test_ran_in_this_build(stranger):
    """assert_derived_calendar_matches_legacy is what keeps a future anchor
    from silently moving the calendar, so it has to actually execute."""
    assert "assert_derived_calendar_matches_legacy" in stranger.build_output


# ===========================================================================
# 22. The All-Star break is fourteen days, not eleven
# ===========================================================================
@pytest.mark.parametrize("season_year", sorted(ALL_STAR))
def test_the_all_star_period_spans_fourteen_calendar_days(stranger, season_year):
    period = ALL_STAR[season_year]
    start, end, _source, count = _row(stranger, season_year, period)

    assert int(count) == 14
    assert (end - start).days == 13, "the build compressed the break"


@pytest.mark.parametrize("season_year", sorted(ALL_STAR))
def test_the_period_after_the_break_starts_the_next_day(stranger, season_year):
    """A build that skipped MLB's three no-game days would pull this period
    -- and every one after it -- three days earlier."""
    period = ALL_STAR[season_year]
    _start, end, _source, _count = _row(stranger, season_year, period)
    following_start, _e, _s, _c = _row(stranger, season_year, period + 1)

    assert following_start == end + timedelta(days=1)
