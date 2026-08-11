"""The WEEKLY PLAYER AND TEAM CHAIN, built with no calendar seed (MLB-235 4B-2).

WHAT THIS ADDS OVER tests/test_derived_calendar_build.py. That one proves
`dim_matchup_period` comes out non-empty and dated from RAW alone. It does not
prove anything downstream still works -- and the stranger-facing claim is not
"the dimension builds", it is "you get weekly numbers without typing a
calendar". So this builds the real ESPN player-performance chain over
synthetic-but-contract-valid RAW and asserts that weekly PLAYER and TEAM
performance rows come out, with their matchup periods and dates traceable to
platform membership rather than to a seed.

IT IS THE EXACT FRESH-CLONE STATE. The seed directory is materialised from
`git show HEAD:` -- the COMMITTED header-only templates, all fourteen of them,
byte for byte what a stranger gets. Not the demo fixture (that one is
populated, so it would prove the easy case), and deliberately NOT the
maintainer's working dbt_league/league_config: those files are skip-worktree
and hold real league data on disk, so reading them would leak private content
into a test and pass for exactly one person.

AND IT RUNS THE COMMANDS THE QUICKSTART PRINTS: unscoped `dbt seed` and
unscoped `dbt run`, not a narrow `--select`. QUICKSTART says every
league_config file may stay blank and then tells the reader to type those two
commands; anything narrower would test a path nobody was told to take.

NOTHING UNDER TEST IS STUBBED. The fact models are the real ones -- no
hand-created ANALYTICS relation standing in for the player fact, which would
prove only that a stub can be selected from. The only thing this file creates
is RAW, exactly as the extract's own sinks would write it.

Isolated: a throwaway DuckDB under pytest's temp root and a throwaway seed
directory. Nothing from data/duckdb/, data/parquet/raw/, the maintainer's
league_config or the preserved rehearsal is read or written.

THE SHAPES ARE THE CONTRACT'S, not invented: every RAW table is created from
config/raw_schema_contract.json, so a column this fixture gets wrong is a
column the real loader would get wrong too. The CBS and MLB-spine tables are
created EMPTY -- present-and-empty is the supported ESPN-only installation
state (MLB-222 C-5), and it is what a stranger actually has.
"""

import csv
import json
import os
import shutil
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
CONTRACT = REPO_ROOT / "config" / "raw_schema_contract.json"

LEAGUE = "espn-stranger"
SEASON = 2026
OPENER = date(2026, 3, 25)

# Three closed matchup periods: a long opening week and two ordinary ones, so
# the derived dates have something other than a uniform seven to reproduce.
PERIOD_LENGTHS = [12, 7, 7]
TEAMS = ((1, "Anchor Analytics", "AAA"), (2, "Boundary Bats", "BBB"))

# The weekly facts are built from the FIRST period only; twelve scoring
# periods of two teams is enough to prove the chain and small enough to stay
# a unit test. Later periods carry membership (so the calendar has range) and
# no box scores, which is also a real state -- a period captured before its
# box scores were pulled.
BOX_SCORE_PERIOD = 1


def _skip_loudly(reason):
    banner = f"[SKIPPED - NOT VERIFIED] {reason}"
    warnings.warn(banner, UserWarning, stacklevel=2)
    print(f"\n!! {banner}", file=sys.stderr)
    pytest.skip(reason)


# ---------------------------------------------------------------------------
# RAW payloads, shaped exactly as extract/extract.py writes them
# ---------------------------------------------------------------------------
def _membership():
    """{matchup_period: [scoring periods]}, contiguous and abutting."""
    periods, scoring_period = {}, 1
    for index, length in enumerate(PERIOD_LENGTHS, start=1):
        periods[index] = list(range(scoring_period, scoring_period + length))
        scoring_period += length
    return periods


def _side(scoring_periods):
    return {"teamId": 987654,
            "pointsByScoringPeriod": {str(sp): 0.0 for sp in scoring_periods}}


def _matchup_schedule_payload():
    schedule = []
    for period, members in _membership().items():
        schedule.append({"matchupPeriodId": period,
                         "home": _side(members), "away": _side(members)})
    return {"seasonId": SEASON,
            "status": {"currentMatchupPeriod": len(PERIOD_LENGTHS) + 1},
            "schedule": schedule}


def _player(player_id, name, slot, points, breakdown):
    """One lineup entry, in serialize_box_scores' shape."""
    return {"name": name, "playerId": player_id, "position": "1B",
            "lineupSlot": slot, "proTeam": "SF", "clubOfGame": "SF",
            "points": points, "breakdown": breakdown,
            "games_played": 1 if breakdown else 0,
            "eligibleSlots": ["1B", "UTIL", "BE"]}


