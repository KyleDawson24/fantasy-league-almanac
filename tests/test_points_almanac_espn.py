"""The ESPN points workbook's content and omissions (MLB-243).

The rehearsal league is a FIRST-YEAR ESPN season-points league still in
flight: no matchup has ever been played, no season has ever finished, and
`mart_team_season_standings` is correctly empty because it aggregates
matchup results that do not exist. The workbook it received was the
head-to-head one.

What these tests pin is that the points workbook shows what a league like
that actually has -- accumulating totals, from day one -- and honestly
declines what it does not. Nothing here waits for a completed matchup.

Pure: every builder is fed synthetic rows. No warehouse, no network, no
Google surface. The team names are invented.
"""
from __future__ import annotations

from datetime import date

import pytest

import almanac_logic
import espn_points_data
import points_almanac
import sheets_workbook
from almanac_render import TEAM_WEEKS_TAB


# --------------------------------------------------------------------------
# Synthetic league: three teams, one in-flight season, no completed anything.
# Two of them deliberately share an abbreviation.
# --------------------------------------------------------------------------

STAT_SPECS = [
    {'stat_name': 'HR', 'stat_category': 'hitting'},
    {'stat_name': 'K', 'stat_category': 'pitching'},
]

# The row shape espn_points_data.standings_rows produces: the SAME contract
# almanac_data.get_team_standings returns, sourced from the season fact.
# W/L/T are None -- this format has no record, and 0-0 would be a result.
SEASON_TOTALS = [
    {'team_id': 1, 'team_name': 'Harbor Otters', 'team_abbrev': 'HAR',
     'owner_display': 'Owner unavailable', 'wins': None, 'losses': None,
     'ties': None, 'matchup_periods_played': 142, 'scoring_days_played': 142,
     'standard_matchup_days': 1, 'calculated_points': 5361.0,
     'calculated_hitting_pts': 2796.0, 'calculated_pitching_pts': 2565.0,
     'against_calculated_points': None, 'hr': 210, 'k': 1400},
    {'team_id': 2, 'team_name': 'Granite Owls', 'team_abbrev': 'GRN',
     'owner_display': 'Owner unavailable', 'wins': None, 'losses': None,
     'ties': None, 'matchup_periods_played': 142, 'scoring_days_played': 142,
     'standard_matchup_days': 1, 'calculated_points': 4641.0,
     'calculated_hitting_pts': 2614.0, 'calculated_pitching_pts': 2027.0,
     'against_calculated_points': None, 'hr': 180, 'k': 1200},
    {'team_id': 3, 'team_name': 'Gale Ridge Giants', 'team_abbrev': 'GRN',
     'owner_display': 'Owner unavailable', 'wins': None, 'losses': None,
     'ties': None, 'matchup_periods_played': 142, 'scoring_days_played': 142,
     'standard_matchup_days': 1, 'calculated_points': 3669.0,
     'calculated_hitting_pts': 2174.0, 'calculated_pitching_pts': 1495.0,
     'against_calculated_points': None, 'hr': 150, 'k': 1000},
]

SLOT_ROWS = [
    {'team_id': 1, 'team_name': 'Harbor Otters', 'team_abbrev': 'HAR',
     'owner_display': 'Owner unavailable', 'lineup_slot': 'C',
     'slot_pts': 308.0, 'sort_order': 1,
     'is_active_lineup_slot': True, 'starter_count': 1},
    {'team_id': 1, 'team_name': 'Harbor Otters', 'team_abbrev': 'HAR',
     'owner_display': 'Owner unavailable', 'lineup_slot': 'P',
     'slot_pts': 2565.0, 'sort_order': 8,
     'is_active_lineup_slot': True, 'starter_count': 7},
    {'team_id': 2, 'team_name': 'Granite Owls', 'team_abbrev': 'GRN',
     'owner_display': 'Owner unavailable', 'lineup_slot': 'C',
     'slot_pts': 242.0, 'sort_order': 1,
     'is_active_lineup_slot': True, 'starter_count': 1},
    {'team_id': 2, 'team_name': 'Granite Owls', 'team_abbrev': 'GRN',
     'owner_display': 'Owner unavailable', 'lineup_slot': 'P',
     'slot_pts': 2027.0, 'sort_order': 8,
     'is_active_lineup_slot': True, 'starter_count': 7},
]


def _standings(**kw):
    """Advanced Standings through THE REAL BUILDER -- the one both mature
    books use -- in points mode."""
    return almanac_logic.build_advanced_standings_tab_rows(
        SEASON_TOTALS, SLOT_ROWS, STAT_SPECS, 2026, season_long=True, **kw)


def _cells(rows):
    return [str(cell) for row in rows for cell in row]


# --------------------------------------------------------------------------
# Advanced Standings: the REAL builder, in points mode
# --------------------------------------------------------------------------

def test_advanced_standings_is_populated_for_an_unfinished_first_season():
    """No completed matchup, no completed season, real numbers anyway."""
    flat = _cells(_standings())
    assert 'HAR' in flat
    assert any('5361' in c for c in flat), 'the season total did not render'
    assert 'Detailed Standings' in flat


def test_the_points_tab_uses_the_rich_builder_not_a_simplified_twin():
    """The earlier stand-in rendered two tables. The established books carry
    detailed standings, slot grids and the rest, and a points league now
    gets the same tab."""
    flat = ' '.join(_cells(_standings()))
    assert 'Detailed Standings' in flat
    assert 'Lineup Slot' in flat


def test_the_identity_column_is_points_not_a_win_loss_record():
    """A points league has no record; "0-0" there is a fabricated result."""
    rows = _standings()
    header = next(r for r in rows if r and r[0] == 'Rank')
    assert header[3] == 'Total Pts'
    assert 'W-L' not in header
    leader = next(r for r in rows if r and r[0] == 1 and r[1] == 'HAR')
    assert leader[3] == 5361
    assert '-' not in str(leader[3])


def test_the_h2h_identity_column_is_untouched():
    """Same builder, default mode: the pinned corpus depends on this."""
    h2h = [dict(r, wins=9, losses=4, ties=0) for r in SEASON_TOTALS]
    rows = almanac_logic.build_advanced_standings_tab_rows(
        h2h, SLOT_ROWS, STAT_SPECS, 2026)
    header = next(r for r in rows if r and r[0] == 'Rank')
    assert header[3] == 'W-L'
    leader = next(r for r in rows if r and r[0] == 1)
    assert leader[3] == '9-4'


def test_the_two_headers_are_the_same_width():
    """Every gradient, chart helper column and block offset is positional,
    so a different-width identity block would silently mispaint the tab."""
    import almanac_render
    assert (len(almanac_render.STANDINGS_FIXED_HEADER)
            == len(almanac_render.STANDINGS_FIXED_HEADER_SEASON_LONG))
    hit, pit = [STAT_SPECS[0]], [STAT_SPECS[1]]
    assert (len(almanac_render.standings_header(hit, pit))
            == len(almanac_render.standings_header(hit, pit, season_long=True)))


def test_the_detailed_standings_scope_label_is_not_weekly_averages():
    """There are no weeks to average over."""
    flat = ' '.join(_cells(_standings()))
    assert 'Season Totals, Current Season' in flat
    assert 'Weekly Averages' not in flat


def test_the_standings_note_drops_divisions_and_weeks():
    flat = ' '.join(_cells(_standings()))
    assert 'no matchups or W-L records' in flat
    assert 'division winners first' not in flat


# --------------------------------------------------------------------------
# Acquisition channels: only when the log was actually read
# --------------------------------------------------------------------------

