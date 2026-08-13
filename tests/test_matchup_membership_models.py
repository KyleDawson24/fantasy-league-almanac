"""The MLB-235 dbt models, built for real against synthetic evidence.

WHY THIS BUILDS RATHER THAN INSPECTS. A test that reads the model SQL and
asserts on its text proves the SQL was written, not that it computes anything.
These build the five models with dbt against a THROWAWAY DuckDB holding only
fixtures this file wrote, then query the results. Nothing here touches the
private warehouse, the checked-in data/duckdb tree, or any real payload:
`tmp_path_factory` puts the database under pytest's temp root and it dies with
the session.

WHY ONE BUILD SERVES BOTH THE EMPTY AND POPULATED CASES. Every model is a
VIEW, so the build happens once over an EMPTY RAW.MATCHUP_SCHEDULE -- proving
that present-but-empty is a supported installation state and not a build
failure -- and the fixtures are inserted afterwards, with the same views
re-queried. The empty case is therefore not a special path that might rot; it
is the state the models were literally created in.

THE PARITY TESTS ARE THE POINT. `extract/matchup_membership.py` and these
models are two implementations of one specification, and the specification is
subtle in exactly the places a re-implementation goes wrong: which period is
closed, whether zero-point days count, what a bye is, when to refuse. So every
fixture season is pushed through BOTH paths and the answers compared --
status, standard length, abnormal periods, membership rows. A divergence fails
here rather than in a record book two layers downstream.
"""

import json
import os
import shutil
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "extract"))

from matchup_membership import derive_matchup_periods  # noqa: E402

LEAGUE = "espn-main"
OTHER_LEAGUE = "espn-other"
PROJECT_DIR = REPO_ROOT / "dbt_league"
PROFILES_DIR = PROJECT_DIR / "profiles"


# ---------------------------------------------------------------------------
# Synthetic payloads
# ---------------------------------------------------------------------------
def _side(scoring_periods, points=0.0):
    """One participating side. Keys are strings, as JSON delivers them, and
    the team id is here to be ignored -- no model may surface it."""
    return {"teamId": 987654,
            "totalPoints": 123.45,
            "pointsByScoringPeriod": {str(sp): points for sp in scoring_periods}}


def _matchup(period, scoring_periods, *, away=None, bye=False):
    entry = {"id": period * 100, "matchupPeriodId": period,
             "winner": "HOME", "home": _side(scoring_periods)}
    if not bye:
        entry["away"] = _side(away if away is not None else scoring_periods)
    return entry


def _payload(period_lengths, *, season_year, current=None, matchups_per_period=2,
             points=0.0, latest_scoring_period=None, final_scoring_period=None):
    """Periods 1..N with consecutive scoring periods, N matchups each.

    Completion fields are omitted unless asked for, so every fixture written
    before the completion exception existed still exercises the strict policy.
    """
    schedule = []
    sp = 1
    for index, length in enumerate(period_lengths, start=1):
        members = list(range(sp, sp + length))
        sp += length
        for _ in range(matchups_per_period):
            entry = _matchup(index, members)
            if points:
                for side in ("home", "away"):
                    entry[side]["pointsByScoringPeriod"] = {
                        str(m): points for m in members}
            schedule.append(entry)
    status = {"currentMatchupPeriod": current or len(period_lengths) + 1}
    if latest_scoring_period is not None:
        status["latestScoringPeriod"] = latest_scoring_period
    if final_scoring_period is not None:
        status["finalScoringPeriod"] = final_scoring_period
    return {"seasonId": season_year, "status": status, "schedule": schedule}


def _completed(period_lengths, *, season_year, **kwargs):
    """A season ESPN has finished with, shaped like the measured 2025 payload:
    the pointer rests ON the final period and latestScoringPeriod is strictly
    past finalScoringPeriod."""
    total = sum(period_lengths)
    return _payload(period_lengths, season_year=season_year,
                    current=len(period_lengths),
                    final_scoring_period=total,
                    latest_scoring_period=total + 1, **kwargs)


# A SYNTHETIC season carrying two anomalies, one either side of the norm.
# Deliberately not a transcript of a real one: the measured leagues have two
# LONG anomalies (2025 periods 1 and 16 ran 13 and 14 days), and a fixture
# with outliers on both sides proves the mode survives either.
ORDINARY = [10] + [7] * 12 + [4] + [7] * 3


