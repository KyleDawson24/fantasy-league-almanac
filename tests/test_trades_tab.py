"""Unit coverage for the MLB-103 Trades tab (pure layers only).

get_trades_tab_data is a live ESPN league-API + warehouse read; per the
conftest scope (warehouse-free, and network-free by the same logic) it is
exercised by real publishes, not here. These tests cover the pure
surface: eligibility display, row formatting, tab-row layout for both
tables, and the write layer's structure parsers (bounds / merge groups).
"""

from almanac_logic import build_trades_tab_rows
from almanac_render import (
    TRADE_RECORD_HEADER,
    TRADES_HEADER,
    format_trade_record_row,
    format_trades_row,
    trade_eligibility_display,
)
from almanac_write import _trade_record_groups, _trades_section_bounds


def _player(**overrides):
    base = {
        'fantasy_team': 'Bo Bichette Bird Law',
        'player_name': 'Yandy Diaz',
        'pro_team': 'TB',
        'eligible_slots': ['1B', '1B/3B', 'DH', 'UTIL', 'BE', 'IL'],
        'availability': None,
        'interest': 0,
        'total_pts': 0,
        'active_pts': 0,
    }
    base.update(overrides)
    return base


def _leg(**overrides):
    base = {
        'receiving_team': 'Atomic Alpacas Assuming Position',
        'sending_abbrev': 'CYCL',
        'player_name': 'Tommy Edman',
        'pro_team': 'LAD',
        'eligible_slots': ['2B', 'SS', '2B/SS', 'UTIL', 'BE'],
        'total_pts': 50.0,
        'active_pts': 25.0,
    }
    base.update(overrides)
    return base


def _data(players=(), trades=(), as_of='2026-07-20 12:00'):
    return {'as_of': as_of, 'players': list(players), 'trades': list(trades)}


def _section_rows(rows, header):
    """Data rows under the given header row until the next blank row."""
    header_idx = next(i for i, r in enumerate(rows) if r == list(header))
    out = []
    for r in rows[header_idx + 1:]:
        if not r or r[0] in ('', None):
            break
        out.append(r)
    return out


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


# ---- row formatters ---------------------------------------------------------

def test_format_trades_row_links_name_and_carries_points():
    row = format_trades_row(_player(
        availability='ON_THE_BLOCK', interest=2, total_pts=262.5,
        active_pts=204.1,
    ))
    assert len(row) == len(TRADES_HEADER)
    assert row[0] == 'Bo Bichette Bird Law'
    assert row[3].startswith('=HYPERLINK(')
    assert 'Yandy Diaz' in row[3]
    assert row[4] == 'On the Block'
    assert row[5:] == [2, 262.5, 204.1]


def test_format_trades_row_blank_label_for_interest_only():
    row = format_trades_row(_player(interest=1))
    assert row[4] == ''
    assert row[5] == 1


def test_format_trade_record_row_sums_and_date_only_when_given():
    first = format_trade_record_row(
        _leg(), team_sums=(100.0, 45.0), date_display='7/20/2026',
    )
    assert len(first) == len(TRADE_RECORD_HEADER)
    assert first[0] == 'Atomic Alpacas Assuming Position'
    assert first[3].startswith('=HYPERLINK(')
    assert first[4] == 'CYCL'
    assert first[5] == ''
    assert first[6:] == [50.0, 25.0, 100.0, 45.0, '7/20/2026']

    continuation = format_trade_record_row(_leg())
    assert continuation[8:] == ['', '', '']


# ---- build_trades_tab_rows: Trading Block section ---------------------------

def test_qualifying_filter_is_availability_or_interest():
    rows = build_trades_tab_rows(_data(players=[
        _player(player_name='Skipped Default'),
        _player(player_name='Marked No Watchers', availability='UNTOUCHABLE'),
        _player(player_name='Watched Unmarked', interest=1),
    ]), 2026)
    names = [r[3] for r in _section_rows(rows, TRADES_HEADER)]
    assert 'Skipped Default' not in ' '.join(names)
    assert len(names) == 2


def test_block_sorts_by_class_then_interest_then_total_points():
    rows = build_trades_tab_rows(_data(players=[
        _player(player_name='Untouchable Star', availability='UNTOUCHABLE',
                interest=3, total_pts=400),
        _player(player_name='Watched Low', interest=1, total_pts=100),
        _player(player_name='Watched High', interest=2, total_pts=100),
        _player(player_name='Watched Big Season', interest=1, total_pts=300),
        _player(player_name='Blocked Quiet', availability='ON_THE_BLOCK',
                interest=0, total_pts=50),
        _player(player_name='Blocked Hot', availability='ON_THE_BLOCK',
                interest=2, total_pts=10),
    ]), 2026)
    names = [r[3].split('"')[-2] for r in _section_rows(rows, TRADES_HEADER)]
    assert names == [
        'Blocked Hot', 'Blocked Quiet',
        'Watched High', 'Watched Big Season', 'Watched Low',
        'Untouchable Star',
    ]