ACQ_ROWS = [{
    'team_id': 1, 'team_abbrev': 'HAR', 'owner_display': 'Owner unavailable',
    'keeper_active_pts': 0, 'draft_active_pts': 500, 'trade_active_pts': 0,
    'fa_add_active_pts': 40, 'acquired_active_pts': 540,
    'dropped_active_pts': 0, 'traded_away_active_pts': 0,
    'lost_active_pts': 0, 'fa_delta_active_pts': 40,
    'trade_delta_active_pts': 0, 'keeper_rostered_pts': 0,
    'draft_rostered_pts': 500, 'trade_rostered_pts': 0,
    'fa_add_rostered_pts': 40, 'acquired_rostered_pts': 540,
    'dropped_rostered_pts': 0, 'traded_away_rostered_pts': 0,
    'lost_rostered_pts': 0, 'fa_delta_rostered_pts': 40,
    'trade_delta_rostered_pts': 0,
}]


def test_a_read_log_renders_the_acquisition_tables():
    flat = ' '.join(_cells(_standings(acquisition_rows=ACQ_ROWS)))
    assert 'Production by Acquisition Channel' in flat


def test_an_unread_log_says_so_instead_of_charting_zeroes():
    """A grid of zeroes asserts nobody made a move all year. That is a
    claim, and an unread log does not support it."""
    note = points_almanac.ACQUISITION_UNAVAILABLE_NOTE
    flat = ' '.join(_cells(_standings(acquisition_rows=None,
                                      acquisition_unavailable=note)))
    assert 'Production by Acquisition Channel' in flat
    assert 'could not be read' in flat
    assert 'does NOT mean there were no' in flat


def test_an_unread_log_never_fabricates_a_channel_classification():
    note = points_almanac.ACQUISITION_UNAVAILABLE_NOTE
    rows = _standings(acquisition_rows=None, acquisition_unavailable=note)
    cells = _cells(rows)
    # The channel column headers only exist when a real table is drawn.
    for fabricated in ('Keeper', 'Opening', 'Picks', 'Trade', 'FA'):
        assert fabricated not in cells, (
            f'{fabricated!r} rendered as a channel over an unread log'
        )


def test_the_adapter_withholds_channels_when_coverage_is_absent(monkeypatch):
    """The gate lives in the adapter, so no caller can accidentally chart an
    unread log."""
    monkeypatch.setattr(espn_points_data, 'query_snowflake',
                        lambda sql, params=None: [{'has_transaction_log': False}])
    monkeypatch.setattr(espn_points_data, 'league_predicate',
                        lambda alias=None: "league_key = 'x'")
    assert espn_points_data.acquisition_channels(2026) is None


def test_the_adapter_returns_channels_when_coverage_is_proven(monkeypatch):
    import almanac_data
    monkeypatch.setattr(espn_points_data, 'query_snowflake',
                        lambda sql, params=None: [{'has_transaction_log': True}])
    monkeypatch.setattr(espn_points_data, 'league_predicate',
                        lambda alias=None: "league_key = 'x'")
    monkeypatch.setattr(almanac_data, 'get_team_acquisition_channels',
                        lambda season: ACQ_ROWS)
    assert espn_points_data.acquisition_channels(2026) == ACQ_ROWS


# --------------------------------------------------------------------------
# The standing-by-period chart walks scoring days
# --------------------------------------------------------------------------

def test_the_chart_apparatus_renders_for_a_points_league():
    arc = [{'team_id': t, 'team_abbrev': a, 'period': p, 'standings_rank': r}
           for p in (1, 2, 3)
           for r, (t, a) in enumerate([(1, 'HAR'), (2, 'GRN')], start=1)]
    flat = ' '.join(_cells(_standings(rank_arc_rows=arc))).lower()
    assert 'time series' in flat


# --------------------------------------------------------------------------
# The Rivalry Matrix keeps its completed-season requirement
# --------------------------------------------------------------------------

def test_an_unfinished_season_is_not_reinterpreted_as_a_rivalry_result():
    """The SHARED rivalry block already says this, so the points tab needs
    no wording of its own -- that is the reapplication working."""
    axes = [{'identity_key': 'fid:1', 'identity_name': 'Harbor Otters',
             'identity_abbrev': 'HAR', 'sort_order': 1,
             'identity_source': 'franchise_id', 'league_format': 'points',
             'has_rivalry_evidence': False, 'active_platform_teams': 1,
             'admissible_seasons': 0, 'current_season': 2026},
            {'identity_key': 'fid:2', 'identity_name': 'Granite Owls',
             'identity_abbrev': 'GRN', 'sort_order': 2,
             'identity_source': 'franchise_id', 'league_format': 'points',
             'has_rivalry_evidence': False, 'active_platform_teams': 1,
             'admissible_seasons': 0, 'current_season': 2026}]
    flat = ' '.join(_cells(_standings(rivalry_axes=axes, rivalry_pairs=[])))
    assert 'RIVALRY RESULTS UNAVAILABLE' in flat


# --------------------------------------------------------------------------
# Home: Team of the Month / Season / All-Time
# --------------------------------------------------------------------------

def _board_row(slot, name, points):
    return {'slot_label': slot, 'player_key': f'k:{name}',
            'player_id': hash(name) % 100000, 'player_name': name,
            'display_name': name, 'pro_team': 'NYY',
            'platform_points': points, 'position_pts': points,
            'team_name': 'Harbor Otters', 'team_abbrev': 'HAR',
            'owner_name': 'Owner unavailable', 'period_label': 'Season'}


BOARDS = {
    'month_window': (date(2026, 8, 1), date(2026, 8, 13)),
    'month_rows': [_board_row('C', 'Ada Kessler', 41.0)],
    'month_all_rows': [_board_row('C', 'Ada Kessler', 41.0)],
    'season_rows': [_board_row('C', 'Bo Nakamura', 308.0)],
    'season_all_rows': [_board_row('C', 'Bo Nakamura', 308.0)],
    'alltime_rows': [_board_row('C', 'Bo Nakamura', 308.0)],
    'alltime_all_rows': [_board_row('C', 'Bo Nakamura', 308.0)],
}


def test_home_right_side_carries_the_three_points_boards():
    rows = almanac_logic.build_points_home_tab_rows(
        BOARDS, season_year=2026, month_window=BOARDS['month_window'],
        era_label='2026', team_titles=['HAR', 'GRN'])
    flat = ' '.join(_cells(rows))
    assert 'Team of the Month - August 2026' in flat
    assert 'rolls over on the 8th' in flat
    assert 'Team of the Season: 2026' in flat
    assert 'All-Time Team (2026)' in flat


def test_home_is_populated_from_day_one_of_an_in_progress_season():
    rows = almanac_logic.build_points_home_tab_rows(
        BOARDS, season_year=2026, month_window=BOARDS['month_window'],
        era_label='2026', team_titles=['HAR'])
    flat = ' '.join(_cells(rows))
    assert 'Ada Kessler' in flat, 'the month board rendered no players'
    assert 'Bo Nakamura' in flat, 'the season board rendered no players'


def test_home_nav_omits_matchup_history_for_a_points_league():
    """It is a week-by-week MATCHUP archive. Linking to a tab this format
    does not build would be a dead link over a concept the league lacks."""
    rows = almanac_logic.build_points_home_tab_rows(
        BOARDS, season_year=2026, month_window=BOARDS['month_window'],
        era_label='2026', team_titles=['HAR'])
    assert TEAM_WEEKS_TAB not in ' '.join(_cells(rows))


