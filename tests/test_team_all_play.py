"""The MLB-301 all-play mart, computed for real over synthetic leagues.

WHY THIS RUNS THE MODEL'S SQL RATHER THAN A PYTHON TWIN. The all-play rule
(each team's PLATFORM score against every other team's in a completed
regular-season period; strict wins, strict losses and ties counted apart;
opponent count carried per matchup) has to hold in the SQL the warehouse
builds, not in a re-statement of it. So this reads mart_team_all_play.sql,
resolves its refs onto fixture tables, and runs it in an in-memory DuckDB.
No warehouse, no dbt process, no private data: the suite stays a pure unit
suite and the assertions still land on the shipped SQL.

THE FIXTURE LEAGUE IS A LIST OF RULINGS. A historical season (2025) with
FOUR teams and a current one (2026) with SIX, so a changing league size is
summed per matchup rather than by one divisor; an exact tie in 2026 P1; a
playoff period with scores (must not count); an OPEN period carrying running
totals (must not count); an uncaptured-but-proven-complete season (counts);
and a second league sharing team ids (must not leak).
"""

import re
from pathlib import Path

import duckdb
import pytest

REPO = Path(__file__).resolve().parents[1]
MODEL = REPO / 'dbt_league' / 'models' / 'marts' / 'reporting' / 'mart_team_all_play.sql'


def _render_model_sql():
    sql = MODEL.read_text(encoding='utf-8')
    sql = re.sub(r"\{\{\s*config\([^}]*\)\s*\}\}", '', sql)
    sql = re.sub(r"\{\{\s*ref\('([a-z_]+)'\)\s*\}\}", r'\1', sql)
    assert '{{' not in sql, 'model uses a jinja construct this harness does not resolve'
    return sql


def _matchup(league, season, period, team, opp, pts, opp_pts, playoff=False):
    result = 'W' if pts > opp_pts else 'L' if pts < opp_pts else 'T'
    return (league, season, period, team, f'Team {team}', f'T{team}', opp,
            float(pts), float(opp_pts), result, playoff)


@pytest.fixture(scope='module')
def con():
    c = duckdb.connect(':memory:')
    c.execute("""create table mart_team_matchup (
        league_key varchar, season_year integer, matchup_period integer,
        team_id integer, team_name varchar, team_abbrev varchar,
        opponent_id integer, platform_points double, opponent_points double,
        result varchar, is_playoff boolean)""")
    c.execute("""create table int_matchup_period_evidence (
        league_key varchar, season_year integer, matchup_period integer, is_closed boolean)""")
    c.execute("""create table int_league_season_closure (
        league_key varchar, season_year integer, has_schedule_capture boolean,
        is_season_complete boolean)""")

    rows = []
    # 2025, league A: FOUR teams, two regular periods, captured and closed,
    # then a playoff period that must not count.
    rows += [_matchup('A', 2025, 1, 1, 2, 100, 90), _matchup('A', 2025, 1, 2, 1, 90, 100),
             _matchup('A', 2025, 1, 3, 4, 80, 70), _matchup('A', 2025, 1, 4, 3, 70, 80)]
    rows += [_matchup('A', 2025, 2, 1, 3, 50, 60), _matchup('A', 2025, 2, 3, 1, 60, 50),
             _matchup('A', 2025, 2, 2, 4, 55, 65), _matchup('A', 2025, 2, 4, 2, 65, 55)]
    rows += [_matchup('A', 2025, 3, 1, 3, 999, 1, playoff=True),
             _matchup('A', 2025, 3, 3, 1, 1, 999, playoff=True)]
    # 2026, league A: SIX teams. P1 closed with an exact tie between teams
    # 1 and 2 (both 100). P2 is OPEN (running totals) and must not count.
    rows += [_matchup('A', 2026, 1, 1, 2, 100, 100), _matchup('A', 2026, 1, 2, 1, 100, 100),
             _matchup('A', 2026, 1, 3, 4, 90, 80), _matchup('A', 2026, 1, 4, 3, 80, 90),
             _matchup('A', 2026, 1, 5, 6, 70, 60), _matchup('A', 2026, 1, 6, 5, 60, 70)]
    rows += [_matchup('A', 2026, 2, 1, 3, 10, 20), _matchup('A', 2026, 2, 3, 1, 20, 10),
             _matchup('A', 2026, 2, 2, 4, 30, 40), _matchup('A', 2026, 2, 4, 2, 40, 30),
             _matchup('A', 2026, 2, 5, 6, 50, 60), _matchup('A', 2026, 2, 6, 5, 60, 50)]
    # 2024, league A: NO schedule capture, but the season is independently
    # proven complete, so its one period counts.
    rows += [_matchup('A', 2024, 1, 1, 2, 10, 5), _matchup('A', 2024, 1, 2, 1, 5, 10),
             _matchup('A', 2024, 1, 3, 4, 7, 8), _matchup('A', 2024, 1, 4, 3, 8, 7)]
    # 2023, league A: no capture and NOT proven complete -> nothing counts.
    rows += [_matchup('A', 2023, 1, 1, 2, 10, 5), _matchup('A', 2023, 1, 2, 1, 5, 10)]
    # League B shares team ids and a season; it must never be compared
    # against league A's scores.
    rows += [_matchup('B', 2026, 1, 1, 2, 500, 1), _matchup('B', 2026, 1, 2, 1, 1, 500)]
    c.executemany('insert into mart_team_matchup values (?,?,?,?,?,?,?,?,?,?,?)', rows)

    c.executemany('insert into int_matchup_period_evidence values (?,?,?,?)', [
        ('A', 2025, 1, True), ('A', 2025, 2, True), ('A', 2025, 3, True),
        ('A', 2026, 1, True), ('A', 2026, 2, False),
        ('B', 2026, 1, True),
    ])
    c.executemany('insert into int_league_season_closure values (?,?,?,?)', [
        ('A', 2025, True, True), ('A', 2026, True, False),
        ('A', 2024, False, True), ('A', 2023, False, False),
        ('B', 2026, True, False),
    ])
    c.execute('create table mart_team_all_play as ' + _render_model_sql())
    return c