def _fixtures():
    """(league_key, season_year, payload, note) -- every scenario, one table."""
    rows = []

    # Ordinary season, every period seven days.
    rows.append((LEAGUE, 2020, _payload([7] * 8, season_year=2020),
                 "ordinary sevens"))

    # Two synthetic anomalies, one long (10) and one short (4).
    rows.append((LEAGUE, 2021, _payload(ORDINARY, season_year=2021),
                 "outliers either side of the norm"))

    # Every value is exactly zero. Membership is the KEY set, so nothing moves.
    rows.append((LEAGUE, 2022, _payload([7] * 8, season_year=2022, points=0.0),
                 "zero-point days"))

    # Period 5 is in flight and reads short. Excluded structurally, so the
    # standard stays seven and nothing is flagged abnormal.
    rows.append((LEAGUE, 2023, _payload([7, 7, 7, 7, 3], season_year=2023,
                                        current=5),
                 "current period excluded"))

    # Home and away disagree in period 2 -> the whole season is malformed.
    disagree = _payload([7] * 8, season_year=2024)
    disagree["schedule"][2]["away"] = _side([1, 2, 3, 4, 5, 6])
    rows.append((LEAGUE, 2024, disagree, "home/away disagreement"))

    # Two closed periods, below the floor.
    rows.append((LEAGUE, 2025, _payload([7] * 6, season_year=2025, current=3),
                 "too little evidence"))

    # 7,7,10,10 -- the modal length ties.
    rows.append((LEAGUE, 2026, _payload([7, 7, 10, 10], season_year=2026),
                 "tied mode"))

    # ESPN answered with an empty schedule.
    rows.append((LEAGUE, 2019, {"seasonId": 2019,
                                "status": {"currentMatchupPeriod": 1},
                                "schedule": []},
                 "unavailable"))

    # A hole in the closed run: period 3 never arrives.
    gap = _payload([7] * 6, season_year=2017, current=7)
    gap["schedule"] = [e for e in gap["schedule"] if e["matchupPeriodId"] != 3]
    rows.append((LEAGUE, 2017, gap, "gap in the closed run"))

    # ESPN's own seasonId disagrees with the season the loader filed it under.
    rows.append((LEAGUE, 2016, _payload([7] * 8, season_year=2015),
                 "season identity mismatch"))

    # A second league, different shape, to prove league scoping.
    rows.append((OTHER_LEAGUE, 2020, _payload([5] * 6, season_year=2020),
                 "other league, five-day periods"))

    # -- the completion exception ------------------------------------------
    # Finished: pointer ON the final period, last scoring day passed, and the
    # final period's membership ends exactly at finalScoringPeriod.
    rows.append((LEAGUE, 2015, _completed([7] * 6, season_year=2015),
                 "completed season promotes its final period"))

    # The SAME schedule and pointer, still live. Only the completion evidence
    # differs, and the final period must stay out.
    rows.append((LEAGUE, 2014, _payload([7] * 6, season_year=2014, current=6,
                                        latest_scoring_period=30,
                                        final_scoring_period=42),
                 "live season keeps the final period out"))

    # Equal does not prove completion -- the final scoring day may be today.
    rows.append((LEAGUE, 2013, _payload([7] * 6, season_year=2013, current=6,
                                        latest_scoring_period=42,
                                        final_scoring_period=42),
                 "latest equal to final is not completion"))

    # Completion is claimed, but this period does not reach the end of it.
    rows.append((LEAGUE, 2012, _payload([7] * 6, season_year=2012, current=6,
                                        latest_scoring_period=99,
                                        final_scoring_period=98),
                 "claimed completion, period stops short"))

    # Completed, with periods ESPN scheduled beyond the pointer. Promotion
    # reaches exactly one period and no further.
    future = _completed([7] * 6, season_year=2011)
    future["schedule"].append({"matchupPeriodId": 7, "home": {}, "away": {}})
    rows.append((LEAGUE, 2011, future, "completed with future periods beyond"))

    # Completed, but the candidate's own sides disagree -- demoted, and the
    # season keeps every earlier period rather than turning malformed.
    contested = _completed([7] * 6, season_year=2010)
    contested["schedule"][-1]["away"] = _side([36, 37, 38])
    rows.append((LEAGUE, 2010, contested, "completed but candidate contested"))

    # -- completion evidence that is the right VALUE and the wrong TYPE -----
    # These ride the parity suite deliberately. Unwrapping JSON to text makes
    # the number 43 and the string "43" identical, so a numeric regex accepts
    # both while the pure parser's isinstance(value, int) rejects the string.
    # Only a JSON-TYPE test keeps the two implementations agreeing, and only a
    # PARITY fixture proves they do.
    string_latest = _completed([7] * 6, season_year=2009)
    string_latest["status"]["latestScoringPeriod"] = "43"
    rows.append((LEAGUE, 2009, string_latest, "latestScoringPeriod is a string"))

    string_final = _completed([7] * 6, season_year=2008)
    string_final["status"]["finalScoringPeriod"] = "42"
    rows.append((LEAGUE, 2008, string_final, "finalScoringPeriod is a string"))

    # The other JSON types, each representable in a stored payload.
    for season, latest, final, note in (
        (2007, True, 42, "boolean latest"),
        (2006, 43.5, 42, "fractional latest"),
        (2005, [43], 42, "array latest"),
        (2004, 43, {"v": 42}, "object final"),
        (2003, 43, None, "final is JSON null"),
    ):
        payload = _completed([7] * 6, season_year=season)
        payload["status"]["latestScoringPeriod"] = latest
        payload["status"]["finalScoringPeriod"] = final
        rows.append((LEAGUE, season, payload, note))

    return rows