def test_an_empty_board_says_so_rather_than_rendering_nothing():
    boards = {**BOARDS, 'month_rows': [], 'month_all_rows': []}
    rows = almanac_logic.build_points_home_tab_rows(
        boards, season_year=2026, month_window=boards['month_window'],
        era_label='2026', team_titles=['HAR'])
    assert 'No qualifying production in this window yet.' in _cells(rows)


def test_a_missing_date_anchor_does_not_invent_a_month():
    rows = almanac_logic.build_points_home_tab_rows(
        BOARDS, season_year=2026, month_window=(None, None),
        era_label='2026', team_titles=['HAR'])
    assert 'window unavailable' in ' '.join(_cells(rows))


# --------------------------------------------------------------------------
# Transactions: unavailable is not zero
# --------------------------------------------------------------------------

def test_home_states_an_unavailable_trades_feed():
    """The rehearsal got HTTP 401 from the communications feed. Silence
    there would read as "no trades happened"."""
    rows = almanac_logic.build_points_home_tab_rows(
        BOARDS, season_year=2026, month_window=BOARDS['month_window'],
        era_label='2026', team_titles=['HAR'],
        trades_note=points_almanac.TRADES_UNAVAILABLE_NOTE)
    flat = ' '.join(_cells(rows))
    assert 'does NOT mean no trades happened' in flat


def test_an_available_feed_links_the_tab_and_adds_no_note():
    rows = almanac_logic.build_points_home_tab_rows(
        BOARDS, season_year=2026, month_window=BOARDS['month_window'],
        era_label='2026', team_titles=['HAR'], trades_note=None)
    flat = ' '.join(_cells(rows))
    assert 'Trades' in flat
    assert 'does NOT mean no trades happened' not in flat


# --------------------------------------------------------------------------
# The 8th-of-month rollover
# --------------------------------------------------------------------------

@pytest.mark.parametrize('today,expected_month', [
    (date(2026, 8, 13), 8),   # past the 8th -> the current month, accruing
    (date(2026, 8, 8), 8),    # the boundary itself rolls over
    (date(2026, 8, 3), 7),    # first week -> retrospect on last month
])
def test_the_month_window_rolls_over_on_the_eighth(today, expected_month):
    context = {'season_opener': date(2026, 3, 25),
               'latest_date': date(2026, 8, 31)}
    lo, hi, sp_lo, sp_hi = espn_points_data.month_window(context, today=today)
    assert lo.month == expected_month
    assert sp_lo == (lo - context['season_opener']).days + 1


def test_the_month_window_caps_at_the_last_captured_day():
    """A running month must not claim days we have no data for."""
    context = {'season_opener': date(2026, 3, 25),
               'latest_date': date(2026, 8, 13)}
    lo, hi, _, _ = espn_points_data.month_window(
        context, today=date(2026, 8, 20))
    assert hi == date(2026, 8, 13)


def test_the_month_window_steps_back_when_the_month_has_no_data():
    context = {'season_opener': date(2026, 3, 25),
               'latest_date': date(2026, 7, 20)}
    lo, hi, _, _ = espn_points_data.month_window(
        context, today=date(2026, 9, 15))
    assert lo.month == 7 and hi == date(2026, 7, 20)


def test_no_date_anchor_yields_no_window_rather_than_a_guess():
    assert espn_points_data.month_window(
        {'season_opener': None, 'latest_date': None}) == (None, None, None, None)


def test_scoring_periods_map_to_dates_through_the_season_opener():
    """ESPN numbers scoring periods as contiguous days from the opener;
    `game_date` is NULL on every ESPN row, so this is the anchor."""
    opener = date(2026, 3, 25)
    assert espn_points_data.period_to_date(1, opener) == opener
    assert espn_points_data.period_to_date(142, opener) == date(2026, 8, 13)
    assert espn_points_data.date_to_period(date(2026, 8, 1), opener) == 130
    assert espn_points_data.period_to_date(5, None) is None


# --------------------------------------------------------------------------
# Team tabs cannot collide
# --------------------------------------------------------------------------

def test_two_teams_sharing_an_abbrev_get_distinct_tabs():
    """A tab title is a KEY. Two 'GRN's meant the second team's page
    overwrote the first's, and a franchise vanished from the book."""
    teams = {
        2: {'team_id': 2, 'team_abbrev': 'GRN', 'team_name': 'Granite Owls'},
        3: {'team_id': 3, 'team_abbrev': 'GRN', 'team_name': 'Gale Ridge'},
    }
    title = almanac_logic._unique_team_titles(
        teams, lambda meta: meta['team_abbrev'])
    titles = {title(meta) for meta in teams.values()}
    assert len(titles) == 2, f'two franchises share one tab title: {titles}'
    assert titles == {'GRN 2', 'GRN 3'}


def test_distinct_abbrevs_keep_their_titles_untouched():
    """The disambiguation engages ONLY on collision, so no existing book's
    tab names move."""
    teams = {
        1: {'team_id': 1, 'team_abbrev': 'HAR'},
        2: {'team_id': 2, 'team_abbrev': 'GRN'},
    }
    title = almanac_logic._unique_team_titles(
        teams, lambda meta: meta['team_abbrev'])
    assert title(teams[1]) == 'HAR'
    assert title(teams[2]) == 'GRN'


# --------------------------------------------------------------------------
# The blank default Sheet1
# --------------------------------------------------------------------------

class _WS:
    def __init__(self, title, gid, values=None):
        self.title, self.id = title, gid
        self._values = values or []

    def get_all_values(self):
        return self._values


class _Spreadsheet:
    def __init__(self, sheets):
        self._sheets = sheets
        self.deleted = []

    def worksheets(self):
        return list(self._sheets)

    def del_worksheet(self, ws):
        self.deleted.append(ws.title)
        self._sheets.remove(ws)


class _Client:
    def __init__(self, spreadsheet):
        self._spreadsheet = spreadsheet

    def open_by_key(self, key):
        return self._spreadsheet


def test_the_untouched_default_sheet1_is_removed_after_real_tabs_exist():
    book = _Spreadsheet([_WS('Sheet1', 0), _WS('Home', 12), _WS('Records', 13)])
    assert sheets_workbook.remove_default_sheet(_Client(book), 'ID') is True
    assert book.deleted == ['Sheet1']


def test_a_sheet1_with_content_is_never_deleted():
    """Google's default is disposable. A Sheet1 somebody typed in is not."""
    book = _Spreadsheet([_WS('Sheet1', 0, [['my notes']]), _WS('Home', 12)])
    assert sheets_workbook.remove_default_sheet(_Client(book), 'ID') is False
    assert book.deleted == []


def test_a_sheet1_that_is_not_the_default_grid_is_never_deleted():
    """Title alone is not identifying: the real default also has gid 0."""
    book = _Spreadsheet([_WS('Sheet1', 77), _WS('Home', 12)])
    assert sheets_workbook.remove_default_sheet(_Client(book), 'ID') is False


def test_the_last_remaining_sheet_is_never_deleted():
    """A render that silently produced nothing must not leave an empty
    workbook behind."""
    book = _Spreadsheet([_WS('Sheet1', 0)])
    assert sheets_workbook.remove_default_sheet(_Client(book), 'ID') is False


def test_sheet1_removal_only_happens_on_the_app_created_lifecycle():
    """Configured dev/prod workbooks are the user's own long-lived books.
    Nothing in their write path may reach the remover -- the ONLY caller is
    publish_workbook, which exists solely for app-created workbooks."""
    import almanac_write
    import cbs_almanac_sheets

    source = sheets_workbook.publish_workbook.__doc__ or ''
    del source
    for module in (almanac_write, cbs_almanac_sheets, points_almanac):
        text = open(module.__file__, encoding='utf-8').read()
        assert 'remove_default_sheet' not in text, (
            f'{module.__name__} can delete a tab from a configured workbook'
        )


