"""The Home All-Time team is one board, one contract, in every format
(Kyle's ruling, 2026-08-17).

Before this the H2H Home kept its all-time team as a thin
Slot | Player | Points | ppg table in the left band while the season-points
Homes (CBS, then ESPN) rendered a full-width board. The ruling: the H2H
Home stacks its boards vertically -- Week, Season-to-Date, All-Time -- with
the all-time team under Season-to-Date at full width, in the shared
all-time row contract the CBS Home established:

    Slot | Team | Player | Fantasy Team | Owner | Points | Slash |
    Stat Line | Years of Service

with Years of Service from the fact-backed definition (distinct seasons of
qualifying active production, net-negative seasons included) through the
existing formatter ("count: compressed ranges"; a first-year player reads
"1: 2026"), and the title carrying the MEASURED era.

These tests pin every half of that: the thin table is gone from the H2H
book and cannot quietly return; the all-time board sits below
Season-to-Date at full width in both ESPN formats; Years of Service is
present and formatted by the shared formatter; the in-season boards (H2H
week + season, points month + season) are byte-for-byte what they were;
and the CBS formatter and the shared one agree.

Pure: synthetic rows, no warehouse. The Sheets writer's cosmetic pass is
covered by inspecting the format requests it builds from a row matrix.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import almanac_data
import almanac_logic
import almanac_render
import almanac_write
import cbs_almanac_sheets
import espn_points_data
from almanac_render import (
    HOME_ALLTIME_HEADER,
    HOME_DEVIATION_LABEL,
    HOME_HEADER,
    HOME_YEARS_OF_SERVICE_LABEL,
    format_all_league_alltime_row,
    format_all_league_team_row_with_deviation,
    format_years_of_service,
)

REPO = Path(__file__).resolve().parents[1]

# The retired thin contract, spelled out once so the guard below can prove
# it is nowhere in the rendered book.
_THIN_HEADER = ['Slot', 'Player', 'Points', 'ppg']


def _player_text(cell):
    if isinstance(cell, str):
        match = re.match(r'^=HYPERLINK\("[^"]*", "(.*)"\)$', cell)
        if match:
            return match.group(1)
    return cell


def _pick(slot, name, points, *, player_id=None, team_abbrev='HAR',
          owner='Owner One', pro_team='NYY', period_label='Season',
          service_years=None, **extra):
    row = {
        'slot_label': slot, 'lineup_slot': slot,
        'player_id': player_id or abs(hash(name)) % 100000,
        'player_name': name, 'display_name': name, 'pro_team': pro_team,
        'platform_points': points, 'position_pts': points,
        'team_id': 1, 'team_name': 'Harbor Otters', 'team_abbrev': team_abbrev,
        'owner_name': owner, 'period_label': period_label,
        'season_year': 2026, 'matchup_period': 7,
    }
    if service_years is not None:
        row['service_years'] = service_years
    row.update(extra)
    return row


WEEKLY = [_pick('C', 'Ada Kessler', 41.25, period_label=None)]
WEEKLY_ALL = [_pick('C', 'Bench Catcher', 60.0, period_label=None)]
SEASON = [_pick('C', 'Bo Nakamura', 308.0)]
SEASON_ALL = [_pick('C', 'Bo Nakamura', 308.0)]
ALL_TIME = [
    _pick('C', 'Bo Nakamura', 308.44, service_years='2026'),
    _pick('1B', 'Cy Marlow', 4477.5, service_years='2005,2006,2007,2009'),
]


def _h2h_home(**overrides):
    kwargs = dict(
        weekly_rows=WEEKLY, season_rows=SEASON, weekly_all_rows=WEEKLY_ALL,
        season_all_rows=SEASON_ALL, all_time_rows=ALL_TIME,
        season_year=2026, matchup_period=7, team_titles=['HAR', 'GRN'],
        league_id=1234567890, first_season=2001,
    )
    kwargs.update(overrides)
    return almanac_logic.build_home_tab_rows(**kwargs)


POINTS_BOARDS = {
    'month_window': None,
    'month_rows': [_pick('C', 'Ada Kessler', 41.0)],
    'month_all_rows': [_pick('C', 'Ada Kessler', 41.0)],
    'season_rows': SEASON,
    'season_all_rows': SEASON_ALL,
    'alltime_rows': ALL_TIME,
}


def _points_home(**overrides):
    from datetime import date
    kwargs = dict(
        boards=POINTS_BOARDS, season_year=2026,
        month_window=(date(2026, 8, 1), date(2026, 8, 13)),
        era_label='2001–2026', team_titles=['HAR', 'GRN'],
        league_id=1234567890,
    )
    kwargs.update(overrides)
    return almanac_logic.build_points_home_tab_rows(**kwargs)


def _left(rows):
    return [list(r[:4]) for r in rows]


def _right_first(rows):
    return [r[5] if len(r) > 5 else '' for r in rows]


def _find_title(rows, prefix):
    for i, cell in enumerate(_right_first(rows)):
        if isinstance(cell, str) and cell.startswith(prefix):
            return i
    raise AssertionError(f'no right-band title starting {prefix!r}')


# --------------------------------------------------------------------------
# The H2H Home: thin table gone, all-time under Season-to-Date, full width
# --------------------------------------------------------------------------

def test_h2h_home_no_longer_carries_the_thin_left_band_all_time_table():
    rows = _h2h_home()
    for row in _left(rows):
        assert row[:4] != _THIN_HEADER, 'thin header rendered in the left band'
        assert not (isinstance(row[0], str)
                    and row[0].startswith('All-League Team')), row
    # The retired columns are simply not in the book any more.
    assert 'ppg' not in {str(c) for r in rows for c in r}


def test_h2h_left_band_keeps_only_navigation_grid_and_glossary():
    rows = _h2h_home()
    left_first = [r[0] for r in _left(rows)]
    for label in ('Navigate', 'Records', 'Advanced Standings', 'Trades',
                  'Team Pages', 'Draft Recap', 'Matchup History',
                  'Points Glossary', 'Total Points', 'Wasted Points'):
        assert label in left_first, label
    grid = next(r for r in rows if len(r) > 2 and r[1] == 'HAR')
    assert grid[2] == 'GRN'
    # Every left-band cell is nav, grid, glossary or blank -- no slot codes.
    slots = {'C', '1B', 'SP 1', 'RP 1'}
    assert not any(r[0] in slots for r in _left(rows))


def test_h2h_all_time_board_sits_below_season_to_date_at_full_width():
    rows = _h2h_home()
    week_idx = _find_title(rows, 'All-League Team of the Week: 2026 Week 7')
    season_idx = _find_title(rows, 'All-League Team Season-to-Date: 2026')
    alltime_idx = _find_title(rows, 'All-League Team: All-Time')
    assert week_idx < season_idx < alltime_idx
    # Same separator + spacer discipline as between Week and Season.
    assert rows[alltime_idx - 1][5:] == [''] * len(rows[alltime_idx - 1][5:])
    assert rows[alltime_idx - 2][5:] == [''] * len(rows[alltime_idx - 2][5:])
    header = rows[alltime_idx + 1]
    assert header[5:14] == HOME_ALLTIME_HEADER
    assert HOME_ALLTIME_HEADER == [*HOME_HEADER, 'Years of Service']
    # Data rows follow the header, in the right band, in the full contract.
    first_pick = rows[alltime_idx + 2]
    assert first_pick[5] == 'C'
    assert _player_text(first_pick[7]) == 'Bo Nakamura'
    assert first_pick[8] == 'HAR' and first_pick[9] == 'Owner One'
    assert first_pick[10] == 308        # whole number at the all-time scale
    assert first_pick[13] == '1: 2026'  # Years of Service


def test_h2h_all_time_title_carries_the_measured_era():
    assert 'All-League Team: All-Time (2001–2026)' in _right_first(_h2h_home())
    # A first-year league reads a single season; nothing measured -> no
    # parenthetical, never '()' or a guessed range.
    assert 'All-League Team: All-Time (2026)' in _right_first(
        _h2h_home(first_season=2026))
    assert 'All-League Team: All-Time' in _right_first(
        _h2h_home(first_season=None))
    assert 'All-League Team: All-Time (' not in ' '.join(
        _right_first(_h2h_home(first_season=None)))
    assert almanac_logic.era_label(2001, 2026) == '2001–2026'
    assert almanac_logic.era_label(2026, 2026) == '2026'
    assert almanac_logic.era_label(None, None) == ''
    assert almanac_logic.era_label(None, 2026) == ''


def test_h2h_week_and_season_boards_are_unchanged_by_the_move():
    """The in-season boards keep their exact rows: header with the
    deviation pair, deviation cells populated where the all-lens pick
    differs, week Points as the boxscore link, season Points at 1dp."""
    rows = _h2h_home()
    header = [*HOME_HEADER, HOME_DEVIATION_LABEL, '']
    week_idx = _find_title(rows, 'All-League Team of the Week')
    season_idx = _find_title(rows, 'All-League Team Season-to-Date')
    assert rows[week_idx + 1][5:15] == header
    assert rows[season_idx + 1][5:15] == header
    week_dev = almanac_logic._deviation_by_slot(WEEKLY, WEEKLY_ALL)
    expected_week = format_all_league_team_row_with_deviation(
        WEEKLY[0], week_dev.get('C'), league_id=1234567890)
    assert rows[week_idx + 2][5:15] == expected_week
    assert str(rows[week_idx + 2][10]).startswith('=HYPERLINK(')
    assert _player_text(rows[week_idx + 2][13]) == 'Bench Catcher'
    expected_season = format_all_league_team_row_with_deviation(
        SEASON[0], None, league_id=1234567890)
    assert rows[season_idx + 2][5:15] == expected_season
    assert rows[season_idx + 2][10] == 308.0
    # Nothing between the two boards but the separator + spacer.
    assert season_idx == week_idx + 2 + len(WEEKLY) + 2


def test_h2h_row_width_and_band_alignment_hold():
    rows = _h2h_home()
    widths = {len(r) for r in rows[3:]}
    assert widths == {4 + 1 + len(HOME_HEADER) + 2}
    assert all(r[4] == '' for r in rows[3:]), 'spacer column E must stay blank'


# --------------------------------------------------------------------------
# The ESPN season-points Home: Month, Season, All-Time -- same contract
# --------------------------------------------------------------------------

def test_points_home_all_time_board_uses_the_shared_contract():
    rows = _points_home()
    month_idx = _find_title(rows, 'Team of the Month - August 2026')
    season_idx = _find_title(rows, 'Team of the Season: 2026')
    alltime_idx = _find_title(rows, 'All-League Team: All-Time (2001–2026)')
    assert month_idx < season_idx < alltime_idx
    assert rows[alltime_idx + 1][5:14] == HOME_ALLTIME_HEADER
    first_pick = rows[alltime_idx + 2]
    assert first_pick[5] == 'C'
    assert _player_text(first_pick[7]) == 'Bo Nakamura'
    assert first_pick[10] == 308
    assert first_pick[13] == '1: 2026'
    second = rows[alltime_idx + 3]
    assert second[13] == '4: 2005–2007, 2009'


def test_points_home_month_and_season_boards_are_unchanged():
    rows = _points_home()
    header = [*HOME_HEADER, HOME_DEVIATION_LABEL, '']
    month_idx = _find_title(rows, 'Team of the Month')
    season_idx = _find_title(rows, 'Team of the Season')
    assert rows[month_idx + 1][5:15] == header
    assert rows[season_idx + 1][5:15] == header
    assert rows[month_idx + 2][5:15] == format_all_league_team_row_with_deviation(
        POINTS_BOARDS['month_rows'][0], None, league_id=1234567890)
    assert rows[season_idx + 2][5:15] == format_all_league_team_row_with_deviation(
        SEASON[0], None, league_id=1234567890)
    assert 'rolls over on the 8th' in rows[month_idx][5]


def test_points_home_all_time_board_and_h2h_all_time_board_render_identical_rows():
    """One contract means one set of bytes: the same picks render the same
    all-time rows whichever book they sit in."""
    h2h = _h2h_home()
    pts = _points_home()
    h_idx = _find_title(h2h, 'All-League Team: All-Time')
    p_idx = _find_title(pts, 'All-League Team: All-Time')
    n = len(ALL_TIME) + 2
    assert [r[5:14] for r in h2h[h_idx:h_idx + n]] == \
        [r[5:14] for r in pts[p_idx:p_idx + n]]


def test_points_home_empty_all_time_board_still_says_so():
    boards = {**POINTS_BOARDS, 'alltime_rows': []}
    rows = _points_home(boards=boards)
    idx = _find_title(rows, 'All-League Team: All-Time')
    assert rows[idx + 1][5:14] == HOME_ALLTIME_HEADER
    assert rows[idx + 2][5] == 'No qualifying production in this window yet.'


# --------------------------------------------------------------------------
# Years of Service: the existing definition + formatter, not a new one
# --------------------------------------------------------------------------

def test_a_first_year_player_reads_one_colon_season():
    assert format_years_of_service('2026') == '1: 2026'


@pytest.mark.parametrize('listagg,expected', [
    ('2024,2025,2026', '3: 2024–2026'),
    ('2001,2002,2003,2009', '4: 2001–2003, 2009'),
    ('2005,2006,2007,2008,2009,2010,2011,2012,2013,2014,2015,2016,2017,'
     '2018,2019,2021,2022', '17: 2005–2019, 2021–2022'),
    ('', ''),
    (None, ''),
])
def test_multi_era_service_uses_the_compressed_range_formatter(listagg, expected):
    assert format_years_of_service(listagg) == expected


@pytest.mark.parametrize('years', [
    [2026], [2024, 2025, 2026], [2001, 2002, 2003, 2009],
    list(range(2001, 2020)) + [2021, 2022], [],
])
def test_the_shared_formatter_agrees_with_the_cbs_one(years):
    """The ESPN formatter mirrors the CBS almanac's _years_of_service
    character for character; the CBS Home is unchanged and the ESPN Homes
    now read the same string for the same seasons."""
    listagg = ','.join(str(y) for y in years)
    assert format_years_of_service(listagg) == \
        cbs_almanac_sheets._years_of_service(years)


def test_all_time_row_takes_years_of_service_from_the_fact_backed_column():
    """The renderer never derives service itself: no `service_years` on the
    row means a blank cell, and the row's team/owner context is whatever
    the selection already carried."""
    row = _pick('SS', 'Ines Rook', 812.6, team_abbrev='GRN', owner='Owner Two')
    cells = format_all_league_alltime_row(row)
    assert cells[:5] == ['SS', 'NYY', cells[2], 'GRN', 'Owner Two']
    assert _player_text(cells[2]) == 'Ines Rook'
    assert cells[5] == 813
    assert cells[8] == ''
    assert len(cells) == len(HOME_ALLTIME_HEADER)


def test_get_service_years_reads_the_fact_with_the_cbs_definition(monkeypatch):
    calls = []

    def fake_query(sql, params=None):
        calls.append((sql, params))
        return [{'player_id': 7, 'service_years': '2025,2026'},
                {'player_id': 9, 'service_years': '2026'}]

    monkeypatch.setattr(almanac_data, 'query_for_presentation', fake_query)
    out = almanac_data.get_service_years([7, 9, None])
    assert out == {7: '2025,2026', 9: '2026'}
    sql, params = calls[0]
    assert 'fct_player_season_performance' in sql
    assert "performance_status = 'active'" in sql
    # Nonzero at 1dp -- so a net-NEGATIVE season still counts as service.
    assert 'HAVING ROUND(CAST(SUM(CAST(calculated_points' in sql
    assert '<> 0' in sql
    assert 'GROUP BY player_id, season_year' in sql
    assert params == [7, 9]
    assert almanac_data.get_service_years([]) == {}


def test_home_all_time_team_attaches_service_years_to_each_pick(monkeypatch):
    picks = [{'player_id': 7, 'slot_label': 'C'},
             {'player_id': 9, 'slot_label': '1B'},
             {'player_id': 11, 'slot_label': 'SS'}]
    monkeypatch.setattr(almanac_data, 'get_optimal_team',
                        lambda season_year=None, matchup_period=None,
                        team_id=None, points_type='active': list(picks))
    monkeypatch.setattr(almanac_data, 'get_service_years',
                        lambda ids, team_id=None: {7: '2025,2026', 9: '2026'})
    team = almanac_data.get_home_all_time_team()
    assert [r['service_years'] for r in team] == ['2025,2026', '2026', '']
    assert [format_years_of_service(r['service_years']) for r in team] == \
        ['2: 2025–2026', '1: 2026', '']


def test_both_formats_fetch_the_same_home_all_time_team(monkeypatch):
    """H2H get_home_tab_data and the season-points home_boards read ONE
    definition of the all-time board, so the two books cannot drift."""
    sentinel = [{'player_id': 1, 'slot_label': 'C', 'service_years': '2026'}]
    monkeypatch.setattr(almanac_data, 'get_home_all_time_team', lambda: sentinel)
    monkeypatch.setattr(almanac_data, 'get_all_league_team',
                        lambda *a, **k: [])
    monkeypatch.setattr(almanac_data, 'get_optimal_team',
                        lambda *a, **k: [])
    monkeypatch.setattr(almanac_data, 'get_first_season', lambda: 2001)
    data = almanac_data.get_home_tab_data(2026, 7)
    assert data['all_time_rows'] is sentinel
    assert data['first_season'] == 2001

    monkeypatch.setattr(espn_points_data, 'month_window',
                        lambda context, today=None: (None, None, None, None))
    monkeypatch.setattr(espn_points_data, 'window_lineup',
                        lambda *a, **k: [])
    monkeypatch.setattr(espn_points_data, 'with_unique_team_abbrevs',
                        lambda rows: rows)
    boards = espn_points_data.home_boards({'season_year': 2026})
    assert boards['alltime_rows'] is sentinel
    assert 'alltime_all_rows' not in boards


# --------------------------------------------------------------------------
# The obsolete thin layout cannot quietly return
# --------------------------------------------------------------------------

def test_the_thin_row_formatter_and_thin_header_are_gone():
    assert not hasattr(almanac_render, 'format_all_league_thin_row')
    assert almanac_render.HOME_ALLTIME_HEADER != _THIN_HEADER
    assert almanac_render.HOME_ALLTIME_HEADER[-1] == HOME_YEARS_OF_SERVICE_LABEL
    # The H2H left band no longer takes an all-time lineup at all.
    params = inspect.signature(almanac_logic._home_left_rows).parameters
    assert 'all_time_rows' not in params and 'align_alltime_to' not in params


def test_no_output_module_still_spells_the_thin_contract():
    """A source-level guard: the retired symbols and the ppg column are
    absent from every rendering module, so nothing can re-import or
    re-emit the thin table without failing here first."""
    banned = ('format_all_league_thin_row', "'Points', 'ppg'",
              'align_alltime_to')
    hits = []
    for path in sorted((REPO / 'output').glob('*.py')):
        text = path.read_text(encoding='utf-8')
        for token in banned:
            if token in text:
                hits.append(f'{path.name}: {token}')
    assert not hits, hits


def test_the_writer_no_longer_paints_the_left_band_all_time_formats():
    """The Sheets polish pass used to give the left band whole-number
    Points (C) and 2dp ppg (D) and bold the thin 'Slot | Player' header.
    Those anchors are gone; K splits at the all-time title instead."""
    rows = _h2h_home()
    labels = almanac_write._home_label_formats(rows, 'O')
    ranges = [f['range'] for f in labels]
    alltime_row = _find_title(rows, 'All-League Team: All-Time') + 1
    assert f'F{alltime_row}:O{alltime_row}' in ranges         # bold title
    assert f'F{alltime_row + 1}:O{alltime_row + 1}' in ranges  # navy header
    assert not any(r.startswith('A') and rows[int(r[1:r.index(':')]) - 1][0] == 'Slot'
                   for r in ranges)
    assert 'All-League Team: All-Time' not in almanac_write._HOME_LEFT_SECTION_LABELS

    points = almanac_write._home_points_formats(rows)
    assert points == [
        {'range': f'K1:K{alltime_row}',
         'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}}},
        {'range': f'K{alltime_row + 1}:K',
         'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}}},
    ]
    src = inspect.getsource(almanac_write._replace_home_tab)
    assert "'C:C'" not in src and "'D:D'" not in src