# Two snapshots of ONE season: the older saw period 3 in flight, the newer has
# closed it. Deterministic selection must take the newer, or the derivation
# describes a season that has moved on.
STALE_SNAPSHOT = _payload([7] * 8, season_year=2018, current=3)
FRESH_SNAPSHOT = _payload([7] * 8, season_year=2018, current=6)


# ---------------------------------------------------------------------------
# The throwaway warehouse
# ---------------------------------------------------------------------------
def _skip_loudly(reason):
    """A silent skip reads as a pass in a scroll-back."""
    banner = f"[SKIPPED - NOT VERIFIED] {reason}"
    warnings.warn(banner, UserWarning, stacklevel=2)
    print(f"\n!! {banner}", file=sys.stderr)
    pytest.skip(reason)


def _run_dbt(args, db_path):
    env = dict(os.environ)
    env["DBT_DUCKDB_PATH"] = str(db_path)
    # The fixture warehouse holds ONLY what this file wrote; the real one is
    # never named, and the profile's default path is overridden above.
    return subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", *args,
         "--project-dir", str(PROJECT_DIR),
         "--profiles-dir", str(PROFILES_DIR),
         "--target", "duckdb"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Build the five models over an EMPTY fixture warehouse, then populate.

    Returns a callable running SQL against the result. The build happening
    while RAW is empty is the empty-source assertion: if present-but-empty
    were not supported, there would be nothing to query.
    """
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        _skip_loudly(f"duckdb not importable ({exc}); MLB-235 models NOT built")

    db_path = tmp_path_factory.mktemp("mlb235_fixture") / "ESPN_FANTASY.duckdb"

    con = duckdb.connect(str(db_path))
    con.execute("create schema if not exists RAW")
    # The RAW contract's shape, as tools/load_parquet_to_duckdb.py lands it:
    # NUMBER(38,0) -> DECIMAL(38,0), VARIANT -> JSON.
    con.execute("""
        create table RAW.MATCHUP_SCHEDULE (
            SEASON_YEAR  decimal(38,0),
            RAW_JSON     json,
            EXTRACTED_AT timestamp,
            LEAGUE_KEY   varchar
        )
    """)
    con.close()

    # dim_matchup_period is a descendant but belongs to rung 4A's contract and
    # needs the calendar + override SEEDS, which this fixture deliberately
    # does not carry: these tests are about the derivation, not about how a
    # consumer resolves it against manual decisions. It has its own build in
    # tests/test_dim_matchup_period_contract.py.
    #
    # dim_league_format is another descendant now that ESPN's explicit type-5
    # signal can identify season-points leagues before standings exist. Its
    # other inputs belong to the rivalry fixture, not this membership fixture.
    #
    # int_league_season_closure is the same shape of exclusion (MLB-229): it
    # reads the schedule capture, which makes it a descendant, and it needs the
    # standings feeds this fixture has no reason to carry. Its whole subtree --
    # season points, the rivalry ledger, and those models' tests -- goes with
    # it. tests/test_franchise_rivalry.py builds them.
    result = _run_dbt(["build", "--select", "stg_matchup_schedule+",
                       "--exclude", "dim_matchup_period+",
                       "dim_league_format+",
                       "int_league_season_closure+"], db_path)
    if result.returncode != 0:
        pytest.fail("dbt build over an EMPTY RAW.MATCHUP_SCHEDULE failed, so "
                    "present-but-empty is not a supported state:\n"
                    f"{result.stdout[-4000:]}\n{result.stderr[-2000:]}")

    con = duckdb.connect(str(db_path))

    def query(sql, params=None):
        return con.execute(sql, params or []).fetchall()

    query.db_path = db_path
    query.build_output = result.stdout

    # Everything empty, asserted before a single fixture row exists.
    query.empty_counts = {
        model: query(f"select count(*) from ANALYTICS.{model}")[0][0]
        for model in ("stg_matchup_schedule", "int_matchup_period_evidence",
                      "int_matchup_season_derivation", "int_matchup_period_shape",
                      "int_matchup_period_membership")
    }

    stamped = datetime(2026, 8, 11, 12, 0, 0)
    for league, season, payload, _note in _fixtures():
        con.execute(
            "insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)",
            [season, json.dumps(payload), stamped, league])
    # The stale snapshot is written LATER in wall-clock insert order but
    # carries the EARLIER extracted_at, so a model that accidentally took
    # "whatever came last" would pick the wrong one.
    con.execute("insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)",
                [2018, json.dumps(FRESH_SNAPSHOT), datetime(2026, 8, 10), LEAGUE])
    con.execute("insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)",
                [2018, json.dumps(STALE_SNAPSHOT), datetime(2026, 8, 1), LEAGUE])
    con.execute("insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)", [
        2027,
        json.dumps({
            "seasonId": 2027,
            "status": {"currentMatchupPeriod": 1,
                       "latestScoringPeriod": 142,
                       "currentLeagueType": 5,
                       "createdAsLeagueType": 5},
            "schedule": [{"matchupPeriodId": 1}],
        }),
        datetime(2026, 8, 11), OTHER_LEAGUE,
    ])

    yield query
    con.close()


@pytest.fixture(scope="module")
def status_by_season(built):
    return {(row[0], int(row[1])): row[2] for row in built(
        "select league_key, season_year, derivation_status "
        "from ANALYTICS.int_matchup_season_derivation")}


def _sql_report(built, season, league=LEAGUE):
    """The SQL side's answer for one season, shaped like the Python report."""
    header = built(
        "select derivation_status, standard_period_length "
        "from ANALYTICS.int_matchup_season_derivation "
        "where league_key = ? and season_year = ?", [league, season])
    periods = built(
        "select matchup_period, scoring_period_count, is_abnormal_derived "
        "from ANALYTICS.int_matchup_period_shape "
        "where league_key = ? and season_year = ? order by matchup_period",
        [league, season])
    membership = built(
        "select matchup_period, scoring_period "
        "from ANALYTICS.int_matchup_period_membership "
        "where league_key = ? and season_year = ? "
        "order by matchup_period, scoring_period", [league, season])
    return {
        "status": header[0][0] if header else None,
        "standard": header[0][1] if header else None,
        "periods": [(int(p), None if c is None else int(c), a) for p, c, a in periods],
        "membership": [(int(m), int(s)) for m, s in membership],
    }