def test_a_failure_to_tidy_never_fails_the_publish():
    class _Boom:
        def open_by_key(self, key):
            raise RuntimeError('drive said no')

    assert sheets_workbook.remove_default_sheet(_Boom(), 'ID') is False


# --------------------------------------------------------------------------
# The assembled tab set
# --------------------------------------------------------------------------

@pytest.fixture
def assembled(monkeypatch):
    """build_all_tabs over stubbed data -- no warehouse, no network.

    Everything a first-year in-flight league actually has is supplied;
    everything it does not (a completed season, a readable trades feed) is
    supplied as absent, because that is the case under test.
    """
    import almanac_data

    monkeypatch.setattr(espn_points_data, 'season_context', lambda: {
        'season_year': 2026, 'first_period': 1, 'last_period': 142,
        'season_opener': date(2026, 3, 25), 'latest_date': date(2026, 8, 13),
        'first_season': 2026,
    })
    import almanac_data
    monkeypatch.setattr(espn_points_data, 'standings_rows',
                        lambda season, specs: SEASON_TOTALS)
    monkeypatch.setattr(espn_points_data, 'rank_arc', lambda season: [])
    monkeypatch.setattr(espn_points_data, 'season_finishes', lambda: [])
    monkeypatch.setattr(espn_points_data, 'acquisition_channels',
                        lambda season: None)
    monkeypatch.setattr(almanac_data, 'get_team_week_stat_specs',
                        lambda: STAT_SPECS)
    monkeypatch.setattr(almanac_data, 'get_team_slot_points',
                        lambda season: SLOT_ROWS)
    monkeypatch.setattr(almanac_data, 'get_team_slot_points_alltime',
                        lambda: [])
    monkeypatch.setattr(almanac_data, 'get_team_affinity_weights',
                        lambda season: [])
    monkeypatch.setattr(almanac_data, 'get_rivalry_axes', lambda: [])
    monkeypatch.setattr(almanac_data, 'get_rivalry_matrix', lambda: [])
    monkeypatch.setattr(espn_points_data, 'home_boards',
                        lambda context, today=None: BOARDS)
    # No season has finished -- the first year is still in flight.
    monkeypatch.setattr(espn_points_data, 'has_completed_season',
                        lambda season: False)
    monkeypatch.setattr(espn_points_data, 'late_draft_note',
                        lambda context: None)

    records = [['League Records'], ['Scope', 'Record', 'Holder']]
    monkeypatch.setattr(points_almanac, 'build_records_tab_rows',
                        lambda **kw: records)
    monkeypatch.setattr(almanac_data, 'get_almanac_records', lambda scope: [])
    monkeypatch.setattr(almanac_data, 'get_franchise_hall_of_fame', lambda: [])
    monkeypatch.setattr(almanac_data, 'get_wasted_hall_of_shame', lambda: [])
    monkeypatch.setattr(almanac_data, 'get_team_roster_history_stats',
                        lambda season: [])
    monkeypatch.setattr(almanac_data, 'get_roster_slot_capacities',
                        lambda season, include_inactive=False: {})
    monkeypatch.setattr(almanac_data, 'team_best_seasons_fn', lambda: None)
    monkeypatch.setattr(points_almanac, 'build_team_history_tabs',
                        lambda *a, **kw: [('HAR', [['Harbor Otters']])])
    monkeypatch.setattr(almanac_data, 'get_draft_board', lambda season: [])
    monkeypatch.setattr(almanac_data, 'get_draft_history_boards',
                        lambda season: [])
    monkeypatch.setattr(almanac_data, 'get_season_scoring_periods', lambda: [])
    monkeypatch.setattr(points_almanac, 'build_draft_tab_rows',
                        lambda *a, **kw: [['Draft Recap']])
    return points_almanac


def test_the_points_book_omits_matchup_history(assembled):
    """The tab is a week-by-week MATCHUP archive. This league has none, so
    it is absent rather than rendered as an empty H2H artifact."""
    tabs, _context, _titles = assembled.build_all_tabs(include_trades=False)
    titles = [t[0] for t in tabs]
    assert TEAM_WEEKS_TAB not in titles, f'Matchup History was built: {titles}'


def test_an_in_flight_first_season_still_gets_home_records_and_standings(
        assembled):
    """No completed matchup, no completed season, a full book anyway."""
    tabs, _context, _titles = assembled.build_all_tabs(include_trades=False)
    by_title = {t[0]: t[1] for t in tabs}
    assert 'Home' in by_title and 'Records' in by_title
    assert 'Advanced Standings' in by_title

    assert any('Team of the Month' in str(c)
               for row in by_title['Home'] for c in row)
    assert by_title['Records'], 'the Records tab came out empty'
    # The tab comes from the SHARED season-points presenter, so what is
    # asserted here is that its sections arrived -- the numbers inside them
    # are pinned by the presenter-level tests above.
    standings = ' '.join(
        str(c) for row in by_title['Advanced Standings'] for c in row)
    for section in ('RANK BY SCORING DAY', 'SEASON FINISHES',
                    'POINTS BY LINEUP SLOT', 'DETAILED STANDINGS'):
        assert section in standings, f'{section} missing from Advanced Standings'


def test_the_points_record_book_drops_the_matchup_language():
    """Same shared records, this format's vocabulary.

    The standing captions promise "standard-length matchups" and records
    "set last week", and the Period cell renders "Week 1" linked to a
    matchup boxscore. A season-points league has none of those, and each
    one is a claim about how a record was set.
    """
    import almanac_render

    rows = almanac_logic.build_records_tab_rows(
        all_time_records=[], current_season_records=[],
        record_specs=[], schedule_lookup={}, season_long=True)
    caption = ' '.join(_cells(rows))
    assert 'standard-length matchups' not in caption
    assert 'set last week' not in caption
    assert 'season total rather than a single week' in caption

    record = {'entity_grain': 'team', 'team_abbrev': 'HAR',
              'owner_name': 'Owner unavailable', 'season_year': 2026,
              'matchup_period': 1, 'stat_name': 'HR', 'stat_value': 12,
              'team_id': 1}
    side = almanac_render._format_record_side(
        record, scope='current_season', league_id='1', schedule_lookup={},
        season_long=True)
    assert side[3] == '2026', f'the period cell is not the season: {side[3]!r}'
    assert 'HYPERLINK' not in str(side[3])


def test_the_h2h_record_book_language_is_untouched():
    """The default must stay byte-identical -- the goldens depend on it."""
    rows = almanac_logic.build_records_tab_rows(
        all_time_records=[], current_season_records=[],
        record_specs=[], schedule_lookup={})
    caption = ' '.join(_cells(rows))
    assert 'Counting Stats only look at standard-length matchups.' in caption
    assert 'Current Season records set last week are italicized.' in caption


def test_home_is_first_so_the_preview_opens_on_it(assembled):
    tabs, _context, _titles = assembled.build_all_tabs(include_trades=False)
    assert tabs[0][0] == 'Home'


def test_an_unextracted_league_says_so_once(assembled, monkeypatch):
    monkeypatch.setattr(espn_points_data, 'season_context', lambda: {
        'season_year': None, 'first_period': None, 'last_period': None,
        'season_opener': None, 'latest_date': None, 'first_season': None,
    })
    tabs, _context, _titles = assembled.build_all_tabs(include_trades=False)
    assert len(tabs) == 1
    assert 'No season data has been extracted' in str(tabs[0][1])


