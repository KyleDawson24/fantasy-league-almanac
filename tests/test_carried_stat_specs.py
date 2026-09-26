"""A league can score a stat no team-grain mart breaks out (MLB-319).

CBS scores IRSTR, which rides only inside total_pitching_stat_pts, so the
shared export's spec-driven standings SELECT asked Snowflake for a column
that does not exist ("invalid identifier 'IRSTR'"). These run the REAL SQL
on a throwaway DuckDB warehouse shaped like the two models involved, so the
fix is proven against a binder, not against a stub that accepts anything.
"""

import duckdb
import pytest

import almanac_data
import db


@pytest.fixture
def cbs_warehouse(tmp_path):
    path = tmp_path / 'ESPN_FANTASY.duckdb'
    con = duckdb.connect(str(path))
    con.execute('CREATE SCHEMA ANALYTICS')
    con.execute("""
        CREATE TABLE ANALYTICS.dim_stat AS SELECT * FROM (VALUES
            ('K', 'K', 'Strikeouts', 'K', 'pitching', TRUE),
            ('OUTS', 'OUTS', 'Outs', 'OUTS', 'pitching', TRUE),
            ('IRSTR', 'IRSTR', 'Inherited Runners Stranded', 'IRSTR', 'pitching', TRUE)
        ) t(stat_name, leaderboard_name, display_name, abbrev, stat_category, is_counting)
    """)
    con.execute("""
        CREATE TABLE ANALYTICS.dim_league_scoring_rule AS SELECT * FROM (VALUES
            ('cbs-bsb', 'K', TRUE, 1.0),
            ('cbs-bsb', 'OUTS', TRUE, 1.0),
            ('cbs-bsb', 'IRSTR', TRUE, 1.0)
        ) t(league_key, stat_name, is_scored, points_per_unit)
    """)
    # The standings mart carries k and outs but, like the real one, no irstr.
    con.execute("""
        CREATE TABLE ANALYTICS.mart_team_season_standings AS SELECT * FROM (VALUES
            ('cbs-bsb', 2025, 7, 'SEVN', 'Team 7', 'Owner', 0, 0, 0, 1, 180, 180,
             900.5, 1100.25, 2000.75, NULL::DOUBLE, 1200, 4000),
            ('cbs-bsb', 2026, 7, 'SEVN', 'Team 7', 'Owner', 0, 0, 0, 1, 180, 180,
             950.0, 1000.0, 1950.0, NULL::DOUBLE, 1100, 3900)
        ) t(league_key, season_year, team_id, team_abbrev, team_name, owner_display,
            wins, losses, ties, matchup_periods_played, scoring_days_played,
            standard_matchup_days, calculated_hitting_pts, calculated_pitching_pts,
            calculated_points, against_calculated_points, k, outs)
    """)
    con.close()
    before = (db.backend(), db.dialect(), db._duck_path, db._duck_conn, db._league)
    db._duck_conn = None
    db.use_duckdb(str(path))
    db.set_league('cbs-bsb')
    yield path
    if db._duck_conn is not None:
        db._duck_conn.close()
    db._BACKEND, db._duck_path, db._duck_conn, db._league = (
        before[0], before[2], before[3], before[4])
    db.set_dialect(before[1])


def test_irstr_is_scored_but_not_carried(cbs_warehouse):
    specs = almanac_data.get_team_week_stat_specs()
    assert 'IRSTR' in {s['stat_name'] for s in specs}
    carried = almanac_data.carried_stat_specs(specs, 'mart_team_season_standings')
    assert {s['stat_name'] for s in carried} == {'K', 'OUTS'}


def test_alltime_standings_run_for_a_league_scoring_irstr(cbs_warehouse):
    specs = almanac_data.get_team_week_stat_specs()
    rows = almanac_data.get_team_standings_alltime(specs)
    assert len(rows) == 1
    row = rows[0]
    assert row['k'] == 2300 and row['outs'] == 7900
    assert 'irstr' not in row
    # The stat's points are not lost: they live in the calculated totals.
    assert row['calculated_pitching_pts'] == pytest.approx(2100.25)


def test_the_relation_lookup_is_case_blind(cbs_warehouse):
    assert 'k' in almanac_data._relation_columns('MART_TEAM_SEASON_STANDINGS')
    assert almanac_data._relation_columns('no_such_relation') == set()
