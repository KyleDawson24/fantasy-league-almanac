"""The season-long-points RAW shape reaches player staging without a rival.

This is a real dbt build against a throwaway DuckDB. The fixture writes only
the two RAW documents the new seam consumes and two empty seed-shaped lookup
relations. No project data, network, warehouse, or Google surface is touched.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = REPO_ROOT / "dbt_league"
PROFILES_DIR = PROJECT_DIR / "profiles"
LEAGUE = "espn-season-points"
SEASON = 2026


def _lineup(player_id, name, slot, points):
    return {
        "name": name, "playerId": player_id, "position": "1B",
        "lineupSlot": slot, "proTeam": "NYY", "clubOfGame": "NYY",
        "points": points, "breakdown": {"AB": 4, "H": 2},
        "games_played": 1, "eligibleSlots": ["1B", "UTIL"],
    }


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    duckdb = pytest.importorskip("duckdb")
    if not (PROJECT_DIR / "dbt_packages").is_dir():
        pytest.skip("dbt_packages missing; run dbt deps")

    root = tmp_path_factory.mktemp("season_points_models")
    # The profile pins database: ESPN_FANTASY because source() is qualified
    # that way on both engines; DuckDB therefore requires the file stem to
    # match even for an isolated fixture.
    db_path = root / "ESPN_FANTASY.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("create schema RAW")
    con.execute("create schema ANALYTICS")
    con.execute("""
        create table RAW.BOX_SCORES (
            SEASON_YEAR decimal(38,0), SCORING_PERIOD decimal(38,0),
            MATCHUP_PERIOD decimal(38,0), RAW_JSON json,
            LOADED_AT timestamp, LEAGUE_KEY varchar)
    """)
    con.execute("""
        create table RAW.MATCHUP_SCHEDULE (
            SEASON_YEAR decimal(38,0), RAW_JSON json,
            EXTRACTED_AT timestamp, LEAGUE_KEY varchar)
    """)
    # F8, the identity feed (MLB-243). The type-5 mRoster payload carries no
    # team labels, so the roster staging model reads them from here instead
    # of publishing the extract's "Team {id}" placeholder.
    con.execute("""
        create table RAW.TEAM_STANDINGS (
            SEASON_YEAR decimal(38,0), RAW_JSON json,
            EXTRACTED_AT timestamp, LEAGUE_KEY varchar)
    """)
    con.execute("""
        create table ANALYTICS.player_nicknames (
            player_id integer, nickname varchar)
    """)
    con.execute("""
        create table ANALYTICS.slot_classification (
            platform varchar, lineup_slot varchar, slot_category varchar)
    """)
    con.executemany(
        "insert into ANALYTICS.slot_classification values (?, ?, ?)",
        [("espn", "1B", "hitting"), ("espn", "BE", "inactive"),
         ("espn", "FA", "inactive")])

    # Team 1's labels are the PLACEHOLDERS the type-5 extract wrote before
    # MLB-243 -- exactly what the rehearsal warehouse contained. Team 2
    # carries real labels in the payload, so the fallback branch is covered
    # too. Both are synthetic.
    blob = {
        "matchups": [],
        "team_rosters": [
            {"team_name": "Team 1", "team_id": 1,
             "team_abbrev": "1", "owner": "Sample One",
             "lineup": [_lineup(101, "Active Player", "1B", 3.0)]},
            {"team_name": "Example Two", "team_id": 2,
             "team_abbrev": "TWO", "owner": "Sample Two",
             "lineup": [_lineup(202, "Benched Player", "BE", 5.0)]},
        ],
        "free_agents": [_lineup(303, "Available Player", "FA", 1.0)],
    }
    stamped = datetime(2026, 8, 13, 12, 0, 0)
    con.execute(
        "insert into RAW.BOX_SCORES values (?, ?, ?, ?, ?, ?)",
        [SEASON, 1, 1, json.dumps(blob), stamped, LEAGUE])
    # mTeam knows team 1's real name; it says nothing about team 2, whose
    # roster payload therefore keeps its own label.
    con.execute(
        "insert into RAW.TEAM_STANDINGS values (?, ?, ?, ?)",
        [SEASON, json.dumps([
            {"id": 1, "name": "Served Sluggers", "abbrev": "SVD"},
        ]), stamped, LEAGUE])
    schedule = {
        "seasonId": SEASON,
        "status": {"currentMatchupPeriod": 1, "latestScoringPeriod": 1,
                   "currentLeagueType": 5, "createdAsLeagueType": 5},
        "schedule": [{"matchupPeriodId": 1}],
    }
    con.execute(
        "insert into RAW.MATCHUP_SCHEDULE values (?, ?, ?, ?)",
        [SEASON, json.dumps(schedule), stamped, LEAGUE])
    con.close()

    env = dict(os.environ, DBT_DUCKDB_PATH=str(db_path))
    result = subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "run", "--select",
         "stg_box_scores__matchups", "stg_box_scores__team_rosters",
         "stg_box_scores__season_points_players",
         "stg_box_scores__free_agents", "stg_team_standings",
         "stg_box_scores", "stg_matchup_pairs", "stg_matchup_schedule",
         "--project-dir", str(PROJECT_DIR), "--profiles-dir",
         str(PROFILES_DIR), "--target", "duckdb"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail("season-points staging build failed:\n" +
                    result.stdout[-7000:] + result.stderr[-1500:])

    con = duckdb.connect(str(db_path))
    yield lambda sql: con.execute(sql).fetchall()
    con.close()


def test_every_team_reaches_player_staging(built):
    rows = built("""
        select team_id, player_id, home_away, lineup_slot
        from ANALYTICS.stg_box_scores
        where team_id is not null order by team_id
    """)
    assert rows == [(1, 101, None, "1B"), (2, 202, None, "BE")]


def test_no_opponent_pair_is_fabricated(built):
    assert built("select count(*) from ANALYTICS.stg_matchup_pairs") == [(0,)]


# ---------------------------------------------------------------------------
# Team identity through the type-5 path (MLB-243)
# ---------------------------------------------------------------------------

def test_the_served_team_name_and_abbrev_survive_the_type_5_path(built):
    """THE DATA-LOSS BUG. The type-5 mRoster document carries no team
    labels, so the extract wrote "Team 1" / "1" and the workbook published
    numbered teams. ESPN served the real name on mTeam the whole time."""
    assert built("""
        select team_name, team_abbrev
        from ANALYTICS.stg_box_scores__team_rosters
        where team_id = 1
    """) == [("Served Sluggers", "SVD")]


def test_the_placeholder_never_reaches_staging_when_identity_is_served(built):
    rows = built("""
        select team_name, team_abbrev
        from ANALYTICS.stg_box_scores__team_rosters
    """)
    flat = {value for row in rows for value in row}
    assert "Team 1" not in flat and "1" not in flat, (
        f"the numeric placeholder survived into staging: {rows}"
    )


def test_the_roster_payload_label_is_kept_when_identity_is_silent(built):
    """A season captured before mTeam was must not be blanked. The identity
    feed WINS where it speaks; it does not overwrite with NULL where it
    does not."""
    assert built("""
        select team_name, team_abbrev
        from ANALYTICS.stg_box_scores__team_rosters
        where team_id = 2
    """) == [("Example Two", "TWO")]


def test_identity_is_keyed_on_team_id_not_the_display_string(built):
    """Two teams may legitimately share an abbreviation, and a rename does
    not make a new franchise. The join is on the id."""
    sql = (PROJECT_DIR / "models" / "staging" /
           "stg_box_scores__team_rosters.sql").read_text(encoding="utf-8")
    assert "f.team_id     = i.team_id" in sql
    assert "team_name = i.team_name" not in sql


def test_the_measured_format_field_reaches_staging(built):
    assert built("""
        select current_league_type, created_as_league_type
        from ANALYTICS.stg_matchup_schedule
    """) == [(5, 5)]


def test_large_json_expansions_are_materialized_before_the_final_union():
    """Pin the boundary the public 142-day rehearsal proved load-bearing."""
    staging = PROJECT_DIR / "models" / "staging"
    final_sql = (staging / "stg_box_scores.sql").read_text(encoding="utf-8")
    for model in ("stg_box_scores__season_points_players",
                  "stg_box_scores__free_agents"):
        boundary = (staging / f"{model}.sql").read_text(encoding="utf-8")
        assert "config(materialized='table')" in boundary
        assert f"ref('{model}')" in final_sql
    assert "flatten_array('lineup', 'p')" not in final_sql
    assert "flatten_array('free_agents_json', 'f')" not in final_sql


def test_free_agents_use_the_streaming_array_shape():
    """Forbid the lateral parent-retention shape that OOMed real type-5 RAW."""
    sql = (PROJECT_DIR / "models" / "staging" /
           "stg_box_scores__free_agents.sql").read_text(encoding="utf-8")
    assert "streamed_array_value('free_agents_json', 'f')" in sql
    assert "streamed_array_join('free_agents_json', 'f')" in sql
    assert "flatten_array('free_agents_json', 'f')" not in sql