def _rows(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def test_grain_is_one_row_per_completed_regular_team_matchup(con):
    keys = _rows(con, """select league_key, season_year, matchup_period, count(*) n
                          from mart_team_all_play group by 1,2,3 order by 1,2,3""")
    assert keys == [
        {'league_key': 'A', 'season_year': 2024, 'matchup_period': 1, 'n': 4},
        {'league_key': 'A', 'season_year': 2025, 'matchup_period': 1, 'n': 4},
        {'league_key': 'A', 'season_year': 2025, 'matchup_period': 2, 'n': 4},
        {'league_key': 'A', 'season_year': 2026, 'matchup_period': 1, 'n': 6},
        {'league_key': 'B', 'season_year': 2026, 'matchup_period': 1, 'n': 2},
    ]


def test_playoff_and_open_periods_and_unproven_seasons_are_excluded(con):
    assert _rows(con, "select * from mart_team_all_play where season_year = 2025 and matchup_period = 3") == []
    assert _rows(con, "select * from mart_team_all_play where season_year = 2026 and matchup_period = 2") == []
    assert _rows(con, "select * from mart_team_all_play where season_year = 2023") == []


def test_historical_four_team_season(con):
    got = {r['team_id']: r for r in _rows(con, """select team_id, all_play_wins, all_play_losses,
        all_play_ties, opponent_count, expected_win_share
        from mart_team_all_play where league_key = 'A' and season_year = 2025 and matchup_period = 1""")}
    # Scores 100 / 90 / 80 / 70: team 1 beats all three, team 4 beats none.
    assert (got[1]['all_play_wins'], got[1]['all_play_losses'], got[1]['all_play_ties']) == (3, 0, 0)
    assert (got[2]['all_play_wins'], got[2]['all_play_losses']) == (2, 1)
    assert (got[3]['all_play_wins'], got[3]['all_play_losses']) == (1, 2)
    assert (got[4]['all_play_wins'], got[4]['all_play_losses']) == (0, 3)
    assert {r['opponent_count'] for r in got.values()} == {3}
    assert got[1]['expected_win_share'] == 1.0
    assert got[2]['expected_win_share'] == pytest.approx(2 / 3)


def test_tie_is_counted_separately_not_as_half_a_win(con):
    got = {r['team_id']: r for r in _rows(con, """select team_id, all_play_wins, all_play_losses,
        all_play_ties, opponent_count, expected_win_share, expected_tie_share
        from mart_team_all_play where league_key = 'A' and season_year = 2026 and matchup_period = 1""")}
    # Teams 1 and 2 both scored 100 against a field of 90 / 80 / 70 / 60.
    for tid in (1, 2):
        assert (got[tid]['all_play_wins'], got[tid]['all_play_losses'], got[tid]['all_play_ties']) == (4, 0, 1)
        assert got[tid]['opponent_count'] == 5
        assert got[tid]['expected_win_share'] == pytest.approx(4 / 5)
        assert got[tid]['expected_tie_share'] == pytest.approx(1 / 5)
    assert (got[3]['all_play_wins'], got[3]['all_play_losses'], got[3]['all_play_ties']) == (3, 2, 0)


def test_changing_league_size_sums_per_matchup(con):
    """Team 1 played a 4-team season (3 opponents per matchup) and a 6-team
    one (5 opponents). Expected wins add the per-matchup shares; one league-wide
    divisor would be wrong in both seasons."""
    got = _rows(con, """select season_year, matchup_period, opponent_count, all_play_wins, expected_win_share
                        from mart_team_all_play where league_key = 'A' and team_id = 1
                        order by season_year, matchup_period""")
    assert [(r['season_year'], r['opponent_count']) for r in got] == [(2024, 3), (2025, 3), (2025, 3), (2026, 5)]
    expected_wins = sum(r['expected_win_share'] for r in got)
    # 2024 P1: 10 beats 5/7/8 -> 3/3; 2025 P1: 3/3; 2025 P2: 50 beats none of
    # 60/55/65 -> 0/3; 2026 P1: 4/5.
    assert expected_wins == pytest.approx(1 + 1 + 0 + 0.8)
    assert sum(r['all_play_wins'] for r in got) == 3 + 3 + 0 + 4


def test_wins_and_losses_are_reciprocal_and_nobody_plays_themselves(con):
    periods = _rows(con, """select league_key, season_year, matchup_period, count(*) teams,
        sum(all_play_wins) w, sum(all_play_losses) l, sum(all_play_ties) t,
        min(opponent_count) mn, max(opponent_count) mx
        from mart_team_all_play group by 1,2,3""")
    for p in periods:
        assert p['w'] == p['l'], p
        assert p['t'] % 2 == 0, p
        assert p['mn'] == p['mx'] == p['teams'] - 1, p


def test_leagues_do_not_leak(con):
    got = _rows(con, "select team_id, all_play_wins, all_play_losses, opponent_count from mart_team_all_play where league_key = 'B' order by team_id")
    assert got == [{'team_id': 1, 'all_play_wins': 1, 'all_play_losses': 0, 'opponent_count': 1},
                   {'team_id': 2, 'all_play_wins': 0, 'all_play_losses': 1, 'opponent_count': 1}]


def test_counts_sum_to_opponent_count(con):
    bad = _rows(con, "select * from mart_team_all_play where all_play_wins + all_play_losses + all_play_ties <> opponent_count")
    assert bad == []
