"""Unit coverage for the MLB-103 Trades tab (pure layers only).

get_trade_block_data is a live ESPN league-API read; per the conftest
scope (warehouse-free, and network-free by the same logic) it is
exercised by real publishes, not here. These tests cover the pure
surface: eligibility display, row formatting, and tab-row layout.
"""

from almanac_logic import build_trades_tab_rows
from almanac_render import (
    TRADES_HEADER,
    format_trades_row,
    trade_eligibility_display,
)


def _player(**overrides):
    base = {
        'fantasy_team': 'Bo Bichette Bird Law',
        'player_name': 'Yandy Diaz',
        'pro_team': 'TB',
        'eligible_slots': ['1B', '1B/3B', 'DH', 'UTIL', 'BE', 'IL'],
        'availability': None,
        'interest': 0,
    }
    base.update(overrides)
    return base


def _data(players, as_of='2026-07-20 12:00'):
    return {'as_of': as_of, 'players': players}


def _data_rows(rows):
    """Rows after the header band."""
    header_idx = next(i for i, r in enumerate(rows) if r == list(TRADES_HEADER))
    return rows[header_idx + 1:]


# ---- trade_eligibility_display ---------------------------------------------

def test_eligibility_drops_combo_and_umbrella_slots():
    assert trade_eligibility_display(
        ['SS', '2B/SS', 'IF', 'UTIL', 'BE', 'IL']) == 'SS'
    assert trade_eligibility_display(
        ['1B', '1B/3B', 'DH', 'UTIL', 'BE', 'IL']) == '1B/DH'


def test_eligibility_outfield_collapses_to_specific_spots():
    assert trade_eligibility_display(['LF', 'CF', 'OF', 'UTIL']) == 'LF/CF'


def test_eligibility_orders_by_lineup_slot():
    assert trade_eligibility_display(['RP', 'SP', 'P']) == 'SP/RP'
    assert trade_eligibility_display(['DH', 'SP', 'P', 'UTIL']) == 'DH/SP'


def test_eligibility_falls_back_to_umbrella_then_dashes():
    assert trade_eligibility_display(['UTIL', 'BE']) == 'UTIL'
    assert trade_eligibility_display([]) == '--'
    assert trade_eligibility_display(None) == '--'


# ---- format_trades_row ------------------------------------------------------

def test_format_trades_row_labels_availability():
    row = format_trades_row(_player(availability='ON_THE_BLOCK', interest=2))
    assert row == ['Bo Bichette Bird Law', 'TB', '1B/DH', 'Yandy Diaz',
                   'On the Block', 2]


def test_format_trades_row_blank_label_for_interest_only():
    row = format_trades_row(_player(interest=1))
    assert row[4] == ''
    assert row[5] == 1


# ---- build_trades_tab_rows --------------------------------------------------

def test_qualifying_filter_is_availability_or_interest():
    rows = build_trades_tab_rows(_data([
        _player(player_name='Skipped Default', availability=None, interest=0),
        _player(player_name='Marked No Watchers',
                availability='UNTOUCHABLE', interest=0),
        _player(player_name='Watched Unmarked', availability=None, interest=1),
    ]), 2026)
    names = [r[3] for r in _data_rows(rows)]
    assert names == ['Marked No Watchers', 'Watched Unmarked']


def test_unknown_future_status_still_qualifies_with_blank_label():
    rows = build_trades_tab_rows(_data([
        _player(player_name='New Status Guy', availability='SHOPPING_HARD'),
    ]), 2026)
    data = _data_rows(rows)
    assert [r[3] for r in data] == ['New Status Guy']
    assert data[0][4] == ''


def test_rows_group_by_team_then_availability_then_name():
    rows = build_trades_tab_rows(_data([
        _player(fantasy_team='Zoo Crew', player_name='Aaron Ape',
                availability='ON_THE_BLOCK'),
        _player(fantasy_team='Aardvarks', player_name='Zed Zebra', interest=1),
        _player(fantasy_team='Aardvarks', player_name='Alice Ant', interest=1),
        _player(fantasy_team='Aardvarks', player_name='Uma Untouchable',
                availability='UNTOUCHABLE'),
        _player(fantasy_team='Aardvarks', player_name='Bob Blocked',
                availability='ON_THE_BLOCK'),
    ]), 2026)
    labeled = [(r[0], r[3]) for r in _data_rows(rows)]
    assert labeled == [
        ('Aardvarks', 'Bob Blocked'),
        ('Aardvarks', 'Uma Untouchable'),
        ('Aardvarks', 'Alice Ant'),
        ('Aardvarks', 'Zed Zebra'),
        ('Zoo Crew', 'Aaron Ape'),
    ]


def test_tab_scaffolding_title_asof_and_header():
    rows = build_trades_tab_rows(
        _data([_player(interest=1)], as_of='2026-07-20 09:30'), 2026,
    )
    assert rows[0] == ['Trades: 2026']
    assert 'As of 2026-07-20 09:30' in rows[2][0]
    assert list(TRADES_HEADER) in rows


def test_empty_market_renders_quiet_row():
    rows = build_trades_tab_rows(_data([_player()]), 2026)
    assert rows[-1][0].startswith('Nobody is on the block')