def test_unknown_future_status_sorts_with_unmarked_and_blank_label():
    rows = build_trades_tab_rows(_data(players=[
        _player(player_name='New Status Guy', availability='SHOPPING_HARD',
                interest=0, total_pts=10),
        _player(player_name='Blocked', availability='ON_THE_BLOCK'),
        _player(player_name='Held', availability='UNTOUCHABLE'),
    ]), 2026)
    data = _section_rows(rows, TRADES_HEADER)
    names = [r[3].split('"')[-2] for r in data]
    assert names == ['Blocked', 'New Status Guy', 'Held']
    assert data[1][4] == ''


def test_tab_scaffolding_title_asof_sections_and_headers():
    rows = build_trades_tab_rows(
        _data(players=[_player(interest=1)], as_of='2026-07-20 09:30'), 2026,
    )
    assert rows[0] == ['Trades: 2026']
    assert 'As of 2026-07-20 09:30' in rows[2][0]
    assert ['Trading Block'] in rows
    assert ['Trade Record'] in rows
    assert list(TRADES_HEADER) in rows
    assert list(TRADE_RECORD_HEADER) in rows


def test_empty_block_and_no_trades_render_notices():
    rows = build_trades_tab_rows(_data(), 2026)
    flat = [r[0] for r in rows if r]
    assert any(s.startswith('Nobody is on the block') for s in flat)
    assert any(s.startswith('No trades have been executed') for s in flat)


# ---- build_trades_tab_rows: Trade Record section ----------------------------

def _two_trades():
    """Newest first: a 2-for-1 between Andys/CYCL, then a 1-for-1."""
    return [
        {
            'date_display': '7/20/2026',
            'legs': [
                _leg(player_name='Tommy Edman', total_pts=50, active_pts=25),
                _leg(player_name='Roki Sasaki', total_pts=50, active_pts=20),
                _leg(receiving_team='Cycle Hitters', sending_abbrev='AAA',
                     player_name='Willson Contreras', total_pts=30,
                     active_pts=12),
            ],
        },
        {
            'date_display': '7/1/2026',
            'legs': [
                _leg(receiving_team='Two Beers Short of the Cycle',
                     sending_abbrev='AAA', player_name='Nico Hoerner',
                     total_pts=75, active_pts=40),
            ],
        },
    ]


def test_record_rows_group_sides_with_sums_and_single_date():
    rows = build_trades_tab_rows(_data(trades=_two_trades()), 2026)
    data = _section_rows(rows, TRADE_RECORD_HEADER)
    assert len(data) == 4

    # Trade 1, side 1 (Andys, alpha-first): sums + date on its first row.
    assert data[0][0] == 'Atomic Alpacas Assuming Position'
    assert data[0][8:] == [100, 45, '7/20/2026']
    # Continuation row: no sums, no date.
    assert data[1][0] == 'Atomic Alpacas Assuming Position'
    assert data[1][8:] == ['', '', '']
    # Trade 1, side 2: its own sums, but no date (merged from row 1).
    assert data[2][0] == 'Cycle Hitters'
    assert data[2][8:] == [30, 12, '']
    # Trade 2 starts a fresh date.
    assert data[3][0] == 'Two Beers Short of the Cycle'
    assert data[3][8:] == [75, 40, '7/1/2026']


def test_record_side_orders_legs_by_since_trade_points():
    rows = build_trades_tab_rows(_data(trades=[{
        'date_display': '7/20/2026',
        'legs': [
            _leg(player_name='Small Piece', total_pts=5, active_pts=1),
            _leg(player_name='Headliner', total_pts=80, active_pts=60),
        ],
    }]), 2026)
    data = _section_rows(rows, TRADE_RECORD_HEADER)
    assert 'Headliner' in data[0][3]
    assert 'Small Piece' in data[1][3]


# ---- write-layer structure parsers ------------------------------------------

def test_section_bounds_and_groups_parse_written_rows():
    rows = build_trades_tab_rows(
        _data(players=[_player(availability='ON_THE_BLOCK')],
              trades=_two_trades()),
        2026,
    )
    block_hdr, block_end, record_hdr, record_end = _trades_section_bounds(rows)
    assert rows[block_hdr] == list(TRADES_HEADER)
    assert block_end - block_hdr - 1 == 1          # one qualifying player
    assert rows[record_hdr] == list(TRADE_RECORD_HEADER)
    assert record_end - record_hdr - 1 == 4        # four leg rows

    groups = _trade_record_groups(rows, record_hdr, record_end)
    assert len(groups) == 2
    first, second = groups
    assert first['end'] - first['start'] == 3
    # Two sides: Andys spans two rows, Cycle Hitters one.
    assert [e - s for s, e in first['sides']] == [2, 1]
    assert second['end'] - second['start'] == 1
    assert [e - s for s, e in second['sides']] == [1]


def test_groups_ignore_notice_rows_before_first_trade():
    rows = build_trades_tab_rows(_data(), 2026)
    _, _, record_hdr, record_end = _trades_section_bounds(rows)
    assert _trade_record_groups(rows, record_hdr, record_end) == []