# --------------------------------------------------------------------------
# The late-start limitation: detected and stated, never silently corrected
# --------------------------------------------------------------------------

def _late_draft_context(monkeypatch, drafted_at):
    monkeypatch.setattr(espn_points_data, 'query_snowflake',
                        lambda sql, params=None: [{'drafted_at': drafted_at}])
    monkeypatch.setattr(espn_points_data, 'league_predicate',
                        lambda alias=None: "league_key = 'espn-main'")
    return {'season_opener': date(2026, 3, 25), 'season_year': 2026}


def test_a_late_draft_produces_a_plain_language_warning(monkeypatch):
    """The rehearsal case: drafted July 31, counting from day one of the
    MLB season."""
    context = _late_draft_context(monkeypatch, date(2026, 7, 31))
    note = espn_points_data.late_draft_note(context)
    assert note is not None
    assert 'July 31, 2026' in note
    assert '128 days' in note
    # It must say which way the distortion runs, or the reader cannot use it.
    assert 'Comparisons BETWEEN teams stay fair' in note
    assert 'larger than what was actually managed' in note


def test_an_on_time_draft_says_nothing(monkeypatch):
    """Silence is the right answer for an ordinary league."""
    context = _late_draft_context(monkeypatch, date(2026, 3, 22))
    assert espn_points_data.late_draft_note(context) is None


def test_a_draft_inside_the_grace_window_says_nothing(monkeypatch):
    context = _late_draft_context(monkeypatch, date(2026, 4, 5))
    assert espn_points_data.late_draft_note(context) is None


def test_an_undated_draft_says_nothing_rather_than_guessing(monkeypatch):
    context = _late_draft_context(monkeypatch, None)
    assert espn_points_data.late_draft_note(context) is None


def test_no_season_calendar_means_no_claim(monkeypatch):
    monkeypatch.setattr(espn_points_data, 'query_snowflake',
                        lambda sql, params=None: [{'drafted_at': date(2026, 7, 31)}])
    assert espn_points_data.late_draft_note(
        {'season_opener': None, 'season_year': 2026}) is None


def test_the_warning_reaches_home_and_advanced_standings():
    caveat = 'Heads up: this league drafted on July 31, 2026'
    home = almanac_logic.build_points_home_tab_rows(
        BOARDS, season_year=2026, month_window=BOARDS['month_window'],
        era_label='2026', team_titles=['HAR'], caveat=caveat)
    standings = _standings(caveat=caveat)
    assert caveat in _cells(home)
    assert caveat in _cells(standings)


def test_no_caveat_leaves_both_tabs_exactly_as_they_were():
    """An ordinary league must not gain a blank row where the warning
    would have gone."""
    with_none = _standings(caveat=None)
    assert all(r != [None] and r != [''] for r in with_none[:4])
    assert with_none[2] == []


def test_the_warning_does_not_change_any_number():
    """Detected and stated -- never silently corrected. The totals with the
    caveat must equal the totals without it."""
    without = _standings()
    with_note = _standings(caveat='anything at all')
    numbers = lambda rows: [c for r in rows for c in r
                            if isinstance(c, (int, float))]
    assert numbers(without) == numbers(with_note)


# --------------------------------------------------------------------------
# The slot section says what it measures (MLB-243 ruling, 2026-08-14)
# --------------------------------------------------------------------------

_SLOT_SPECS = [
    {'section': 'Production by Actual Lineup Slot', 'label': 'C (as started)',
     'grain': 'player', 'stat_name': 'LINEUP_SLOT_POINTS__C__1',
     'direction': 'most'},
]

_SLOT_RECORD = [{
    'entity_grain': 'player', 'stat_name': 'LINEUP_SLOT_POINTS__C__1',
    'record_direction': 'most', 'rank': 1, 'season_year': 2026,
    'matchup_period': 1, 'team_id': 1, 'team_name': 'Harbor Otters',
    'team_abbrev': 'HAR', 'owner_name': 'Owner unavailable',
    'player_id': 9001, 'player_name': 'Rowan Pike',
    'display_name': 'Rowan Pike', 'stat_value': 292.0,
}]


def _slot_section(season_long):
    import almanac_data
    return almanac_logic.build_records_tab_rows(
        all_time_records=_SLOT_RECORD, current_season_records=_SLOT_RECORD,
        record_specs=[dict(s, section=(
            almanac_data.LINEUP_SLOT_SECTION_SEASON_LONG if season_long
            else almanac_data.LINEUP_SLOT_SECTION),
            label=almanac_data._slot_record_label('C', season_long))
            for s in _SLOT_SPECS],
        schedule_lookup={}, season_long=season_long)


def test_the_points_slot_section_is_renamed():
    import almanac_data
    flat = _cells(_slot_section(True))
    assert almanac_data.LINEUP_SLOT_SECTION_SEASON_LONG in flat
    assert almanac_data.LINEUP_SLOT_SECTION not in flat


def test_the_points_slot_section_carries_the_contrast_caption():
    """A reader must meet the qualification before the first name."""
    rows = _slot_section(True)
    titles = [i for i, r in enumerate(rows)
              if r and r[0] == 'Production by Actual Lineup Slot']
    assert titles, 'the renamed section heading is missing'
    caption_row = rows[titles[0] + 1]
    caption = str(caption_row[0])
    assert 'while a player was started' in caption
    assert 'position-eligible' in caption
    assert 'may differ' in caption
    # It precedes the column header, so it cannot be scrolled past.
    assert rows[titles[0] + 2][0] == 'Record'


def test_a_slot_row_is_not_labelled_as_a_bare_position():
    """"C | Rowan Pike" reads as the catcher record. The heading three rows
    up does not travel with the row a reader is looking at."""
    rows = _slot_section(True)
    # The holder cell is a Baseball-Reference =HYPERLINK, so match inside it.
    holder_rows = [r for r in rows
                   if len(r) > 1 and 'Rowan Pike' in str(r[1])]
    assert holder_rows, 'the slot record holder did not render'
    assert holder_rows[0][0] == 'C (as started)'
    assert holder_rows[0][0] != 'C'


def test_the_h2h_slot_section_is_untouched():
    """Its heading and bare labels are pinned by the golden corpus."""
    import almanac_data
    rows = _slot_section(False)
    flat = _cells(rows)
    assert almanac_data.LINEUP_SLOT_SECTION in flat
    assert almanac_data.LINEUP_SLOT_SECTION_SEASON_LONG not in flat
    assert 'as started' not in ' '.join(flat)
    # The holder cell is a Baseball-Reference =HYPERLINK, so match inside it.
    holder_rows = [r for r in rows
                   if len(r) > 1 and 'Rowan Pike' in str(r[1])]
    assert holder_rows[0][0] == 'C'


def test_the_golden_corpus_still_pins_the_h2h_heading():
    """If this fixture ever stops containing the old heading, the H2H book
    moved and the scoping argument above no longer holds."""
    from pathlib import Path
    fixture = (Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' /
               'almanac_v1_1_0' / 'Records.tsv')
    if not fixture.exists():
        pytest.skip('private golden corpus not present')
    assert 'Lineup Slot Records' in fixture.read_text(encoding='utf-8')


def test_both_slot_headings_keep_the_one_decimal_value_rule():
    """The write layer keys number formatting off the section name, so a
    rename must not silently drop the points formatting."""
    import almanac_data
    import almanac_write
    assert almanac_data.LINEUP_SLOT_SECTION in almanac_write._ONE_DECIMAL_SECTIONS
    assert (almanac_data.LINEUP_SLOT_SECTION_SEASON_LONG
            in almanac_write._ONE_DECIMAL_SECTIONS)