# ---------------------------------------------------------------------------
# Present-but-empty is a supported installation state
# ---------------------------------------------------------------------------
def test_every_model_builds_over_an_empty_source(built):
    """The capture is opt-in, so a warehouse that has never run it holds this
    table with zero rows. That has to build, not fail."""
    assert built.empty_counts == {
        "stg_matchup_schedule": 0,
        "int_matchup_period_evidence": 0,
        "int_matchup_season_derivation": 0,
        "int_matchup_period_shape": 0,
        "int_matchup_period_membership": 0,
    }


def test_an_empty_source_invents_no_league_season(built):
    """`unavailable` is a statement about the LEAGUE -- ESPN served an empty
    schedule -- not about this installation. A season nobody captured must
    have no row at all, or a consumer cannot tell the two apart."""
    assert built.empty_counts["int_matchup_season_derivation"] == 0


def test_the_dbt_tests_ran_in_the_empty_build(built):
    """`dbt build`, not `dbt run`: the grain and accepted-values tests are
    part of the evidence that this compiled, not just that it parsed."""
    assert "PASS" in built.build_output or "Completed successfully" in built.build_output


# ---------------------------------------------------------------------------
# Deterministic latest-snapshot selection
# ---------------------------------------------------------------------------
def test_the_latest_snapshot_wins(built):
    """Membership is retrospective, so two snapshots of one season genuinely
    differ -- the later has closed a period the earlier had in flight. The
    newest is the only one that describes the season as it stands."""
    rows = built("select current_matchup_period from ANALYTICS.stg_matchup_schedule "
                 "where league_key = ? and season_year = 2018", [LEAGUE])

    assert rows == [(6,)], "expected the fresher snapshot's currentMatchupPeriod"


