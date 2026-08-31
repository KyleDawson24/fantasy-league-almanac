"""The Team-of-the-Month board prices SP and RP as pitchers (MLB-249 X-1).

THE DEFECT. `espn_points_data.window_lineup` built its candidate pool with

    CASE WHEN slot = 'P' THEN total_pitching_stat_pts
                         ELSE total_hitting_stat_pts END

so a pitcher reaching the board through his SP or RP eligibility -- which
is how ESPN spells nearly every pitcher -- fell into the ELSE and was
priced on `total_hitting_stat_pts`. That column is 0 for a pitcher, and
the pool's `HAVING SUM(...) > 0` then dropped the row outright. The board
did not merely misprice pitchers; it had none in the two slots pitchers
actually occupy, while the SP/RP capacities sat unfilled.

It survived because the one league shape that exercises it is the one
nobody rendered: a league using only the generic `P` slot is unaffected,
and `fct_player_position_pts` -- the season/matchup-grain path -- has
always spelled the three-slot CASE correctly. The daily-window path was
the single copy that never got it.

Pure: no warehouse, no network. The pool query and the capacities are both
stubbed, which is what lets the SP/RP shape be stated exactly rather than
depending on whichever league happens to be configured.
"""
from __future__ import annotations

import pytest

import espn_points_data


CAPS = {'C': 1, 'SP': 2, 'RP': 1}


def _candidates():
    """A pool as the fixed query would return it: pitchers carrying real
    points at SP and RP, ordered by position then points DESC as the SQL's
    ORDER BY guarantees.
    """
    return [
        {'player_key': 'k-catch', 'player_id': 1, 'player_name': 'Buster Bench',
         'display_name': 'Buster Bench', 'pro_team': 'SF',
         'position': 'C', 'position_pts': 300.0},
        {'player_key': 'k-rp', 'player_id': 2, 'player_name': 'Reliever Ray',
         'display_name': 'Reliever Ray', 'pro_team': 'NYY',
         'position': 'RP', 'position_pts': 150.0},
        {'player_key': 'k-sp1', 'player_id': 3, 'player_name': 'Ace Alpha',
         'display_name': 'Ace Alpha', 'pro_team': 'LAD',
         'position': 'SP', 'position_pts': 500.0},
        {'player_key': 'k-sp2', 'player_id': 4, 'player_name': 'Ace Beta',
         'display_name': 'Ace Beta', 'pro_team': 'ATL',
         'position': 'SP', 'position_pts': 450.0},
    ]


@pytest.fixture
def stub_window(monkeypatch):
    """Route window_lineup's three reads without a warehouse.

    Returns the list the SQL texts land in, so a test can assert on the
    query the module actually emitted.
    """
    seen_sql = []

    def fake_query(sql, params=None):
        seen_sql.append(sql)
        if 'MAX(season_year)' in sql:
            return [{'sy': 2026}]
        if 'FROM exploded' in sql:
            return _candidates()
        # _enrich_window_lineup's stat-tail / attribution read.
        return []

    monkeypatch.setattr(espn_points_data, 'query_for_presentation', fake_query)
    monkeypatch.setattr(espn_points_data, 'league_predicate',
                        lambda alias=None: "league_key = 'x'")

    import almanac_data
    monkeypatch.setattr(almanac_data, 'get_slot_capacities',
                        lambda year, matchup_period=None: dict(CAPS))
    return seen_sql


def test_the_pool_prices_sp_and_rp_off_the_pitching_column(stub_window):
    """The regression itself, asserted on the emitted SQL.

    Checked here rather than only through results because the CASE lives
    in the query text: a stubbed pool cannot execute it, and a test that
    only inspected the returned rows would pass against the broken
    spelling forever.
    """
    espn_points_data.window_lineup(1, 30)

    pool_sql = next(s for s in stub_window if 'FROM exploded' in s)

    assert "IN ('SP', 'RP', 'P')" in pool_sql, (
        "the pool must price all three pitching slots off the pitching "
        f"column; emitted:\n{pool_sql}"
    )
    # The exact broken spelling, so a revert cannot pass quietly.
    assert "WHEN {} = 'P'".format(espn_points_data.json_text('slot.value')) \
        not in pool_sql, "the single-slot 'P' test is back"


def test_pitchers_fill_the_sp_and_rp_capacities(stub_window):
    """End to end: the board seats pitchers in the slots they occupy."""
    board = espn_points_data.window_lineup(1, 30)

    by_slot = {}
    for row in board:
        by_slot.setdefault(row['lineup_slot'], []).append(row)

    assert 'SP' in by_slot, f"no SP seat filled; slots were {sorted(by_slot)}"
    assert 'RP' in by_slot, f"no RP seat filled; slots were {sorted(by_slot)}"

    # Both SP instances are seated, best first.
    assert len(by_slot['SP']) == CAPS['SP']
    assert [r['player_name'] for r in by_slot['SP']] == ['Ace Alpha', 'Ace Beta']
    assert [r['player_name'] for r in by_slot['RP']] == ['Reliever Ray']

    # And the pitching points ride through rather than being zeroed.
    assert by_slot['SP'][0]['position_pts'] == pytest.approx(500.0)
    assert by_slot['RP'][0]['position_pts'] == pytest.approx(150.0)


def test_bench_and_il_stay_out_of_the_pool(stub_window):
    """The non-position guard is unrelated to the X-1 fix and must survive
    it -- the two clauses sit in the same SELECT."""
    espn_points_data.window_lineup(1, 30)
    pool_sql = next(s for s in stub_window if 'FROM exploded' in s)
    assert "'BE'" in pool_sql and "'IL'" in pool_sql