def test_the_ruling_is_recorded_for_2_0():
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[1] / 'docs' / 'decisions' /
           'POSITION_ELIGIBLE_LENS.md')
    assert doc.exists(), 'the 2.0 lens ruling was not written down'
    text = doc.read_text(encoding='utf-8')
    assert 'required for 2.0' in text
    assert 'fct_player_position_pts' in text


# --------------------------------------------------------------------------
# Column widths: a caption must not size the column it sits in
# --------------------------------------------------------------------------

def _standings_shape():
    """The real Advanced Standings shape: three prose rows above a table."""
    return _standings(
        caveat=('Heads up: this league drafted on July 31, 2026 -- 128 days '
                'after the 2026 season opened on March 25. Totals on every '
                'tab include MLB production from before the league existed, '
                'credited to whoever first rostered each player.'),
    )


def test_a_long_caption_does_not_widen_the_column_it_sits_in():
    """THE WRECKED TAB. autoResizeDimensions sized column A to fit a
    300-character sentence, so the whole table sat off the right edge of
    the screen -- every value in the correct cell and the sheet unreadable.
    """
    import almanac_write

    rows = _standings_shape()
    width = max(len(r) for r in rows)
    widths = almanac_write._plain_tab_column_widths(rows, width)

    longest_prose = max(len(str(r[0])) for r in rows
                        if len(r) == 1 and str(r[0]).strip())
    assert longest_prose > 200, 'fixture lost its long caption'
    assert widths[0] <= almanac_write._PLAIN_MAX_PX
    assert widths[0] < longest_prose * 2, (
        f'column A ({widths[0]}px) is still being sized by the caption'
    )


def test_no_column_exceeds_the_ceiling_and_none_collapses():
    import almanac_write

    rows = _standings_shape()
    width = max(len(r) for r in rows)
    for px in almanac_write._plain_tab_column_widths(rows, width):
        assert almanac_write._PLAIN_MIN_PX <= px <= almanac_write._PLAIN_MAX_PX


def test_the_team_column_is_sized_by_the_longest_team_name():
    """Prose is excluded; real table content still drives the width."""
    import almanac_write

    rows = _standings_shape()
    width = max(len(r) for r in rows)
    widths = almanac_write._plain_tab_column_widths(rows, width)
    # Table A identifies teams by ABBREV, so that is what sizes column B.
    longest = max(len(r['team_abbrev']) for r in SEASON_TOTALS)
    assert widths[1] >= longest * 7, (
        'the Team column is too narrow to show a team abbreviation'
    )


def test_an_all_prose_tab_still_gets_usable_widths():
    import almanac_write

    rows = [['Advanced Standings: 2026'], ['a very long explanatory line ' * 12]]
    widths = almanac_write._plain_tab_column_widths(rows, 1)
    assert widths == [almanac_write._PLAIN_MIN_PX]


# --------------------------------------------------------------------------
# Identity precedence and owner honesty (transform-layer contracts)
# --------------------------------------------------------------------------

from pathlib import Path                                       # noqa: E402

import yaml                                                    # noqa: E402

_MODELS = Path(__file__).resolve().parents[1] / 'dbt_league' / 'models'
_PROJECT = Path(__file__).resolve().parents[1] / 'dbt_league' / 'dbt_project.yml'


def test_a_configured_canonical_name_still_wins_over_the_observed_one():
    """Repairing the OBSERVED name must not demote the CONFIGURED one.

    dim_franchise resolves the human's lineage override ahead of whatever
    the platform reports, and that order is what lets a maintainer rename a
    franchise for good. The identity repair happens upstream, in the
    observed branch, precisely so this coalesce is untouched.
    """
    sql = (_MODELS / 'marts' / 'core' / 'dim_franchise.sql').read_text(
        encoding='utf-8')
    assert 'coalesce(r.override_name, anchor.franchise_name)' in sql
    assert 'coalesce(r.override_abbrev, anchor.abbrev)' in sql


def test_a_withheld_owner_name_is_labelled_not_invented():
    """ESPN serves the stable member GUID and NULLs every name field for a
    public league. That is a privacy limitation, not a gap to fill."""
    project = yaml.safe_load(_PROJECT.read_text(encoding='utf-8'))
    label = project['vars']['owner_unavailable_label']
    assert label == 'Owner unavailable'

    sql = (_MODELS / 'marts' / 'core' / 'dim_owner.sql').read_text(
        encoding='utf-8')
    assert "var(\"owner_unavailable_label\")" in sql
    # The fallback is LAST in the coalesce: any real name the platform or
    # the nickname seed supplies still wins.
    assert sql.index('n.preferred_name') < sql.index('owner_unavailable_label')
    assert sql.index('o.seen_name') < sql.index('owner_unavailable_label')


def test_the_owner_fallback_is_a_label_rather_than_a_person():
    """"Unknown owner" read as a failure to identify somebody. The label
    must not look like a name, and must never be derived from one."""
    project = yaml.safe_load(_PROJECT.read_text(encoding='utf-8'))
    label = project['vars']['owner_unavailable_label']
    assert 'unknown' not in label.lower()
    assert label.lower().startswith('owner')


def test_the_identity_repair_is_scoped_to_the_season_points_path():
    """It must not re-key every league's franchise names.

    The H2H box scores already carry real labels, and where the two feeds
    disagree the difference is undiagnosed (the 2025 team-7 drift). Doing
    this in the shared registry would move published output for reasons
    nobody has established, so it lives in the model that produces rows
    ONLY for season-long points leagues.
    """
    registry = (_MODELS / 'intermediate' / 'int_franchise_registry.sql'
                ).read_text(encoding='utf-8')
    assert "ref('stg_team_standings')" not in registry, (
        'the identity repair leaked into the shared franchise registry, '
        'which would re-key every league'
    )
    roster = (_MODELS / 'staging' / 'stg_box_scores__team_rosters.sql'
              ).read_text(encoding='utf-8')
    assert "ref('stg_team_standings')" in roster


# --------------------------------------------------------------------------
# The live write path (MLB-243)
# --------------------------------------------------------------------------

def test_the_write_path_routes_each_tab_to_its_own_writer(monkeypatch):
    """A NameError here is invisible to every other test in this file --
    nothing else executes write_points_almanac, and it only runs against
    real Sheets. It shipped one, so it gets a stub-level smoke test."""
    import almanac_data
    import almanac_write

    calls = {'plain': [], 'standings': [], 'home': [], 'sorted': []}

    class _WS:
        def __init__(self, title):
            self.title, self.id = title, abs(hash(title)) % 1000

    monkeypatch.setattr(points_almanac, '_build', lambda: (
        [('Home', [['H']]), ('Records', [['R']]),
         ('Advanced Standings', [['A']], [{'range': 'A1:A1'}]),
         ('Draft Recap', [['D']]), ('HAR', [['T']])],
        {'season_year': 2026}, ['HAR'], lambda targets: [['HOME', targets]]))
    monkeypatch.setattr(almanac_data, 'get_team_week_stat_specs',
                        lambda: STAT_SPECS)
    monkeypatch.setattr(almanac_write, '_replace_plain_tab',
                        lambda sp, t, r: calls['plain'].append(t) or _WS(t))
    monkeypatch.setattr(almanac_write, '_replace_records_tab',
                        lambda sp, r: _WS('Records'))
    monkeypatch.setattr(almanac_write, '_replace_draft_tab',
                        lambda sp, r, color_grid=None: _WS('Draft Recap'))
    monkeypatch.setattr(almanac_write, '_replace_team_tab',
                        lambda sp, t, r: _WS(t))
    import cbs_almanac_sheets
    monkeypatch.setattr(
        cbs_almanac_sheets, '_write_tab',
        lambda sp, t, r, f, **kw: calls['standings'].append((t, f))
        or _WS(t))
    monkeypatch.setattr(almanac_write, '_replace_home_tab',
                        lambda sp, r: calls['home'].append(r))
    monkeypatch.setattr(almanac_write, '_sort_almanac_tabs',
                        lambda sp, order: calls['sorted'].append(order))

    class _Client:
        def open_by_key(self, key):
            return object()

    points_almanac.write_points_almanac('SHEET', client=_Client())

    assert calls['standings'] == [('Advanced Standings', [{'range': 'A1:A1'}])], (
        "Advanced Standings did not reach the shared presenter's writer "
        "with its format specs"
    )
    assert 'Advanced Standings' not in calls['plain'], (
        'Advanced Standings fell back to the unstyled writer'
    )
    assert calls['home'], 'Home was never written'