def test_the_latest_snapshot_choice_reaches_the_derivation(built):
    """The stale snapshot would have shown 2 closed periods (below the floor);
    the fresh one shows 5."""
    assert _sql_report(built, 2018)["status"] == "derived"
    assert built("select closed_period_count from "
                 "ANALYTICS.int_matchup_season_derivation "
                 "where league_key = ? and season_year = 2018", [LEAGUE]) == [(5,)]


# ---------------------------------------------------------------------------
# Ordinary membership
# ---------------------------------------------------------------------------
def test_ordinary_seven_day_membership(built):
    report = _sql_report(built, 2020)

    assert report["status"] == "derived"
    assert report["standard"] == 7
    assert [c for _p, c, _a in report["periods"]] == [7] * 8
    assert report["membership"][:8] == [(1, 1), (1, 2), (1, 3), (1, 4),
                                        (1, 5), (1, 6), (1, 7), (2, 8)]


def test_a_long_opening_and_a_short_all_star_period_are_flagged(built):
    report = _sql_report(built, 2021)

    assert report["status"] == "derived"
    assert report["standard"] == 7
    assert [p for p, _c, a in report["periods"] if a] == [1, 14]
    assert dict((p, c) for p, c, _a in report["periods"])[1] == 10
    assert dict((p, c) for p, c, _a in report["periods"])[14] == 4


def test_zero_point_scoring_periods_stay_in_the_membership(built):
    """Membership is the KEY set. Dropping zeroes would shorten real weeks
    into fake abnormalities."""
    report = _sql_report(built, 2022)

    assert report["status"] == "derived"
    assert len(report["membership"]) == 56
    assert all(c == 7 for _p, c, _a in report["periods"])


def test_the_current_period_is_excluded(built):
    """Period 5 is in flight and reads 3 days. Structurally excluded, so it
    neither drags the mode nor appears as an abnormality."""
    report = _sql_report(built, 2023)

    assert report["status"] == "derived"
    assert report["standard"] == 7
    assert [p for p, _c, _a in report["periods"]] == [1, 2, 3, 4]
    assert 5 not in {m for m, _s in report["membership"]}
    assert not any(a for _p, _c, a in report["periods"])


def test_type_five_is_reportable_without_being_closed_h2h(built):
    evidence = built("""
        select matchup_period, is_closed, is_reportable,
               is_season_points_period, scoring_period_count,
               min_scoring_period, max_scoring_period
        from ANALYTICS.int_matchup_period_evidence
        where league_key = ? and season_year = 2027
    """, [OTHER_LEAGUE])
    shape = built("""
        select matchup_period, is_abnormal_derived, derivation_status
        from ANALYTICS.int_matchup_period_shape
        where league_key = ? and season_year = 2027
    """, [OTHER_LEAGUE])

    assert evidence == [(1, False, True, True, 142, 1, 142)]
    assert shape == [(1, False, "insufficient_evidence")]


def test_leagues_do_not_bleed_into_each_other(built):
    """Same season year, different league, different standard."""
    assert _sql_report(built, 2020, OTHER_LEAGUE)["standard"] == 5
    assert _sql_report(built, 2020, LEAGUE)["standard"] == 7


# ---------------------------------------------------------------------------
# The completion exception
# ---------------------------------------------------------------------------
def _promoted(built, season, league=LEAGUE):
    rows = built("select promoted_final_period from "
                 "ANALYTICS.int_matchup_season_derivation "
                 "where league_key = ? and season_year = ?", [league, season])
    return rows[0][0] if rows else None