def _lineup(team_index):
    """Two active hitters and one benched, so active/inactive both have rows."""
    base = team_index * 100
    return [
        _player(base + 1, f"Active One {team_index}", "1B", 3.0,
                {"AB": 4, "H": 2, "R": 1}),
        _player(base + 2, f"Active Two {team_index}", "C", 1.0,
                {"AB": 3, "H": 1}),
        _player(base + 3, f"Benched {team_index}", "BE", 5.0,
                {"AB": 4, "H": 3}),
    ]


def _box_score_blob():
    """One fantasy matchup between the two teams, plus a free agent."""
    (home_id, home_name, home_abbrev), (away_id, away_name, away_abbrev) = TEAMS
    return {
        "matchups": [{
            "home_team": home_name, "home_team_id": home_id,
            "home_team_abbrev": home_abbrev, "home_owner": "Owner A",
            "away_team": away_name, "away_team_id": away_id,
            "away_team_abbrev": away_abbrev, "away_owner": "Owner B",
            "home_score": 40.0, "away_score": 35.0, "is_bye": False,
            "home_lineup": _lineup(1), "away_lineup": _lineup(2),
        }],
        "free_agents": [
            _player(900, "Free Agent", "FA", 2.0, {"AB": 2, "H": 1}),
        ],
    }


SCORING_ITEMS = [
    {"statId": 0, "points": 0.0, "isReverseItem": False},   # AB
    {"statId": 1, "points": 1.0, "isReverseItem": False},   # H
    {"statId": 20, "points": 1.0, "isReverseItem": False},  # R
]

TEAM_OWNERS = [{"team_id": team_id, "team_name": name, "team_abbrev": abbrev,
                "owner": f"Owner {abbrev}", "owner_id": f"{{OWNER-{team_id}}}"}
               for team_id, name, abbrev in TEAMS]


def _calendar_snapshot():
    """Built through the real capture code, not hand-written."""
    sys.path.insert(0, str(REPO_ROOT / "extract"))
    from season_calendar import season_calendar_snapshot

    return season_calendar_snapshot(
        {"seasons": [{"seasonId": str(SEASON),
                      "regularSeasonStartDate": OPENER.isoformat(),
                      "regularSeasonEndDate": f"{SEASON}-09-27"}]},
        season_year=SEASON)


# ---------------------------------------------------------------------------
# The throwaway warehouse
# ---------------------------------------------------------------------------
_DUCKDB_TYPES = {"TEXT": "varchar", "BOOLEAN": "boolean", "DATE": "date",
                 "TIMESTAMP_NTZ": "timestamp", "TIMESTAMP_TZ": "timestamptz",
                 "VARIANT": "json", "FLOAT": "double", "REAL": "double",
                 "DOUBLE": "double"}


def _duckdb_type(column):
    declared = column["snowflake_type"]
    if declared in _DUCKDB_TYPES:
        return _DUCKDB_TYPES[declared]
    if declared == "NUMBER":
        scale = column["scale"] or 0
        precision = column["precision"] or 38
        return "decimal(38,0)" if scale == 0 else f"decimal({precision},{scale})"
    raise AssertionError(f"no DuckDB mapping for contract type {declared!r}")