# --------------------------------------------------------------------------
# The season-points layout contract (MLB-243 corrective pass)
# --------------------------------------------------------------------------

def _presenter_tab(periods=142, teams=10, compact=True):
    """Advanced Standings through the SHARED season-points presenter, in the
    shape a first-year ESPN league produces."""
    import cbs_almanac_sheets

    ids = list(range(1, teams + 1))
    names = {t: "Club %02d" % t for t in ids}
    arc = [{"team_id": t, "team_name": names[t], "period": p,
            "standings_rank": ((t + p) % teams) + 1,
            "is_latest_period": p == periods}
           for p in range(1, periods + 1) for t in ids]
    fmap = {t: {"canonical_id": t, "abbrev": "C%02d" % t} for t in ids}
    slots = [{"team_id": t, "season_year": 2026, "lineup_slot": s,
              "slot_pts": 100.0 + t}
             for t in ids for s in ("C", "P")]
    return cbs_almanac_sheets.build_standings_rows(
        {"season_year": 2026, "latest_period": periods, "first_season": 2026},
        arc,
        [{"season_year": 2026, "franchise_id": t, "team_name": names[t],
          "standings_rank": i + 1, "is_champion": False}
         for i, t in enumerate(ids)],
        [{"team_id": t, "team_name": names[t]} for t in ids],
        slot_rows=slots,
        alltime_slot_rows=slots,
        alltime_pitching_rows=[{"team_id": t, "p_pts": 500.0} for t in ids],
        season_days=[{"season_year": 2026, "days": periods}],
        franchise_map=fmap,
        period_label="Scoring Day",
        compact_chart=compact,
    )


def _hidden_rows(formats):
    hidden = set()
    for spec in formats:
        if "hide_rows" in spec:
            start, end = spec["hide_rows"]
            hidden.update(range(start, end))
    return hidden


def _visible(rows, formats):
    hidden = _hidden_rows(formats)
    return [i for i in range(len(rows)) if i not in hidden]


def test_142_scoring_days_do_not_create_142_visible_rows():
    """THE CANYON. The helper block is one row per period and was hidden
    only by COLUMN, so 142 scoring days opened a 142-row blank gap between
    the chart and the next section."""
    rows, formats = _presenter_tab(periods=142)
    visible = _visible(rows, formats)
    assert len(rows) - len(visible) >= 142, "per-day helper rows are not hidden"
    assert len(visible) < 140, (
        "%d visible rows -- the scoring-day helper still consumes display "
        "rows" % len(visible))


def test_visible_sections_stay_compactly_adjacent():
    """No more than two blank display rows between visible sections."""
    rows, formats = _presenter_tab(periods=142)
    visible = set(_visible(rows, formats))
    labels = [i for i in sorted(visible)
              if rows[i] and str(rows[i][0]).isupper()
              and len(str(rows[i][0])) > 4]
    assert labels, "no section banners found"
    # The chart's reserved canvas is deliberately blank -- the chart image
    # floats over it -- so it is a section, not a gap between sections.
    labels = [i for i in labels if not str(rows[i][0]).startswith("RANK BY")]
    for a, b in zip(labels, labels[1:]):
        blanks = sum(1 for i in range(a + 1, b)
                     if i in visible
                     and not any(str(c).strip() for c in rows[i]))
        assert blanks <= 2, (
            "%d blank display rows between %r and %r"
            % (blanks, rows[a][0], rows[b][0]))


def test_the_chart_area_has_a_fixed_compact_height():
    """It must not grow with the number of scoring days."""
    short, _ = _presenter_tab(periods=20)
    long_, _ = _presenter_tab(periods=142)

    def anchor(rows):
        return next(i for i, r in enumerate(rows)
                    if r and str(r[0]).startswith("SEASON FINISHES"))

    assert anchor(short) == anchor(long_), (
        "the first section after the chart moved when the day count grew, "
        "so the chart area is not a fixed height")
    assert anchor(long_) < 40, "the chart area is not compact"


def test_no_visible_points_chart_label_says_week_or_matchup():
    rows, _ = _presenter_tab(periods=142)
    flat = " ".join(str(c) for r in rows for c in r).lower()
    for banned in ("rank by period", "by week", "weekly", "matchup"):
        assert banned not in flat, "%r survived into the points tab" % banned
    assert "rank by scoring day" in flat


def test_team_and_franchise_spines_use_canonical_names():
    """Never an abbreviation, never an owner."""
    rows, _ = _presenter_tab(periods=20, teams=4)
    seen = False
    for i, row in enumerate(rows):
        if row and str(row[0]) in ("Team", "Franchise"):
            for follower in rows[i + 1:i + 5]:
                if not follower or not str(follower[0]).strip():
                    break
                label = str(follower[0])
                if label.startswith("Club "):
                    seen = True
                assert not label.startswith("C0"), (
                    "an abbreviation reached a %s spine: %r" % (row[0], label))
    assert seen, "no canonical team name found in any spine"


def test_first_year_current_and_alltime_twins_are_both_populated():
    """All-time equals the current season in year one, and printing both
    halves is truthful -- dropping one leaves the reader guessing."""
    rows, _ = _presenter_tab(periods=20, teams=4)
    flat = " ".join(str(c) for r in rows for c in r)
    assert "Totals by Deployed Slot, Current Season" in flat
    assert "Pace per Standard Season, All-Time" in flat


def test_the_mature_book_layout_is_unchanged_by_the_compact_option():
    """compact_chart defaults off, so no established book's anchors move."""
    _rows, formats = _presenter_tab(periods=20, teams=4, compact=False)
    assert not any("hide_rows" in f for f in formats), (
        "the default path grew a hidden row group, which would move an "
        "existing book")


def test_an_inaccessible_owner_falls_back_to_the_canonical_team_name():
    rows = [{"team_name": "Harbor Otters", "owner_display": "Owner unavailable"},
            {"team_name": "Granite Owls", "owner_display": "Unknown owner"},
            {"team_name": "Gale Ridge", "owner_display": "A Real Person"}]
    out = espn_points_data.with_owner_fallback(rows)
    assert out[0]["owner_display"] == "Harbor Otters"
    assert out[1]["owner_display"] == "Granite Owls"
    assert out[2]["owner_display"] == "A Real Person", (
        "a real owner name was overwritten")


def test_the_owner_fallback_leaves_the_team_name_alone():
    rows = [{"team_name": "Harbor Otters", "owner_display": "Owner unavailable"}]
    out = espn_points_data.with_owner_fallback(rows)
    assert out[0]["team_name"] == "Harbor Otters"