def test_a_completed_season_includes_the_period_its_pointer_rests_on(built):
    """The measured case: 2025 came back with the pointer ON period 26 of 26
    and the last scoring day passed, so the final completed week is provable
    rather than lost."""
    report = _sql_report(built, 2015)

    assert report["status"] == "derived"
    assert _promoted(built, 2015) == 6
    assert [p for p, _c, _a in report["periods"]] == [1, 2, 3, 4, 5, 6]
    assert (6, 42) in report["membership"]


def test_the_same_pointer_in_a_live_season_excludes_the_period(built):
    """Same schedule, same pointer. Only the completion evidence differs."""
    report = _sql_report(built, 2014)

    assert _promoted(built, 2014) is None
    assert [p for p, _c, _a in report["periods"]] == [1, 2, 3, 4, 5]
    assert 6 not in {m for m, _s in report["membership"]}


def test_latest_equal_to_final_does_not_prove_completion(built):
    """Equal means the final scoring day may be the day in progress."""
    assert _promoted(built, 2013) is None
    assert len(_sql_report(built, 2013)["periods"]) == 5


def test_a_claimed_completion_that_stops_short_is_not_promoted(built):
    """The status block says the season is over; the schedule says this period
    is not where it ended."""
    assert _promoted(built, 2012) is None
    assert len(_sql_report(built, 2012)["periods"]) == 5


def test_periods_beyond_the_pointer_stay_excluded_when_promoting(built):
    """Promotion reaches exactly one period."""
    report = _sql_report(built, 2011)

    assert _promoted(built, 2011) == 6
    assert max(p for p, _c, _a in report["periods"]) == 6


def test_a_contested_candidate_demotes_rather_than_condemning_the_season(built):
    """The difference between the candidate and every other period: failing
    its shape check costs one period, not twenty-five."""
    report = _sql_report(built, 2010)

    assert report["status"] == "derived"
    assert _promoted(built, 2010) is None
    assert [p for p, _c, _a in report["periods"]] == [1, 2, 3, 4, 5]


def test_a_numeric_string_is_not_completion_evidence(built):
    """The value is right and the TYPE is wrong. Unwrapping JSON to text makes
    the number 43 and the string "43" identical, so the regex form this
    replaced accepted both -- and promoted a period in SQL that the pure
    parser refused. Only a JSON-type test keeps them agreeing."""
    for season in (2009, 2008):
        assert _promoted(built, season) is None, season
        report = _sql_report(built, season)
        assert report["status"] == "derived", "earlier periods must survive"
        assert [p for p, _c, _a in report["periods"]] == [1, 2, 3, 4, 5], season


@pytest.mark.parametrize("season, note", [
    (2007, "boolean"), (2006, "fractional"), (2005, "array"),
    (2004, "object"), (2003, "json null"),
])
def test_other_wrong_typed_completion_values_stay_strict(built, season, note):
    assert _promoted(built, season) is None, note
    assert len(_sql_report(built, season)["periods"]) == 5


def test_a_genuine_json_integer_still_promotes(built):
    """The control for every rejection above -- the type gate must not have
    closed the door on the case it exists to admit."""
    assert _promoted(built, 2015) == 6
    assert _promoted(built, 2011) == 6


def test_a_strict_fallback_season_reports_no_promotion(built):
    """Every fixture written before the exception existed must still take the
    strict path, which is what keeps the fallback under test."""
    for season in (2020, 2021, 2022, 2023, 2025, 2026):
        assert _promoted(built, season) is None, season


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------
def test_side_disagreement_fails_the_whole_season_closed(built, status_by_season):
    """Contested membership is not membership. Picking the longer set would
    manufacture a normal week and the shorter one an abnormal week."""
    assert status_by_season[(LEAGUE, 2024)] == "malformed"

    report = _sql_report(built, 2024)
    assert report["periods"] == [], "a malformed season published period rows"
    assert report["membership"] == [], "a malformed season published membership"
    assert report["standard"] is None


def test_too_little_evidence_refuses_a_standard(built, status_by_season):
    """The only closed periods early in a season may be the anomalous ones; a
    mode over them would bless the anomaly as the norm."""
    assert status_by_season[(LEAGUE, 2025)] == "insufficient_evidence"

    report = _sql_report(built, 2025)
    assert report["standard"] is None
    assert [p for p, _c, _a in report["periods"]] == [1, 2]
    assert all(a is None for _p, _c, a in report["periods"]), \
        "unknown must be NULL, never false"