def _committed_blank_templates(root):
    """The fourteen committed league_config templates, from git.

    `git show HEAD:` rather than the working tree, and that is a privacy rule
    rather than a convenience: every file under dbt_league/league_config/ is
    skip-worktree, so the path ON DISK holds the maintainer's real league data
    while the path COMMITTED holds a blank template. Copying the working tree
    would pull private content into a temporary directory and make the test
    pass for one person and fail for everyone -- and reading it at all is what
    CLAUDE.md tells every session not to do.

    So this is literally what a stranger clones: header row, no rows.
    """
    seed_dir = root / "committed_league_config"
    seed_dir.mkdir()
    listed = subprocess.run(
        ["git", "ls-tree", "--name-only", "HEAD", "dbt_league/league_config/"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=True)
    templates = [line for line in listed.stdout.split() if line.endswith(".csv")]
    assert templates, "no committed league_config templates found"

    for path in templates:
        shown = subprocess.run(["git", "show", f"HEAD:{path}"],
                               cwd=str(REPO_ROOT), capture_output=True,
                               text=True, check=True)
        body = shown.stdout.splitlines()
        assert len([line for line in body[1:] if line.strip()]) == 0, (
            f"{path} is committed with data rows; this fixture is the EMPTY "
            f"installation and would no longer be testing it")
        (seed_dir / Path(path).name).write_text(
            (body[0] if body else "") + "\n", encoding="utf-8")

    return seed_dir, [Path(p).stem for p in templates]


@pytest.fixture(scope="module")
def weekly(tmp_path_factory):
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        _skip_loudly(f"duckdb not importable ({exc}); the chain was NOT built")

    root = tmp_path_factory.mktemp("mlb235_weekly")
    db = root / "ESPN_FANTASY.duckdb"
    seed_dir, template_names = _committed_blank_templates(root)
    contract = json.loads(CONTRACT.read_text())["tables"]

    con = duckdb.connect(str(db))
    con.execute("create schema if not exists RAW")
    # EVERY contract table, from the contract. The ESPN ones get rows; the
    # CBS and MLB-spine ones stay empty, which is the supported ESPN-only
    # installation state and what a stranger actually has.
    for table, columns in sorted(contract.items()):
        cols = ", ".join(f'"{c["name"]}" {_duckdb_type(c)}' for c in columns)
        con.execute(f"create table RAW.{table} ({cols})")

    stamped = datetime(2026, 8, 11, 12, 0, 0)

    def snapshot(table, payload):
        con.execute(
            f"insert into RAW.{table} (SEASON_YEAR, RAW_JSON, EXTRACTED_AT, "
            f"LEAGUE_KEY) values (?, ?, ?, ?)",
            [SEASON, json.dumps(payload), stamped, LEAGUE])

    snapshot("MATCHUP_SCHEDULE", _matchup_schedule_payload())
    snapshot("MLB_SEASON_CALENDAR", _calendar_snapshot())
    snapshot("SCORING_SETTINGS", SCORING_ITEMS)
    snapshot("TEAM_OWNERS", TEAM_OWNERS)

    blob = _box_score_blob()
    for scoring_period in _membership()[BOX_SCORE_PERIOD]:
        con.execute(
            "insert into RAW.BOX_SCORES (SEASON_YEAR, SCORING_PERIOD, "
            "MATCHUP_PERIOD, RAW_JSON, LOADED_AT, LEAGUE_KEY) "
            "values (?, ?, ?, ?, ?, ?)",
            [SEASON, scoring_period, BOX_SCORE_PERIOD, json.dumps(blob),
             stamped, LEAGUE])
    con.close()

    env = dict(os.environ,
               DBT_DUCKDB_PATH=str(db),
               DBT_LEAGUE_CONFIG=str(seed_dir))

    def _dbt(*args):
        return subprocess.run(
            [sys.executable, "-m", "dbt.cli.main", *args,
             "--project-dir", str(PROJECT_DIR),
             "--profiles-dir", str(PROFILES_DIR), "--target", "duckdb"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)

    # THE TWO COMMANDS THE QUICKSTART PRINTS, unscoped and in order.
    # Narrowing either would test a path nobody was told to take -- and the
    # whole point of this fixture is that "every league_config file may stay
    # blank" and "run these commands" have to be true together.
    seeded = _dbt("seed")
    if seeded.returncode != 0:
        pytest.fail(
            "`dbt seed` over the COMMITTED header-only league_config "
            "templates failed, so a fresh clone cannot complete step 5 of "
            f"the Quickstart:\n{seeded.stdout[-5000:]}")

    result = _dbt("run")
    if result.returncode != 0:
        pytest.fail(
            "the unscoped `dbt run` a stranger is told to type failed on the "
            "exact all-blank installation, so the Quickstart's claim that "
            "every league_config file may stay blank is not true:\n"
            f"{result.stdout[-8000:]}\n{result.stderr[-1500:]}")

    # The declared tests for the models this file is about. Scoped, and only
    # here: the BUILD above was not, so nothing about the installation is
    # being avoided -- this just keeps the assertion about the ESPN weekly
    # chain rather than about the whole project's test surface.
    tested = _dbt("test", "--select",
                  "dim_matchup_period", "fct_player_daily_performance",
                  "fct_player_weekly_slot_performance",
                  "fct_player_weekly_active_performance",
                  "fct_team_weekly_active_performance")
    if tested.returncode != 0:
        pytest.fail("the weekly chain's own dbt tests failed over derived "
                    f"membership:\n{tested.stdout[-6000:]}")

    con = duckdb.connect(str(db))
    query = lambda sql, p=None: con.execute(sql, p or []).fetchall()
    query.build_output = result.stdout
    query.seed_output = seeded.stdout
    query.test_output = tested.stdout
    query.template_names = template_names
    yield query
    con.close()


def _count(weekly, relation):
    return weekly(f"select count(*) from ANALYTICS.{relation}")[0][0]


# ===========================================================================
# 13. The weekly chain survives the seed's removal
# ===========================================================================
def test_every_committed_league_config_template_is_empty_in_this_build(weekly):
    """The control, and it covers all fourteen rather than the calendar alone.

    With a row in any of them, everything below would be measuring a
    configured installation rather than the one a stranger clones.
    """
    populated = {name: _count(weekly, name)
                 for name in weekly.template_names
                 if _count(weekly, name) != 0}

    assert populated == {}
    assert len(weekly.template_names) == 14
    assert "matchup_schedule" in weekly.template_names


def test_the_unscoped_seed_loaded_every_template(weekly):
    """`dbt seed` is the stranger's first command over these files, and an
    untyped empty CSV is exactly where it used to be able to fail."""
    assert "ERROR=0" in weekly.seed_output
    assert "Completed successfully" in weekly.seed_output


def test_the_unscoped_run_built_the_whole_project(weekly):
    """THE CONTRADICTION THIS CLOSES. QUICKSTART says every league_config
    file may stay blank and then prints an unscoped `dbt run`. On the exact
    all-blank state that run used to die: an empty CSV has nothing to infer a
    type from, so `league_key` arrived INTEGER and met the VARCHAR one from
    stg_box_scores in int_franchise_registry, int_cbs__team_owner_season and
    stg_cbs__mlbam_crosswalk. Declaring the types in dbt_project.yml is what
    makes the two halves of that sentence true together."""
    assert "ERROR=0" in weekly.build_output
    assert "SKIP=0" in weekly.build_output
    assert "Completed successfully" in weekly.build_output


def test_the_cbs_only_models_build_empty_rather_than_erroring(weekly):
    """Neutral empty-table compatibility, and nothing more.

    These three are the ones that USED to fail on this exact state, and the
    fix must leave them saying the true thing about a league with no CBS
    configuration: zero rows. Not an error, and not rows borrowed from
    somewhere else -- an empty CBS seed must never acquire ESPN semantics.
    """
    for relation in ("int_cbs__team_owner_season", "stg_cbs__mlbam_crosswalk",
                     "int_cbs__player_daily"):
        count = _count(weekly, relation)
        assert count == 0, f"{relation} invented {count} row(s) from blank config"


def test_the_platform_general_registry_carries_espn_and_only_espn(weekly):
    """int_franchise_registry is NOT CBS-only, which is why it is asserted
    separately rather than expected to be empty: it derives franchises from
    whatever box scores exist, and synthesizes the holding pen for every
    league. With a blank CBS seed it must carry this league's ESPN
    franchises and no CBS ones -- an empty seed producing a CBS franchise
    would be exactly the fabrication this fix must not make.
    """
    rows = weekly("""
        select league_key, count(*) from ANALYTICS.int_franchise_registry
        group by 1 order by 1""")
    by_league = {key: int(n) for key, n in rows}

    assert set(by_league) == {LEAGUE}, by_league
    # Two observed teams plus the synthesized holding pen.
    assert by_league[LEAGUE] == len(TEAMS) + 1


def test_no_matchup_period_row_comes_from_the_legacy_seed(weekly):
    rows = weekly("""
        select calendar_source, count(*) from ANALYTICS.dim_matchup_period
        group by 1 order by 1""")

    assert dict(rows) == {"derived": len(PERIOD_LENGTHS)}


def test_a_player_weekly_performance_model_is_not_empty(weekly):
    """THE CLAIM. A blank calendar seed used to mean no weekly surfaces at
    all; this is the model behind them, built from RAW alone."""
    assert _count(weekly, "fct_player_weekly_slot_performance") > 0
    assert _count(weekly, "fct_player_weekly_active_performance") > 0


def test_a_team_weekly_performance_model_is_not_empty(weekly):
    assert _count(weekly, "fct_team_weekly_active_performance") > 0


def test_both_teams_reach_the_team_weekly_fact(weekly):
    """Not just "a row exists": every team that played the week is there."""
    teams = weekly("""
        select distinct team_id from ANALYTICS.fct_team_weekly_active_performance
        where league_key = ? and season_year = ? order by 1""",
        [LEAGUE, SEASON])

    assert [int(t) for (t,) in teams] == sorted(t for t, _n, _a in TEAMS)


def test_the_weekly_rows_carry_real_production(weekly):
    """A chain that produced rows of nothing would satisfy a bare row count
    while proving the numbers never arrived."""
    total = weekly("""
        select sum(cast(total_stat_pts as double))
        from ANALYTICS.fct_player_weekly_active_performance
        where league_key = ? and season_year = ?""", [LEAGUE, SEASON])[0][0]

    assert total is not None and float(total) > 0


# ===========================================================================
# The matchup-period / date relationship is derived, not seeded
# ===========================================================================
def test_the_weekly_facts_sit_on_the_derived_matchup_period(weekly):
    periods = weekly("""
        select distinct matchup_period
        from ANALYTICS.fct_player_weekly_active_performance
        where league_key = ? and season_year = ? order by 1""",
        [LEAGUE, SEASON])

    assert [int(p) for (p,) in periods] == [BOX_SCORE_PERIOD]


def test_that_periods_dates_come_from_the_derived_calendar(weekly):
    """The end of the chain, joined back to its beginning: the week the facts
    sit on is dated by MLB's opener plus ESPN's membership bounds, and by
    nothing a human typed."""
    members = _membership()[BOX_SCORE_PERIOD]
    start, end, source = weekly("""
        select start_date, end_date, calendar_source
        from ANALYTICS.dim_matchup_period
        where league_key = ? and season_year = ? and matchup_period = ?""",
        [LEAGUE, SEASON, BOX_SCORE_PERIOD])[0]

    assert source == "derived"
    assert start == OPENER + timedelta(days=members[0] - 1)
    assert end == OPENER + timedelta(days=members[-1] - 1)
    assert (end - start).days + 1 == PERIOD_LENGTHS[BOX_SCORE_PERIOD - 1] == 12


def test_the_scoring_periods_in_the_facts_are_the_platforms(weekly):
    """The daily grain the weekly rollup is built from spans exactly the
    scoring periods ESPN put in that matchup period."""
    rows = weekly("""
        select min(scoring_period), max(scoring_period), count(distinct scoring_period)
        from ANALYTICS.fct_player_daily_performance
        where league_key = ? and season_year = ?""", [LEAGUE, SEASON])[0]
    members = _membership()[BOX_SCORE_PERIOD]

    assert (int(rows[0]), int(rows[1]), int(rows[2])) == (
        members[0], members[-1], len(members))


def test_the_declared_dbt_tests_passed_over_derived_membership(weekly):
    """The grain and not-null tests are part of the evidence that this chain
    is sound, not just that it compiled -- and they have to run against the
    POPULATED result, which is why the fixture tests after it runs."""
    import re

    # dbt's own summary line, parsed rather than substring-matched: the naive
    # `"ERROR" not in output` is satisfied by nothing and broken by the string
    # "ERROR=0", which is the healthy case.
    summary = re.search(r"PASS=(\d+) WARN=(\d+) ERROR=(\d+) SKIP=(\d+)",
                        weekly.test_output)
    assert summary, weekly.test_output[-2000:]
    passed, _warn, errored, skipped = (int(g) for g in summary.groups())

    assert passed > 0, "no declared test actually ran"
    assert errored == 0
    assert skipped == 0, "a skipped test proves nothing"
    assert "Failure in test" not in weekly.test_output


def test_the_row_counts_are_what_the_fixture_implies(weekly):
    """Counts, not just non-emptiness.

    A chain that lost half its rows to a bad join would still pass every
    "> 0" assertion above. These are derivable from the fixture by hand --
    two teams, three players each, twelve scoring periods, one free agent --
    so a change in any of them is either a fixture edit or a regression.
    """
    counts = {rel: weekly(f"select count(*) from ANALYTICS.{rel}")[0][0]
              for rel in ("dim_matchup_period",
                          "fct_player_daily_performance",
                          "fct_player_weekly_slot_performance",
                          "fct_player_weekly_active_performance",
                          "fct_team_weekly_active_performance",
                          "fct_player_season_performance")}

    assert counts == {
        # Three closed matchup periods, all derived.
        "dim_matchup_period": 3,
        # 12 scoring periods x (2 teams x 3 players + 1 free agent) = 84.
        "fct_player_daily_performance": 84,
        # One row per rostered player-week per slot, plus the free agent: the
        # six rostered players each held one slot all week.
        "fct_player_weekly_slot_performance": 7,
        # The four players in starting slots. The two benched and the free
        # agent are inactive and do not appear here.
        "fct_player_weekly_active_performance": 4,
        # Both teams, one week each.
        "fct_team_weekly_active_performance": 2,
        # Season rollup over the same seven player-seasons.
        "fct_player_season_performance": 7,
    }, counts
