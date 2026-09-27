"""The neutral dashboard writer (MLB-319) on a tiny synthetic warehouse.

Pins the rules the writer encodes rather than the numbers of any real
league: a points league's periods are bins (period 1 starts on opening day,
then every 7 days) with no matchup key; a head-to-head league's period is
its dated matchup; the day key stays a real date; uniqueness and the
contact-data guard stop the run instead of writing.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
import publish_extract as pe  # noqa: E402

STATS = ["h", "ab", "b_bb", "b_so", "hbp", "sf", "hr", "r", "rbi", "sb", "cs", "tb", "singles",
         "doubles", "triples", "xbh", "gdp", "b_ibb", "cyc", "w", "l", "k", "er", "outs", "qs",
         "sv", "hld", "p_h", "p_bb", "p_hr", "p_r", "cg", "blk", "wp", "hbp_p", "blsv", "nh",
         "pg", "pk", "sho"]


def _fact_row(league, season, day, team, player, slot, category, weight, pts=1.0,
              matchup=None, team_name="Team"):
    row = {"league_key": league, "season_year": season, "matchup_period": matchup,
           "scoring_period": int(day.strftime("%Y%m%d")) if matchup is None
           else (day - date(season, 3, 25)).days + 1,
           "team_id": team, "team_name": team_name, "team_abbrev": "T", "owner_name": None,
           "player_id": int(player), "player_key": player, "player_name": "P",
           "display_name": "Player " + player, "position": "SS", "pro_team": "NYY",
           "mlb_team_id": 147, "eligible_slots": '["SS","BE"]', "lineup_slot": slot,
           "lineup_slot_category": category, "games_played": 1, "game_date": day,
           "is_active_slot": slot not in ("BE", "RS", "IL", "FA"),
           "performance_status": "active", "wasted_bucket": None, "active_weight": weight,
           "provenance": "captured", "platform_points": None, "platform_hitting_pts": None,
           "platform_pitching_pts": None, "negative_points": 0.0,
           "total_hitting_stat_pts": pts, "total_pitching_stat_pts": 0.0,
           "total_stat_pts": pts, "owner_display": None}
    row.update({s: 0.0 for s in STATS})
    return row


def _build(path, rows, fmt, matchups=(), extra_sql=()):
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA ANALYTICS")
    cols = list(rows[0])
    con.execute("CREATE TABLE ANALYTICS.fct_player_daily_performance AS SELECT * FROM (VALUES "
                + ", ".join("(" + ", ".join("?" for _ in cols) + ")" for _ in rows)
                + ") t(" + ", ".join(cols) + ")", [r[c] for r in rows for c in cols])
    con.execute("""CREATE TABLE ANALYTICS.dim_lineup_slot AS SELECT * FROM (VALUES
        ('espn','SS','shortstop','hitting',TRUE,TRUE,'SS',50),
        ('espn','DH','designated_hitter','hitting',TRUE,TRUE,'DH',110),
        ('espn','UTIL','utility','hitting',TRUE,FALSE,'UTIL',120),
        ('espn','BE','bench','inactive',FALSE,FALSE,'BE',900),
        ('cbs','SS','shortstop','hitting',TRUE,TRUE,'SS',50),
        ('cbs','DH','designated_hitter','hitting',TRUE,TRUE,'DH',110),
        ('cbs','U','utility','hitting',TRUE,FALSE,'U',120),
        ('cbs','RS','bench','inactive',FALSE,FALSE,'RS',900)
        ) t(platform, lineup_slot, canonical_slot_key, slot_category, is_starting_slot,
            is_position_slot, display_slot, sort_order)""")
    con.execute(f"""CREATE TABLE ANALYTICS.dim_league_format AS
        SELECT DISTINCT league_key, '{fmt}' AS league_format
        FROM ANALYTICS.fct_player_daily_performance""")
    con.execute("""CREATE TABLE ANALYTICS.dim_league_scoring_rule AS
        SELECT DISTINCT league_key, 'HR' stat_name, 'hr' canonical_key, 'hitting' stat_category,
               2026 settings_season, 'HR' platform_stat_key, 4.0 points_per_unit, TRUE is_scored
        FROM ANALYTICS.fct_player_daily_performance""")
    con.execute("""CREATE TABLE ANALYTICS.dim_matchup_period (league_key VARCHAR, season_year INT,
        matchup_period INT, start_date DATE, end_date DATE, is_playoff BOOLEAN)""")
    for m in matchups:
        con.execute("INSERT INTO ANALYTICS.dim_matchup_period VALUES (?,?,?,?,?,?)", list(m))
    con.execute("""CREATE TABLE ANALYTICS.mart_period_standings (league_key VARCHAR,
        season_year INT, period INT, team_id VARCHAR, period_points DOUBLE)""")
    con.execute("""CREATE TABLE ANALYTICS.dim_franchise_season (league_key VARCHAR,
        franchise_id VARCHAR, season_year INT, canonical_franchise_id VARCHAR,
        canonical_name VARCHAR, canonical_abbrev VARCHAR, configured_name VARCHAR,
        configured_abbrev VARCHAR)""")
    con.execute("""CREATE TABLE ANALYTICS.dim_team_season_standing (league_key VARCHAR,
        season_year INT, team_id INT, team_name VARCHAR, team_abbrev VARCHAR,
        standings_rank INT, playoff_seed INT, final_rank INT, is_champion BOOLEAN,
        made_playoffs BOOLEAN, wins INT, losses INT, ties INT, platform_points DOUBLE,
        total_points DOUBLE, batting_points DOUBLE, pitching_points DOUBLE, lens VARCHAR,
        teams_in_season INT)""")
    con.execute("""CREATE TABLE ANALYTICS.dim_roster_slot_counts (league_key VARCHAR,
        season_year INT, lineup_slot VARCHAR, starter_count INT)""")
    for sql in extra_sql:
        con.execute(sql)
    con.close()


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out"


def _run(db, out, league):
    root = pe.main(["--db", str(db), "--out", str(out), "--league", league])
    slug = league.replace("-", "_")
    return root / slug, json.loads((root / slug / "manifest.json").read_text())


def test_points_league_periods_are_bins_from_opening_day(tmp_path, out):
    # A Wednesday opener: period 1 runs to the first Sunday (5 days), then
    # Monday-Sunday weeks.
    opener = date(2026, 3, 25)
    rows = [_fact_row("cbs-bsb", 2026, opener + timedelta(days=d), 1, "11", "SS", "hitting", 1.0)
            for d in range(10)]
    db = tmp_path / "ESPN_FANTASY.duckdb"
    _build(db, rows, "points")
    league_dir, manifest = _run(db, out, "cbs-bsb")
    con = duckdb.connect()
    atom = con.execute(f"""SELECT scoring_day, period_key, week_bin_key, matchup_period_ordinal,
        platform, lineup_slot, platform_lineup_slot, eligible_slots
        FROM read_parquet('{(league_dir / 'atom' / '2026.parquet').as_posix()}')
        ORDER BY scoring_day""").fetchall()
    assert [r[1] for r in atom] == [1] * 5 + [2] * 5
    assert atom[0][2] == "2026-P01" and atom[5][2] == "2026-P02"
    assert atom[5][0].weekday() == 0  # period 2 opens on a Monday
    assert {r[3] for r in atom} == {None}  # one matchup, the season: no matchup key
    assert atom[0][4] == "cbs" and atom[0][5] == "shortstop" and atom[0][6] == "SS"
    # Bench stripped, canonical keys, and BSB's open slots (DH and U, a
    # league rule in config/leagues.yml) added for a hitter even though the
    # platform never prints them on the card.
    assert atom[0][7] == ["shortstop", "designated_hitter", "utility"]
    periods = con.execute(f"""SELECT period_key, start_date, end_date, period_label
        FROM read_parquet('{(league_dir / 'dims' / 'periods.parquet').as_posix()}')""").fetchall()
    assert periods[0] == (1, opener, date(2026, 3, 29), "Period 1")
    assert periods[1][1:3] == (date(2026, 3, 30), date(2026, 4, 5))
    bins = manifest["validations"]["period_bins"]["seasons"]
    assert bins[0]["derived_periods"] == 2 and bins[0]["status"] == "no_reference"
    assert manifest["validations"]["daily_key_is_yyyymmdd"]["rows_disagreeing"] == 0


def test_h2h_league_periods_are_its_matchups(tmp_path, out):
    days = [date(2026, 3, 25) + timedelta(days=d) for d in range(10)]
    rows = [_fact_row("espn-main", 2026, d, 1, "21", "SS", "hitting", 1.0,
                      matchup=1 if d < date(2026, 3, 30) else 2) for d in days]
    db = tmp_path / "ESPN_FANTASY.duckdb"
    _build(db, rows, "h2h", matchups=[
        ("espn-main", 2026, 1, date(2026, 3, 25), date(2026, 3, 29), False),
        ("espn-main", 2026, 2, date(2026, 3, 30), date(2026, 4, 5), False)])
    league_dir, manifest = _run(db, out, "espn-main")
    con = duckdb.connect()
    got = con.execute(f"""SELECT DISTINCT period_key, matchup_period_ordinal, is_complete, day_state
        FROM read_parquet('{(league_dir / 'atom' / '2026.parquet').as_posix()}') ORDER BY 1""").fetchall()
    # Matchup 1 closed before the data horizon; matchup 2 is still running.
    assert got == [(1, 1, True, "complete"), (2, 2, False, "in_flight")]
    assert manifest["validations"]["period_key_matches_fact_matchup_period"]["rows_disagreeing"] == 0
    assert manifest["validations"]["scoring_day_ordinal_matches_platform_ordinal"]["rows_disagreeing"] == 0


def test_a_duplicate_player_day_stops_the_run(tmp_path, out):
    d = date(2026, 4, 1)
    rows = [_fact_row("cbs-bsb", 2026, d, 1, "11", "SS", "hitting", 1.0),
            _fact_row("cbs-bsb", 2026, d, 2, "11", "SS", "hitting", 1.0)]
    db = tmp_path / "ESPN_FANTASY.duckdb"
    _build(db, rows, "points")
    with pytest.raises(pe.ExtractError, match="duplicate"):
        pe.main(["--db", str(db), "--out", str(out), "--league", "cbs-bsb"])


def test_contact_data_in_a_team_label_stops_the_run(tmp_path, out):
    rows = [_fact_row("cbs-bsb", 2026, date(2026, 4, 1), 1, "11", "SS", "hitting", 1.0,
                      team_name="call 555-867-5309")]
    db = tmp_path / "ESPN_FANTASY.duckdb"
    _build(db, rows, "points")
    with pytest.raises(pe.ExtractError, match="email or phone"):
        pe.main(["--db", str(db), "--out", str(out), "--league", "cbs-bsb"])


def test_team_names_that_look_odd_are_carried_verbatim(tmp_path, out):
    rows = [_fact_row("cbs-bsb", 2026, date(2026, 4, 1), 1, "11", "SS", "hitting", 1.0,
                      team_name="14-30-8-24-5-15-13-20"),
            _fact_row("cbs-bsb", 2026, date(2026, 4, 1), 9999, "12", "SS", "hitting", 1.0,
                      team_name="####")]
    db = tmp_path / "ESPN_FANTASY.duckdb"
    _build(db, rows, "points")
    league_dir, _ = _run(db, out, "cbs-bsb")
    names = duckdb.connect().execute(f"""SELECT team_id, team_name, name_source
        FROM read_parquet('{(league_dir / 'dims' / 'teams.parquet').as_posix()}') ORDER BY 1""").fetchall()
    assert names == [(1, "14-30-8-24-5-15-13-20", "fct_player_daily_performance"),
                     (9999, "####", "fct_player_daily_performance")]


def test_no_game_roster_days_become_dense_rows_with_null_production(tmp_path, out):
    game_day, off_day = date(2026, 4, 1), date(2026, 4, 2)
    # A later game day keeps the off day inside the data horizon (days past
    # the last completed game are never emitted).
    rows = [_fact_row("cbs-bsb", 2026, game_day, 1, "11", "SS", "hitting", 1.0, pts=3.0),
            _fact_row("cbs-bsb", 2026, date(2026, 4, 3), 1, "11", "SS", "hitting", 1.0)]
    db = tmp_path / "ESPN_FANTASY.duckdb"
    _build(db, rows, "points", extra_sql=[
        """CREATE TABLE ANALYTICS.fct_player_roster_day AS SELECT * FROM (VALUES
            ('cbs-bsb', 2026, DATE '2026-04-01', '11', 'Player 11', 'NYY', 1, 'SS', '["SS"]', 'rostered_game_day', 'observed_game_day'),
            ('cbs-bsb', 2026, DATE '2026-04-02', '11', NULL, NULL, 1, NULL, '["SS"]', 'rostered', 'inferred_gap')
        ) t(league_key, season_year, roster_date, player_key, display_name, pro_team, team_id, lineup_slot,
            eligible_slots, roster_status, roster_source)"""])
    league_dir, manifest = _run(db, out, "cbs-bsb")
    got = duckdb.connect().execute(f"""SELECT scoring_day, has_game, total_pts, ab, deployment_state,
        provenance, display_name, eligible_slots, period_key
        FROM read_parquet('{(league_dir / 'atom' / '2026.parquet').as_posix()}') ORDER BY 1""").fetchall()
    assert got[0][:3] == (game_day, True, 3.0)
    # The off day exists, carries no production (NULL, never zero), and keeps
    # its label, eligibility and period.
    assert got[1] == (off_day, False, None, None, "unobserved", "inferred_gap", "Player 11",
                      ["shortstop", "designated_hitter", "utility"], 1)
    assert manifest["validations"]["dense_no_game_rows"] == 1


def test_a_league_without_overrides_keeps_dh_eligibility_only(tmp_path, out):
    # Buns: DH needs DH eligibility; only the non-position UTIL is open.
    days = [date(2026, 3, 25) + timedelta(days=d) for d in range(3)]
    rows = [_fact_row("espn-main", 2026, d, 1, "21", "SS", "hitting", 1.0, matchup=1) for d in days]
    db = tmp_path / "ESPN_FANTASY.duckdb"
    _build(db, rows, "h2h", matchups=[("espn-main", 2026, 1, date(2026, 3, 25), date(2026, 3, 31), False)])
    league_dir, manifest = _run(db, out, "espn-main")
    got = duckdb.connect().execute(f"""SELECT DISTINCT eligible_slots
        FROM read_parquet('{(league_dir / 'atom' / '2026.parquet').as_posix()}')""").fetchall()
    assert got == [(["shortstop", "utility"],)]
    assert manifest["validations"]["open_hitting_slots"]["slots"] == ["utility"]