def test_a_tied_mode_refuses_a_standard(built, status_by_season):
    assert status_by_season[(LEAGUE, 2026)] == "ambiguous_standard_length"

    report = _sql_report(built, 2026)
    assert report["standard"] is None
    assert all(a is None for _p, _c, a in report["periods"])


def test_an_insufficient_season_still_publishes_its_membership(built):
    """The lengths are known facts; only the norm is missing. Dropping the
    rows would confuse "no norm" with "no evidence"."""
    assert len(_sql_report(built, 2025)["membership"]) == 14


def test_an_empty_schedule_is_unavailable(built, status_by_season):
    assert status_by_season[(LEAGUE, 2019)] == "unavailable"
    assert _sql_report(built, 2019)["periods"] == []


def test_a_gap_in_the_closed_run_is_malformed(built, status_by_season):
    """A mode over a gapped set is a statistic about the gap."""
    assert status_by_season[(LEAGUE, 2017)] == "malformed"
    assert _sql_report(built, 2017)["membership"] == []


def test_a_season_identity_mismatch_is_malformed(built, status_by_season):
    """The row's season_year was stamped by the loader, so it agrees with
    itself no matter which season the document described."""
    assert status_by_season[(LEAGUE, 2016)] == "malformed"
    assert _sql_report(built, 2016)["membership"] == []


def test_unknown_h2h_abnormality_is_never_published_as_false(built):
    """Only the explicit type-5 container may be ordinary without a norm."""
    leaked = built("""
        select count(*) from ANALYTICS.int_matchup_period_shape
        where derivation_status <> 'derived'
          and is_abnormal_derived is not null
          and not (league_key = ? and season_year = 2027)
    """, [OTHER_LEAGUE])
    assert leaked == [(0,)]


# ---------------------------------------------------------------------------
# No identity reaches these models
# ---------------------------------------------------------------------------
def test_no_team_identity_is_exposed_by_any_model(built):
    """Every fixture side carries teamId 987654. Which days a matchup period
    contains is a property of the schedule; the sides are read only to prove
    they agree about it."""
    for model in ("int_matchup_period_evidence", "int_matchup_period_shape",
                  "int_matchup_period_membership", "int_matchup_season_derivation"):
        columns = {c[0].lower() for c in built(f"describe ANALYTICS.{model}")}
        assert not {"team_id", "teamid", "owner", "owner_id"} & columns, model
        rendered = str(built(f"select * from ANALYTICS.{model} limit 200"))
        assert "987654" not in rendered, f"{model} surfaced a team id"


# ---------------------------------------------------------------------------
# Python / SQL parity -- one specification, two implementations
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "league, season, payload, note",
    [(lg, yr, pl, note) for lg, yr, pl, note in _fixtures()],
    ids=[f"{yr}-{note.replace(' ', '-')}" for _lg, yr, _pl, note in _fixtures()],
)
def test_sql_matches_the_pure_derivation(built, league, season, payload, note):
    """The same payload through both paths, compared on every published fact.

    A re-implementation goes wrong in exactly the places this specification is
    subtle -- which period is closed, whether zero-point days count, what a
    bye is, when to refuse -- so the two are held against each other rather
    than each against its own author's intent.
    """
    expected = derive_matchup_periods(
        payload, league_key=league, season_year=season)
    actual = _sql_report(built, season, league)

    assert actual["status"] == expected.status, note
    assert actual["standard"] == expected.standard_period_length, note

    assert [(p.matchup_period, p.scoring_period_count, p.is_abnormal_derived)
            for p in expected.periods] == actual["periods"], note
    assert [(r.matchup_period, r.scoring_period) for r in expected.rows] \
        == actual["membership"], note


def test_the_parity_fixtures_cover_every_status():
    """A parity suite that never exercises a refusal proves only the happy
    path. This fails if a fixture is removed and a status stops being
    represented."""
    seen = {derive_matchup_periods(pl, league_key=lg, season_year=yr).status
            for lg, yr, pl, _n in _fixtures()}

    assert seen == {"derived", "insufficient_evidence",
                    "ambiguous_standard_length", "unavailable", "malformed"}
