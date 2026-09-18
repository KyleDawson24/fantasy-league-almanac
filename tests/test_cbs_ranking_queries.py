"""CBS ranking queries preserve their aggregate semantics on the local lane."""
from datetime import date

import pytest

import cbs_almanac_sheets as cbs
import db


@pytest.fixture
def daily_fact(monkeypatch):
    duckdb = pytest.importorskip('duckdb')
    con = duckdb.connect()
    con.execute('''CREATE TABLE daily (
        league_key VARCHAR, season_year INTEGER, player_key VARCHAR,
        team_id INTEGER, team_abbrev VARCHAR, team_name VARCHAR,
        game_date DATE, total_stat_pts DOUBLE, active_weight DOUBLE,
        provenance VARCHAR)''')
    monkeypatch.setattr(db, '_dialect', 'duckdb')
    monkeypatch.setattr(cbs, 'league_predicate', lambda: "league_key = 'cbs-test'")
    con.execute('''CREATE VIEW fct_player_daily_performance AS
        SELECT * FROM daily WHERE league_key='cbs-test'
        UNION ALL SELECT * FROM daily WHERE league_key='other' ''')

    def query(sql):
        cur = con.execute(sql)
        names = [col[0].lower() for col in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    monkeypatch.setattr(cbs, 'query_for_presentation', query)
    yield con
    con.close()


def test_franchise_ranking_uses_weighted_positive_totals(daily_fact):
    rows = [
        ('cbs-test', 2025, "player'1", 1, 'OLD', 'First', date(2025, 4, 1), 20, 1, 'captured'),
        ('cbs-test', 2026, "player'1", 1, 'NEW', 'First', date(2026, 4, 1), 20, 1, 'captured'),
        ('cbs-test', 2026, "player'1", 2, 'SECOND', 'Second', date(2026, 4, 1), 100, .3, 'captured'),
        ('cbs-test', 2026, "player'1", 3, None, 'Third', date(2026, 4, 1), 20, 1, 'captured'),
        ('cbs-test', 2026, "player'1", 4, 'FOURTH', 'Fourth', date(2026, 4, 1), 10, 1, 'captured'),
        ('cbs-test', 2026, "player'1", 5, 'INACTIVE', 'Inactive', date(2026, 4, 1), 999, None, 'captured'),
        ('other', 2026, "player'1", 6, 'OTHER', 'Other', date(2026, 4, 1), 999, 1, 'captured'),
    ]
    daily_fact.executemany('INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
    lineup = [{'player_key': "player'1", 'player_name': 'Test Player'}]
    actual = cbs._apply_alltime_board_context(lineup, {}, {}, {"player'1": [2025, 2026]})
    assert actual[0]['team_abbrev'] == 'NEW, SECOND, Third'
    assert actual[0]['_alltime_retired'] is True
    current = {"player'1": {'abbrev': 'CURRENT', 'owner': 'Test Owner'}}
    actual = cbs._apply_alltime_board_context(lineup, current, {}, {})
    assert actual[0]['team_abbrev'] == 'CURRENT'
    assert actual[0]['owner_name'] == 'Test Owner'
    assert actual[0]['_alltime_retired'] is False


@pytest.mark.parametrize('dialect', ['duckdb', 'snowflake'])
def test_stat_sources_uses_dominant_tier_per_season(daily_fact, monkeypatch, dialect):
    monkeypatch.setattr(db, '_dialect', dialect)
    rows = []
    for year, provenance, count in [(2024, 'estimated_startshare', 3),
                                     (2024, 'reconstructed_day', 1),
                                     (2025, 'reconstructed_day', 4),
                                     (2026, 'captured', 2)]:
        rows.extend([('cbs-test', year, 'player', 1, 'TEST', 'Test',
                      date(year, 4, 1), 1, 1, provenance)] * count)
    daily_fact.executemany('INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
    tiers = cbs.get_stat_sources()
    assert [row['pct'] for row in tiers] == [20, 50, 30]
    assert tiers[1]['dates'] == '2025'
    assert tiers[2]['dates'] == '2024'