def test_the_draft_recap_reuses_the_established_color_grading():
    """A visible v1.9 regression: the points path called the same writer as
    the H2H book but without its colour grid."""
    import almanac_logic
    board = [{"overall_pick": i, "round_num": 1, "round_pick": i,
              "team_id": 1, "season_points": 100.0 * i, "value_delta": i,
              "player_name": "P%d" % i} for i in range(1, 5)]
    assert points_almanac._draft_color_grid(board) == \
        almanac_logic.build_draft_board_color_grid(board), (
        "the points path invented its own scale instead of reusing the "
        "established one")


# --------------------------------------------------------------------------
# Visual-QA regressions (MLB-243 corrective pass, round 2)
# --------------------------------------------------------------------------

def _neutral_tab(periods=142, teams=10, finishes=None, duplicate_abbrev=False):
    """The ESPN season-points tab: neutral copy, scoring days, compact."""
    import cbs_almanac_sheets

    ids = list(range(1, teams + 1))
    names = {t: "Club %02d" % t for t in ids}
    arc = [{"team_id": t, "team_name": names[t], "period": p,
            "standings_rank": ((t + p) % teams) + 1,
            "is_latest_period": p == periods}
           for p in range(1, periods + 1) for t in ids]
    abbrev = (lambda t: "DUP" if t <= 2 else "C%02d" % t) if duplicate_abbrev \
        else (lambda t: "C%02d" % t)
    fmap = {t: {"canonical_id": t, "abbrev": abbrev(t)} for t in ids}
    slots = [{"team_id": t, "season_year": 2026, "lineup_slot": s,
              "slot_pts": 100.0 + t} for t in ids for s in ("C", "P")]
    return cbs_almanac_sheets.build_standings_rows(
        {"season_year": 2026, "latest_period": periods, "first_season": 2026},
        arc,
        finishes if finishes is not None else [],
        [{"team_id": t, "team_name": names[t]} for t in ids],
        slot_rows=slots, alltime_slot_rows=slots,
        alltime_pitching_rows=[{"team_id": t, "p_pts": 500.0} for t in ids],
        season_days=[{"season_year": 2026, "days": periods}],
        affinity_rows=[{"team_id": t, "mlb_team_id": 1, "mlb_team_name": "NYY",
                        "season_wt": 10.0, "alltime_wt": 10.0} for t in ids],
        franchise_map=fmap,
        period_label="Scoring Day",
        compact_chart=True,
        copy=cbs_almanac_sheets.NEUTRAL_COPY,
    )


def test_an_unfinished_first_year_has_exactly_one_current_season_column():
    """THE DUPLICATE. The presenter appends the season in flight itself, so
    an adapter that also returned it produced two 2026 columns -- and the
    in-flight year, sitting in the finished set, collected a medal, a
    division title and a closed-season average it had not earned."""
    rows, _ = _neutral_tab(periods=20, teams=4, finishes=[])
    header = next(r for r in rows if r and str(r[0]) == "Franchise")
    assert header.count("2026") == 1, (
        "expected one 2026 column, got %d: %r" % (header.count("2026"), header))


def test_an_unfinished_first_year_carries_no_closed_season_decoration():
    rows, _ = _neutral_tab(periods=20, teams=4, finishes=[])
    header = next(i for i, r in enumerate(rows)
                  if r and str(r[0]) == "Franchise")
    body = rows[header + 1:header + 5]
    flat = " ".join(str(c) for r in body for c in r)
    for glyph in ("\U0001F3C6", "\U0001F948", "\U0001F949"):
        assert glyph not in flat, "a medal was awarded in an unfinished season"
    for row in body:
        # Titles / Div / Avg are columns 1..3 and must all be empty.
        assert not any(str(c).strip() for c in row[1:4]), (
            "closed-season honours were credited to an in-flight year: %r"
            % (row[:5],))


def test_the_medal_legend_is_absent_when_nothing_has_closed():
    rows, _ = _neutral_tab(periods=20, teams=4, finishes=[])
    flat = " ".join(str(c) for r in rows for c in r)
    assert "Season Champion" not in flat, (
        "a legend explains medals no season can have earned")
    assert "Division Champion" not in flat, (
        "division language with no measured divisions")


def test_the_espn_tab_carries_no_cbs_only_claims():
    """The layout is shared; its sentences carried one league's history."""
    rows, _ = _neutral_tab(periods=142)
    flat = " ".join(str(c) for r in rows for c in r)
    for claim in ("2002 ran 15", "2020 ran 12", "short seasons (2020)",
                  "2001-25", "2001\u201325", "2004-2020",
                  "matching the Records page"):
        assert claim not in flat, "CBS-only claim on the ESPN tab: %r" % claim


def test_the_cbs_caller_keeps_its_own_history():
    """Parameterising the copy must not delete it from the book it
    describes."""
    import cbs_almanac_sheets
    assert "2002 ran 15" in cbs_almanac_sheets.CBS_COPY["finishes_history"]
    assert "2004-2020" in cbs_almanac_sheets.CBS_COPY["affinity_provenance"]
    assert "2001-25" in cbs_almanac_sheets.CBS_COPY["slot_capture_note"]
    assert cbs_almanac_sheets.CBS_COPY["has_divisions"] is True
    assert cbs_almanac_sheets.NEUTRAL_COPY["has_divisions"] is False


def test_the_chart_bottom_axis_says_scoring_day():
    _rows, formats = _neutral_tab(periods=142)
    chart = next(f["chart"] for f in formats if "chart" in f)
    assert chart["domain_label"] == "Scoring Day"
    assert "scoring day" in chart["title"].lower()


def test_the_default_chart_axis_is_still_period():
    import cbs_almanac_sheets
    ids = [1, 2]
    arc = [{"team_id": t, "team_name": "Club %d" % t, "period": p,
            "standings_rank": t, "is_latest_period": p == 5}
           for p in range(1, 6) for t in ids]
    _rows, formats = cbs_almanac_sheets.build_standings_rows(
        {"season_year": 2026, "latest_period": 5, "first_season": 2026},
        arc, [], [{"team_id": t, "team_name": "Club %d" % t} for t in ids],
        franchise_map={t: {"canonical_id": t, "abbrev": "C%d" % t}
                       for t in ids})
    chart = next(f["chart"] for f in formats if "chart" in f)
    assert chart["domain_label"] == "Period"


def test_duplicate_abbreviations_get_distinct_chart_controls():
    """Two identical checkboxes and two identical legend entries give a
    reader no way to tell which team is which."""
    rows, formats = _neutral_tab(periods=20, teams=4, duplicate_abbrev=True)
    controls = next(r for r in rows if r and str(r[0]) == "Chart teams:")
    labels = [str(c) for c in controls[1:-1]]        # drop 'Chart teams:'/'ALL'
    assert len(labels) == len(set(labels)), (
        "duplicate chart controls: %r" % labels)
    assert "DUP 1" in labels and "DUP 2" in labels, labels
    assert "DUP" not in labels, "the bare duplicate abbrev survived"
    # The same labels must reach the hidden helper header, which is what
    # names the chart series.
    helper = [r for r in rows if len(r) > 36 and str(r[36]) == "Scoring Day"]
    assert helper, "no helper header found"
    assert "DUP 1" in [str(c) for c in helper[0]]


def test_a_unique_abbreviation_is_left_alone():
    rows, _ = _neutral_tab(periods=20, teams=4, duplicate_abbrev=False)
    controls = next(r for r in rows if r and str(r[0]) == "Chart teams:")
    labels = [str(c) for c in controls[1:-1]]
    assert "C03" in labels, labels
    assert not any(l.startswith("C03 ") for l in labels), (
        "a unique abbrev was needlessly suffixed")
