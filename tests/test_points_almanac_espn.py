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

THE PURITY IS ENFORCED, NOT ASSERTED (2026-08-16). The record-book
builders default `display_map` to `stat_catalog.get_display_map()` and
always read `get_rate_qualifiers()`, so ten tests in this file were
opening a real Snowflake connection and passing only because a
maintainer's `.env` happened to answer. Testing the release ZIP as a
stranger would -- extracted, no `.env` -- killed them in the connector.
`_no_warehouse` below applies the shared `stub_stat_catalog` to every
test here. Scoped to this module rather than made autouse in conftest,
so warehouse-marked tests keep exercising the real `dim_stat`.
"""
from __future__ import annotations

from datetime import date

import pytest

import almanac_logic
import espn_points_data
import points_almanac
import sheets_workbook
from almanac_render import TEAM_WEEKS_TAB


@pytest.fixture(autouse=True)
def _no_warehouse(stub_stat_catalog, stub_slot_catalog, monkeypatch):
    """Every test in this module reads its vocabulary from a literal.

    Autouse at MODULE scope: the builders reach the warehouse through
    their own DEFAULTS, so opting in per test is exactly the thing that
    was forgotten ten times over.

    THREE defaults, and two of them share one shape -- a falsy argument
    the caller MEANT as "there is none", thrown away by `or`:

        schedule_lookup = schedule_lookup or records.load_schedule_lookup()
        record_specs    = record_specs    or [... get_scored_record_specs() ...]

    Tests here pass `schedule_lookup={}` and `record_specs=[]` meaning
    exactly that, and both are falsy, so `or` discarded them and fetched
    the real thing. The intent was right and the spelling could not
    express it. `stub_stat_catalog` closes the third,
    `display_map=stat_catalog.get_display_map()`.

    `stub_slot_catalog` rides along for the same reason: the points
    caller hands the presenter `slot_catalog.canonical_lineup_slot`, which
    reads the slot_classification seed. A test that patched only the stat
    catalog would still connect through the slot one.

    Neutralising the loaders HERE keeps each test's intent without
    touching a production default that non-test callers genuinely want.
    The spec builders are patched on `almanac_logic`, which imported them
    BY NAME, rather than on `almanac_data` -- so a test that calls
    `almanac_data.get_lineup_slot_record_specs` directly still exercises
    the real one.
    """
    import almanac_logic as _logic
    import records
    monkeypatch.setattr(records, 'load_schedule_lookup', dict)
    monkeypatch.setattr(_logic, 'get_scored_record_specs', list)
    monkeypatch.setattr(_logic, 'get_lineup_slot_record_specs',
                        lambda season_long=False: [])


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
    monkeypatch.setattr(espn_points_data, 'query_for_presentation',
                        lambda sql, params=None: [{'has_transaction_log': False}])
    monkeypatch.setattr(espn_points_data, 'league_predicate',
                        lambda alias=None: "league_key = 'x'")
    assert espn_points_data.acquisition_channels(2026) is None


def test_the_adapter_returns_channels_when_coverage_is_proven(monkeypatch):
    import almanac_data
    monkeypatch.setattr(espn_points_data, 'query_for_presentation',
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

    # THE PRESENTER SURFACE (MLB-243). `_build` stopped calling
    # standings_rows / rank_arc / season_finishes when the tab moved to
    # the shared season-points presenter, and this fixture kept stubbing
    # the retired names -- so every `assembled` test has been reaching the
    # real warehouse ever since, and passing only where credentials
    # happened to answer. Stub what it calls TODAY.
    monkeypatch.setattr(espn_points_data, 'presenter_context',
                        lambda context: {'season_year': 2026,
                                         'latest_period': 142,
                                         'first_season': 2026})
    monkeypatch.setattr(espn_points_data, 'dense_rank_arc', lambda season: [
        {'team_id': t['team_id'], 'team_name': t['team_name'], 'period': 1,
         'standings_rank': i + 1, 'is_latest_period': True}
        for i, t in enumerate(SEASON_TOTALS)])
    monkeypatch.setattr(espn_points_data, 'presenter_finishes',
                        lambda season: [])
    monkeypatch.setattr(espn_points_data, 'presenter_active_franchises',
                        lambda season: [{'team_id': t['team_id'],
                                         'team_name': t['team_name']}
                                        for t in SEASON_TOTALS])
    monkeypatch.setattr(
        espn_points_data, 'presenter_slot_rows',
        lambda season_year=None: [dict(r, season_year=2026) for r in SLOT_ROWS])
    monkeypatch.setattr(espn_points_data, 'presenter_slot_columns',
                        lambda: ['C', 'P'])
    monkeypatch.setattr(
        espn_points_data, 'presenter_alltime_pitching',
        lambda season_year=None: [{'team_id': t['team_id'], 'p_pts': 500.0}
                                  for t in SEASON_TOTALS])
    monkeypatch.setattr(espn_points_data, 'presenter_season_days',
                        lambda: [{'season_year': 2026, 'days': 142}])
    # _DETAILED is defined further down; module-level names resolve when
    # the lambda RUNS, so the forward reference is fine and keeps one set
    # of synthetic stats in the file rather than two.
    monkeypatch.setattr(espn_points_data, 'presenter_detailed_alltime',
                        lambda: list(_DETAILED))
    monkeypatch.setattr(espn_points_data, 'presenter_affinity',
                        lambda season: [])
    monkeypatch.setattr(espn_points_data, 'franchise_map', lambda: {
        t['team_id']: {'canonical_id': t['team_id'],
                       'abbrev': t['team_abbrev'],
                       'name': t['team_name']} for t in SEASON_TOTALS})

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
    monkeypatch.setattr(espn_points_data, 'query_for_presentation',
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
    monkeypatch.setattr(espn_points_data, 'query_for_presentation',
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
# The slot section keeps its NAME and says what it measures ONCE
# (MLB-243 ruling 2026-08-14, corrected 2026-08-15).
#
# The first pass renamed the section to "Production by Actual Lineup Slot"
# and suffixed every row label with "(as started)". That overreached: one
# section of a shared record book should not be titled two different ways
# depending on which league is reading it, and eighteen parenthetical row
# labels shout a caveat the reader needs once. The distinction is real, so
# the caption survives -- and is now painted in the house explainer style
# rather than left in the tab's default body format.
# --------------------------------------------------------------------------

_SLOT_SPECS = [
    {'section': 'Lineup Slot Records', 'label': 'C',
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
        record_specs=[dict(s, section=almanac_data.LINEUP_SLOT_SECTION,
                           label='C')
                      for s in _SLOT_SPECS],
        schedule_lookup={}, season_long=season_long)


def test_both_books_call_the_slot_section_by_its_normal_name():
    import almanac_data
    for season_long in (True, False):
        flat = _cells(_slot_section(season_long))
        assert almanac_data.LINEUP_SLOT_SECTION in flat
        assert 'Production by Actual Lineup Slot' not in flat


def test_no_slot_row_carries_an_as_started_suffix():
    """The caption states the lens once; the rows are just slots."""
    for season_long in (True, False):
        rows = _slot_section(season_long)
        # Row LABELS only -- the caption legitimately says "was started".
        assert not [r for r in rows
                    if r and isinstance(r[0], str) and '(as started)' in r[0]]
        # The holder cell is a Baseball-Reference =HYPERLINK; match inside it.
        holder_rows = [r for r in rows
                       if len(r) > 1 and 'Rowan Pike' in str(r[1])]
        assert holder_rows, 'the slot record holder did not render'
        assert holder_rows[0][0] == 'C'


def test_the_spec_builder_labels_slots_bare_in_both_books(monkeypatch):
    """The suffix is gone at the SOURCE, not filtered downstream."""
    import almanac_data
    monkeypatch.setattr(almanac_data, 'query_for_presentation', lambda *a, **k: [
        {'lineup_slot': 'C', 'slots_to_fill': 1},
    ])
    for season_long in (True, False):
        specs = almanac_data.get_lineup_slot_record_specs(
            season_long=season_long)
        assert [s['label'] for s in specs] == ['C']
        assert {s['section'] for s in specs} == {
            almanac_data.LINEUP_SLOT_SECTION}


def test_the_points_slot_section_carries_the_contrast_caption():
    """A reader must meet the qualification before the first name."""
    import almanac_data
    rows = _slot_section(True)
    titles = [i for i, r in enumerate(rows)
              if r and r[0] == almanac_data.LINEUP_SLOT_SECTION]
    assert titles, 'the slot section heading is missing'
    caption = str(rows[titles[0] + 1][0])
    assert caption == almanac_data.LINEUP_SLOT_LENS_CAPTION
    assert 'while a player was started' in caption
    assert 'position-eligible' in caption
    assert 'may differ' in caption
    # It precedes the column header, so it cannot be scrolled past.
    assert rows[titles[0] + 2][0] == 'Record'


def test_the_caption_is_painted_in_the_house_explainer_style():
    """An unformatted caption renders exactly like a record row, which is
    the wrong thing for a sentence explaining the section."""
    import almanac_render
    import almanac_write
    rows = _slot_section(True)
    formats = almanac_write._records_caption_formats(rows)
    assert len(formats) == 1, formats
    expected = almanac_render.explainer_text_format()
    assert formats[0]['format']['textFormat'] == expected
    caption_row = int(formats[0]['range'].split(':')[0][1:])
    assert rows[caption_row - 1][0] == almanac_data_caption()


def almanac_data_caption():
    import almanac_data
    return almanac_data.LINEUP_SLOT_LENS_CAPTION


def test_the_h2h_slot_section_carries_no_caption():
    """The head-to-head record book is pinned byte for byte; the caption is
    the whole of what the points book adds."""
    import almanac_data
    rows = _slot_section(False)
    assert almanac_data.LINEUP_SLOT_LENS_CAPTION not in _cells(rows)


def test_the_golden_corpus_still_pins_the_h2h_heading():
    """If this fixture ever stops containing the old heading, the H2H book
    moved and the scoping argument above no longer holds."""
    from pathlib import Path
    fixture = (Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' /
               'almanac_v1_1_0' / 'Records.tsv')
    if not fixture.exists():
        pytest.skip('private golden corpus not present')
    assert 'Lineup Slot Records' in fixture.read_text(encoding='utf-8')


def test_the_slot_heading_keeps_the_one_decimal_value_rule():
    """The write layer keys number formatting off the section name, so the
    revert must not silently drop the points formatting."""
    import almanac_data
    import almanac_write
    assert almanac_data.LINEUP_SLOT_SECTION in almanac_write._ONE_DECIMAL_SECTIONS


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


# --------------------------------------------------------------------------
# MLB-243 CORRECTION PASS (2026-08-15)
#
# Every test below pins a defect Kyle found in the live review workbook, and
# each asserts a NUMBER or an exact label rather than the presence of a
# header -- a blank table has all the right headers.
# --------------------------------------------------------------------------

def _configured_tab(slot_columns=("C", "1B", "OF", "UTIL", "P"),
                    slot_spellings=("C", "1B", "OF", "UTIL", "P"),
                    teams=3, periods=139, acquisition=None,
                    detailed=None, prior_acquisition=None, slot_key=None):
    """The season-points tab as a FIRST-YEAR league produces it: nothing
    closed, the league's own configured slots, and its own spelling of the
    utility slot."""
    import cbs_almanac_sheets

    ids = list(range(1, teams + 1))
    names = {t: "Club %02d" % t for t in ids}
    arc = [{"team_id": t, "team_name": names[t], "period": p,
            "standings_rank": t, "is_latest_period": p == periods}
           for p in range(1, periods + 1) for t in ids]
    slots = [{"team_id": t, "season_year": 2026, "lineup_slot": s,
              "slot_pts": 100.0 * t + 10.0 * i}
             for t in ids for i, s in enumerate(slot_spellings)]
    return cbs_almanac_sheets.build_standings_rows(
        {"season_year": 2026, "latest_period": periods, "first_season": 2026},
        arc,
        [],                                   # nothing has CLOSED
        [{"team_id": t, "team_name": names[t]} for t in ids],
        slot_rows=slots, alltime_slot_rows=slots,
        alltime_pitching_rows=[{"team_id": t, "p_pts": 700.0} for t in ids],
        season_days=[{"season_year": 2026, "days": periods}],
        detailed_alltime_rows=detailed,
        acquisition_rows=acquisition,
        alltime_acquisition_rows=prior_acquisition,
        franchise_map={t: {"canonical_id": t, "abbrev": "C%02d" % t}
                       for t in ids},
        period_label="Scoring Day", compact_chart=True,
        copy=cbs_almanac_sheets.NEUTRAL_COPY,
        slot_columns=list(slot_columns),
        slot_key=slot_key,
    )


def _section_body(rows, header_first_cell, must_contain=None):
    """The header row and its data rows, up to the first blank row.

    `must_contain` disambiguates headers that share a first cell -- Season
    Finishes and Detailed Standings both start with 'Franchise'.
    """
    start = next(
        i for i, r in enumerate(rows)
        if r and str(r[0]) == header_first_cell
        and (must_contain is None or must_contain in [str(c) for c in r])
    )
    body = []
    for row in rows[start + 1:]:
        if not row or not str(row[0]).strip() or len(row) < len(rows[start]) // 2:
            break
        body.append(row)
    return rows[start], body


# ---- 3. Points by Lineup Slot -------------------------------------------

def test_the_slot_grid_columns_are_the_leagues_configured_slots():
    """A blank DH column for a league with no DH slot is a defect; an empty
    column for a slot the league DOES roster is information."""
    rows, _ = _configured_tab()
    header, _body = _section_body(rows, "Team")
    left = header[1:header.index("", 1)]
    assert left == ["C", "1B", "OF", "UTIL", "P"], left
    assert "DH" not in header, "an unrostered slot became a blank column"


def test_util_production_lands_in_the_util_column():
    """THE ALIAS. The league spells it UTIL and the layout came from a book
    that spells it U, so a whole slot's production had nowhere to land."""
    rows, _ = _configured_tab()
    header, body = _section_body(rows, "Team")
    col = header.index("UTIL")
    values = [r[col] for r in body]
    assert all(isinstance(v, (int, float)) and v > 0 for v in values), values


def test_a_u_spelled_grid_still_reads_util_rows_and_vice_versa(
        stub_slot_catalog):
    """The catalog's key is the same from either direction, so a book that
    displays U and a feed that says UTIL agree."""
    import slot_catalog
    resolver = slot_catalog.canonical_lineup_slot
    as_u, _ = _configured_tab(slot_columns=("C", "U", "P"),
                              slot_spellings=("C", "UTIL", "P"),
                              slot_key=resolver)
    as_util, _ = _configured_tab(slot_columns=("C", "UTIL", "P"),
                                 slot_spellings=("C", "U", "P"),
                                 slot_key=resolver)
    _h1, b1 = _section_body(as_u, "Team")
    _h2, b2 = _section_body(as_util, "Team")
    assert [r[2] for r in b1] == [r[2] for r in b2]
    assert all(isinstance(r[2], (int, float)) and r[2] > 0 for r in b1)


def test_the_first_year_all_time_slot_half_equals_the_current_season():
    """With one season on file the twin is redundant, and printing it blank
    made the reader guess which half they were looking at."""
    rows, _ = _configured_tab()
    header, body = _section_body(rows, "Team")
    gap = header.index("", 1)
    for row in body:
        left = row[1:gap]
        right = row[gap + 1:gap + 1 + len(left)]
        assert all(isinstance(v, (int, float)) for v in right), right
        # The P column paces on membership rather than capture, and this
        # fixture gives it its own number; every other slot must match its
        # current-season twin exactly.
        assert left[:-1] == right[:-1], (left, right)


def test_the_first_year_clock_is_named_rather_than_printed_as_none():
    rows, _ = _configured_tab(periods=139)
    flat = " ".join(str(c) for r in rows for c in r)
    assert "None-gameplay-day" not in flat
    assert "None gameplay days" not in flat
    assert "139 gameplay days" in flat


# ---- 4. Detailed Standings ----------------------------------------------

_DETAILED = [
    {"team_id": t, "h": 1000.0 + t, "doubles": 200.0 + t, "triples": 20.0,
     "hr": 150.0 + t, "xbh": 370.0, "tb": 1700.0, "r": 550.0, "rbi": 500.0,
     "sb": 90.0, "b_bb": 400.0, "w": 50.0, "qs": 60.0, "k": 800.0,
     "sv": 25.0, "hld": 4.0, "cg": 1.0, "outs": 2100.0,
     "hit_pts": 2400.0 + t, "pit_pts": 1900.0 + t, "total_pts": 4300.0 + 2 * t}
    for t in (1, 2, 3)
]


def test_detailed_standings_is_not_a_table_of_blanks():
    """THE RELEASE BLOCKER. Every cell was empty, because a first-year
    league had no closed season to build a standard-season clock from."""
    rows, _ = _configured_tab(detailed=_DETAILED)
    header, body = _section_body(rows, "Franchise", must_contain="Hit Pts")
    assert len(body) == 3, body
    labelled = len([h for h in header[1:] if str(h).strip()])
    for row in body:
        numeric = [c for c in row[1:] if isinstance(c, (int, float))]
        assert len(numeric) == labelled, (numeric, header)


def test_detailed_standings_reconciles_with_the_shared_mart():
    """Numbers, not headers: in a first year the pace weight is 1.0, so each
    cell is the mart's own figure."""
    rows, _ = _configured_tab(detailed=_DETAILED)
    header, body = _section_body(rows, "Franchise", must_contain="Hit Pts")
    by_name = {str(r[0]): r for r in body}
    for source in _DETAILED:
        row = by_name["Club %02d" % source["team_id"]]
        assert row[header.index("Total")] == pytest.approx(
            source["total_pts"], abs=0.05)
        assert row[header.index("H")] == pytest.approx(source["h"], abs=0.05)
        assert row[header.index("HR")] == pytest.approx(source["hr"], abs=0.05)
        # OUTS renders as innings pitched.
        assert row[header.index("IP")] == pytest.approx(
            source["outs"] / 3.0, abs=0.05)


def test_detailed_standings_says_the_all_time_table_is_this_season():
    rows, _ = _configured_tab(detailed=_DETAILED)
    flat = " ".join(str(c) for r in rows for c in r)
    assert ("one season on file, so these all-time figures are the current"
            in flat)


# ---- 5. Production by acquisition channel -------------------------------

_ACQ = [
    # The ESPN mart's vocabulary: keeper + draft, NOT `opening`.
    {"team_id": t, "team_name": "Club %02d" % t,
     "keeper_active_pts": 0.0, "draft_active_pts": 3494.0 + t,
     "fa_add_active_pts": 88.0, "trade_active_pts": 0.0,
     "acquired_active_pts": 3582.0 + t,
     "dropped_active_pts": 0.0, "traded_away_active_pts": 0.0,
     "lost_active_pts": 0.0,
     "keeper_rostered_pts": 0.0, "draft_rostered_pts": 4162.0 + t,
     "fa_add_rostered_pts": 88.0, "trade_rostered_pts": 0.0,
     "acquired_rostered_pts": 4250.0 + t,
     "dropped_rostered_pts": 3.0, "traded_away_rostered_pts": 0.0,
     "lost_rostered_pts": 3.0}
    for t in (1, 2, 3)
]


def _acq_tables(rows):
    """Every acquisition lens table, as (header, body) pairs."""
    tables = []
    for i, row in enumerate(rows):
        if row and str(row[0]) == "Team" and "Opening" in [str(c) for c in row]:
            body = []
            for follower in rows[i + 1:]:
                if not follower or not str(follower[0]).strip():
                    break
                body.append(follower)
            tables.append((row, body))
    return tables


def _acq_total_columns(header):
    """(current-season, all-time) ACQUIRED-total column indexes.

    'Total' appears four times per row -- acquired and lost, in each half --
    so it cannot be located by name alone. The row is
    [Team, *half, '', *half], and Total is the fourth cell of a half.
    """
    n_half = (len(header) - 2) // 2
    return 1 + 3, 2 + n_half + 3


def test_every_acquisition_total_reconciles_with_its_own_channels():
    """THE ARITHMETIC. Opening read a column the ESPN mart does not have, so
    it printed 0.0 while the Total beside it carried the whole season."""
    rows, _ = _configured_tab(acquisition=_ACQ)
    tables = _acq_tables(rows)
    assert tables, "no acquisition table rendered"
    for header, body in tables:
        n_half = (len(header) - 2) // 2
        assert body, "an acquisition table rendered with no rows"
        for base in (1, 2 + n_half):
            opening, pickup, trade, total = (base, base + 1, base + 2,
                                             base + 3)
            for row in body:
                assert row[opening] > 0, (
                    "Opening is zero while a season of drafted production "
                    "exists: %r" % (row[base:base + 4],))
                assert (row[opening] + row[pickup] + row[trade]
                        == pytest.approx(row[total], abs=0.05)),                     row[base:base + 4]


def test_a_first_year_all_time_acquisition_equals_the_current_season():
    """It used to be exactly DOUBLE: the caller passed this season as the
    prior era and the presenter added the season on top of it."""
    rows, _ = _configured_tab(acquisition=_ACQ)
    for header, body in _acq_tables(rows):
        current, alltime = _acq_total_columns(header)
        for row in body:
            assert row[current] == pytest.approx(row[alltime], abs=0.05), (
                "all-time %r != current %r" % (row[alltime], row[current]))


def test_a_real_prior_era_is_still_added_to_the_current_season():
    """The first-year fix must not stop a league WITH history accumulating
    one -- that is what the all-time half is for."""
    prior = [dict(r, draft_active_pts=1000.0, acquired_active_pts=1000.0,
                  fa_add_active_pts=0.0, trade_active_pts=0.0,
                  keeper_active_pts=0.0) for r in _ACQ]
    rows, _ = _configured_tab(acquisition=_ACQ, prior_acquisition=prior)
    header, body = _acq_tables(rows)[0]
    current, alltime = _acq_total_columns(header)
    for row in body:
        assert row[alltime] == pytest.approx(
            row[current] + 1000.0, abs=0.05), row[:6]


def test_the_acquisition_caption_makes_no_claim_about_cbs():
    rows, _ = _configured_tab(acquisition=_ACQ)
    flat = " ".join(str(c) for r in rows for c in r)
    assert "CBS never logged drafts" not in flat
    assert "already on the roster when scoring began" in flat


def test_the_cbs_book_keeps_its_own_opening_sentence():
    import cbs_almanac_sheets
    assert ("CBS never logged drafts"
            in cbs_almanac_sheets.CBS_COPY["acquisition_opening_note"])


def test_an_adapter_that_reports_opening_directly_is_left_alone():
    """CBS already speaks the shared vocabulary; deriving over the top of it
    would be a silent rewrite of a mart's own number."""
    import almanac_render
    rows = [{"team_id": 1, "opening_active_pts": 12.0,
             "keeper_active_pts": 99.0, "draft_active_pts": 99.0}]
    out = almanac_render.with_standard_acquisition_channels(rows)
    assert out[0]["opening_active_pts"] == 12.0


def test_no_opening_is_invented_when_neither_vocabulary_is_present():
    """A 0.0 here would assert that nothing arrived that way."""
    import almanac_render
    out = almanac_render.with_standard_acquisition_channels(
        [{"team_id": 1, "fa_add_active_pts": 5.0}])
    assert "opening_active_pts" not in out[0]


# ---- 1. The owner fallback, everywhere ----------------------------------

def test_the_presentation_wrapper_performs_the_fallback(monkeypatch):
    """Applied per-renderer it reached two surfaces out of six. Applied by
    the one seam every workbook-facing module reads through, it cannot
    drift between tabs, books or platforms."""
    import db
    served = [
        {"team_id": 1, "team_name": "Harbor Otters",
         "owner_display": "Owner unavailable"},
        {"team_id": 2, "team_name": "Granite Owls",
         "owner_name": "Unknown owner"},
        {"team_id": 3, "team_name": "Gale Ridge",
         "owner": "Owner unavailable"},
        {"team_id": 4, "team_name": "Cedar Flats",
         "owner_display": "Dana Reid"},
        # NOT ours: the unmanned sentinel team's platform-supplied label.
        {"team_id": 7, "team_name": "####", "owner_display": "Unknown"},
    ]
    monkeypatch.setattr(db, "_BACKEND", "duckdb")
    monkeypatch.setattr(db, "_duckdb_query",
                        lambda sql, params=None: [dict(r) for r in served])
    out = db.query_for_presentation("select 1")
    assert out[0]["owner_display"] == "Harbor Otters"
    assert out[1]["owner_name"] == "Granite Owls"
    assert out[2]["owner"] == "Gale Ridge"
    assert out[3]["owner_display"] == "Dana Reid", (
        "a real owner name was overwritten")
    assert out[4]["owner_display"] == "Unknown", (
        "a platform-supplied label was mistaken for our own sentinel")


def test_the_fallback_never_invents_a_name_it_does_not_have():
    """No team name on the row means the sentinel stands. Inventing one from
    a player column would be worse than the label."""
    import owner_labels
    row = {"owner_display": "Owner unavailable", "display_name": "Rowan Pike"}
    assert owner_labels.apply_row(row)["owner_display"] == "Owner unavailable"


def test_an_empty_owner_is_not_treated_as_a_sentinel():
    """A blank cell makes no claim, and the CBS continuity harvest reads
    owner names through this same seam and writes them to a seed."""
    import owner_labels
    row = {"owner_display": "", "team_name": "Harbor Otters"}
    assert owner_labels.apply_row(row)["owner_display"] == ""


def test_identity_columns_are_never_rewritten():
    import owner_labels
    row = {"owner_id": "{GUID}", "owner_display": "Owner unavailable",
           "team_id": 7, "team_name": "Harbor Otters"}
    out = owner_labels.apply_row(dict(row))
    assert out["owner_id"] == "{GUID}"
    assert out["team_id"] == 7


def test_a_tab_builder_cannot_opt_out_of_the_fallback(monkeypatch):
    """A workbook-facing module reads through the wrapper, so no tab can
    print the sentinel by forgetting to ask."""
    import db
    import owner_labels
    monkeypatch.setattr(db, "_BACKEND", "duckdb")
    monkeypatch.setattr(db, "_duckdb_query", lambda sql, params=None: [
        {"team_id": 1, "team_name": "Harbor Otters",
         "owner_display": "Owner unavailable", "stat_value": 1.0},
    ])
    flat = " ".join(str(v) for row in db.query_for_presentation("select 1")
                    for v in row.values())
    for sentinel in owner_labels.OWNER_UNAVAILABLE_LABELS:
        assert sentinel not in flat


# ---- 6. Compact labels are unique everywhere ----------------------------

def test_the_affinity_spine_uses_the_same_labels_as_the_chart_controls():
    """Disambiguated checkboxes over two indistinguishable columns is the
    inconsistency, not either answer on its own."""
    rows, _ = _neutral_tab(periods=20, teams=4, duplicate_abbrev=True)
    controls = next(r for r in rows if r and str(r[0]) == "Chart teams:")
    control_labels = [str(c) for c in controls[1:-1]]
    spine = next(r for r in rows if r and str(r[0]) == "MLB Team")
    left = [str(c) for c in spine[1:1 + len(control_labels)]]
    assert left == control_labels, (left, control_labels)
    assert len(left) == len(set(left)), "duplicate affinity columns: %r" % left


def test_the_draft_board_disambiguates_duplicate_team_abbreviations():
    board = [
        {"overall_pick": i, "round_num": 1, "round_pick": i,
         "team_id": tid, "team_abbrev": "DUP" if tid in (7, 10) else "AAA",
         "season_points": 100.0 + i, "value_delta": i,
         "player_name": "P%d" % i, "display_name": "P%d" % i}
        for i, tid in enumerate((7, 10, 3), start=1)
    ]
    rows = almanac_logic.build_draft_tab_rows(board, 2026)
    header = next(r for r in rows if r and str(r[0]) == "Rd")
    labels = [str(c) for c in header[6:]]
    assert sorted(labels) == ["AAA", "DUP 10", "DUP 7"], labels
    assert "DUP" not in labels, "the bare duplicate abbrev survived"
    # The value leaderboards name teams too, and must agree with the board.
    flat = [str(c) for r in rows for c in r]
    assert "DUP 7" in flat and "DUP 10" in flat


def test_a_league_with_distinct_abbreviations_keeps_its_draft_board_labels():
    board = [
        {"overall_pick": i, "round_num": 1, "round_pick": i, "team_id": i,
         "team_abbrev": "T%d" % i, "season_points": 100.0 + i,
         "value_delta": i, "player_name": "P%d" % i, "display_name": "P%d" % i}
        for i in (1, 2, 3)
    ]
    rows = almanac_logic.build_draft_tab_rows(board, 2026)
    header = next(r for r in rows if r and str(r[0]) == "Rd")
    assert [str(c) for c in header[6:]] == ["T1", "T2", "T3"]


def test_the_home_boards_name_teams_the_same_way_the_tabs_do(monkeypatch):
    """A board cell whose abbreviation maps to two different team pages is
    a dead end for the reader who clicks through."""
    monkeypatch.setattr(espn_points_data, "franchise_map", lambda: {
        7: {"canonical_id": 7, "abbrev": "DUP"},
        10: {"canonical_id": 10, "abbrev": "DUP"},
        3: {"canonical_id": 3, "abbrev": "SOLO"},
    })
    out = espn_points_data.with_unique_team_abbrevs([
        {"team_id": 7, "team_abbrev": "DUP"},
        {"team_id": 10, "team_abbrev": "DUP"},
        {"team_id": 3, "team_abbrev": "SOLO"},
    ])
    assert [r["team_abbrev"] for r in out] == ["DUP 7", "DUP 10", "SOLO"]


def test_the_daily_facts_own_unknown_is_a_sentinel_only_on_its_own_column():
    """Same word, two provenances. On the daily fact's `owner_name` a bare
    "Unknown" is our pre-dim_owner fallback and the month board printed it
    once per row; on a dim_owner-resolved column it is the platform's own
    label for the unmanned sentinel team, and true -- which is also what
    the head-to-head golden corpus pins."""
    import owner_labels
    board = {"team_name": "June's Ball Club", "owner_name": "Unknown"}
    owner_labels.apply_row(board, owner_keys=("owner_name",),
                           labels=owner_labels.DAILY_FACT_UNAVAILABLE_LABELS)
    assert board["owner_name"] == "June's Ball Club"

    resolved = {"team_name": "####", "owner_display": "Unknown"}
    assert owner_labels.apply_row(resolved)["owner_display"] == "Unknown"


# --------------------------------------------------------------------------
# MLB-243 FINAL RULINGS (Kyle 2026-08-15)
#
# 1. The database seam returns warehouse truth. Presentation asks for the
#    owner fallback BY NAME. A boundary that holds because of today's SELECT
#    lists is not a boundary, and an opt-OUT has to be remembered by code
#    that does not exist yet -- so the default is raw.
# 2. The slot vocabulary is the seed's, read through the catalog.
# --------------------------------------------------------------------------

# Modules that READ TO RENDER. Every one of them must go through the
# presentation wrapper, or a tab can print the sentinel by accident.
_PRESENTATION_MODULES = (
    'almanac_data', 'almanac_sheets', 'cbs_almanac_sheets',
    'cbs_draft_recap_data', 'espn_points_data', 'generate_season_report',
    'generate_summary', 'league_notes', 'records', 'records_data',
)

# Modules that resolve identity, seed configuration, guard PII, classify a
# league or read a vocabulary. Every one of them must keep the RAW seam --
# these are the callers the wrapper exists to protect.
_RAW_MODULES = (
    'build_continuity_sheet', 'league_format', 'slot_catalog', 'stat_catalog',
)


def _sentinel_row():
    return {'team_id': 1, 'team_name': 'Harbor Otters',
            'owner_id': '{GUID-1}', 'owner_display': 'Owner unavailable'}


def test_the_raw_seam_never_rewrites_an_owner_value(monkeypatch):
    """RULING 1, the load-bearing half. `query_snowflake` is warehouse
    truth -- a continuity harvest or a PII inventory that reaches for the
    obvious function gets what the warehouse said."""
    import db
    monkeypatch.setattr(db, '_BACKEND', 'duckdb')
    monkeypatch.setattr(db, '_duckdb_query',
                        lambda sql, params=None: [_sentinel_row()])
    out = db.query_snowflake('select 1')
    assert out[0]['owner_display'] == 'Owner unavailable'
    assert out[0]['owner_id'] == '{GUID-1}'


def test_an_identity_shaped_caller_receives_raw_owner_truth(monkeypatch):
    """The continuity sheet becomes owner SEEDS. Whatever it reads is what
    a future league's owner history is built from, so it must never see a
    team name standing in for a person."""
    import build_continuity_sheet
    import db
    monkeypatch.setattr(db, '_BACKEND', 'duckdb')
    monkeypatch.setattr(db, '_duckdb_query',
                        lambda sql, params=None: [_sentinel_row()])
    row = build_continuity_sheet._q('select 1')[0]
    assert row['owner_display'] == 'Owner unavailable'
    assert row['team_name'] == 'Harbor Otters'


def _module_source(name):
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / 'output' / (name + '.py')
            ).read_text(encoding='utf-8')


def test_every_rendering_module_reads_through_the_presentation_seam():
    """The structural guarantee behind "no tab can drift".

    Read from the SOURCE rather than from a binding, because the two ways
    a module can reach the seam are both legitimate -- most `from db
    import query_for_presentation`, while league_notes goes through
    `records.query_for_presentation` -- and only one of them puts a name
    on the module."""
    for name in _PRESENTATION_MODULES:
        src = _module_source(name)
        assert 'query_for_presentation' in src, (
            '%s renders but does not read through the presentation seam'
            % name)
        assert 'query_snowflake' not in src, (
            '%s still reaches the raw seam; an owner sentinel can arrive '
            'in a rendered cell through it' % name)


def test_every_identity_or_seed_module_keeps_the_raw_seam():
    for name in _RAW_MODULES:
        src = _module_source(name)
        assert 'query_snowflake' in src, (
            '%s must read warehouse truth' % name)
        assert 'query_for_presentation' not in src, (
            '%s is not a rendering module and must not have a display rule '
            'applied to warehouse truth' % name)


def test_the_pii_guard_and_the_row_count_fixture_stay_on_the_raw_seam():
    """Named separately because these two are the ones a mistake would
    hurt most: the guard decides whether a push is blocked, and the fixture
    is tracked."""
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    for rel in ('tools/check_pii.py', 'tests/capture_row_counts.py'):
        src = (repo / rel).read_text(encoding='utf-8')
        assert 'query_for_presentation' not in src, rel
        assert 'query_snowflake' in src, rel


def test_a_real_owner_name_and_every_identifier_survive_the_wrapper(
        monkeypatch):
    import db
    served = [
        {'team_id': 4, 'team_name': 'Cedar Flats', 'owner_id': '{GUID-4}',
         'owner_display': 'Dana Reid'},
        {'team_id': 1, 'team_name': 'Harbor Otters', 'owner_id': '{GUID-1}',
         'owner_display': 'Owner unavailable'},
    ]
    monkeypatch.setattr(db, '_BACKEND', 'duckdb')
    monkeypatch.setattr(db, '_duckdb_query',
                        lambda sql, params=None: [dict(r) for r in served])
    out = db.query_for_presentation('select 1')
    assert out[0]['owner_display'] == 'Dana Reid'
    assert [r['owner_id'] for r in out] == ['{GUID-4}', '{GUID-1}']
    assert [r['team_id'] for r in out] == [4, 1]
    assert [r['team_name'] for r in out] == ['Cedar Flats', 'Harbor Otters']


def test_a_whole_workbook_payload_carries_no_owner_sentinel(monkeypatch):
    """RULING 1's product half, over a COMPLETE payload rather than a row:
    every tab of the points book, assembled, with a warehouse that withheld
    every owner name."""
    import db
    import owner_labels
    monkeypatch.setattr(db, '_BACKEND', 'duckdb')
    monkeypatch.setattr(
        db, '_duckdb_query',
        lambda sql, params=None: [
            {'season_year': 2026, 'team_id': 1, 'team_name': 'Harbor Otters',
             'owner_display': 'Owner unavailable',
             'owner_name': 'Owner unavailable', 'stat_value': 1.0},
        ])
    rows = db.query_for_presentation('select 1')
    flat = ' '.join(str(v) for row in rows for v in row.values())
    for sentinel in owner_labels.OWNER_UNAVAILABLE_LABELS:
        assert sentinel not in flat
    assert 'Harbor Otters' in flat


# ---- RULING 2: the slot vocabulary is the seed's -------------------------

def test_every_utility_spelling_converges_on_the_catalog_key(
        stub_slot_catalog):
    import slot_catalog
    keys = {slot_catalog.canonical_lineup_slot(s)
            for s in ('U', 'UTIL', 'Util', 'utility', 'Utility', ' util ')}
    assert keys == {'utility'}, keys


def test_the_key_is_the_catalogs_own_vocabulary_not_a_python_spelling(
        stub_slot_catalog):
    """'UTIL' was this module's invented answer. The catalog's answer is
    'utility', and it is the same column the project joins CBS and ESPN
    stats on."""
    import slot_catalog
    assert slot_catalog.canonical_lineup_slot('U') == 'utility'
    assert slot_catalog.canonical_lineup_slot('P') == 'pitcher'


def test_no_hand_maintained_alias_table_remains():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'output' / 'slot_catalog.py'
           ).read_text(encoding='utf-8')
    assert '_SLOT_ALIASES' not in src, (
        'a second Python slot vocabulary reappeared beside the seed')


def test_editing_the_catalog_changes_python_without_editing_python(
        monkeypatch):
    """THE REGRESSION KYLE ASKED FOR. A platform whose utility slot is
    spelled FLEX converges the moment the SEED says so -- no Python edit,
    no release."""
    import slot_catalog

    def _catalog_with_flex(sql, params=None):
        return [
            {'platform': 'espn', 'lineup_slot': 'UTIL', 'slot_category':
             'hitting', 'is_starting_slot': True, 'sort_order': 120,
             'notes': None, 'canonical_key': 'utility'},
            {'platform': 'yahoo', 'lineup_slot': 'FLEX', 'slot_category':
             'hitting', 'is_starting_slot': True, 'sort_order': 120,
             'notes': None, 'canonical_key': 'utility'},
        ]

    for name in dir(slot_catalog):
        clear = getattr(getattr(slot_catalog, name, None), 'cache_clear', None)
        if clear is not None:
            clear()
    monkeypatch.setattr(slot_catalog, 'query_snowflake', _catalog_with_flex)
    try:
        assert slot_catalog.canonical_lineup_slot('FLEX') == 'utility'
        assert slot_catalog.canonical_lineup_slot('UTIL') == 'utility'
    finally:
        for name in dir(slot_catalog):
            clear = getattr(getattr(slot_catalog, name, None),
                            'cache_clear', None)
            if clear is not None:
                clear()


def test_an_unknown_slot_stays_visible_rather_than_being_reclassified(
        stub_slot_catalog):
    """It must not be dropped and it must not be filed under a position it
    is not -- it comes back as itself and simply matches no column."""
    import slot_catalog
    assert slot_catalog.canonical_lineup_slot('ROVER') == 'ROVER'
    assert slot_catalog.canonical_lineup_slot('') == ''
    assert slot_catalog.canonical_lineup_slot(None) == ''


def test_the_presenter_stays_queryless_and_takes_the_resolver(monkeypatch):
    """The builder's own promise is that it queries nothing. Reading the
    seed is a warehouse round-trip, so the resolver is handed IN -- and the
    default is a pure upper-case, which is what keeps the mature book's
    tests (and its layout-only callers) free of a connection."""
    import db
    import cbs_almanac_sheets

    def _explode(*a, **k):
        raise AssertionError('build_standings_rows reached the warehouse')

    monkeypatch.setattr(db, 'query_snowflake', _explode)
    monkeypatch.setattr(db, 'query_for_presentation', _explode)
    monkeypatch.setattr(cbs_almanac_sheets, 'query_for_presentation', _explode)
    rows, _formats = _configured_tab(
        slot_columns=('C', 'U', 'P'), slot_spellings=('C', 'UTIL', 'P'))
    header, body = _section_body(rows, 'Team')
    assert header[1:4] == ['C', 'U', 'P']
    # Without a resolver the U column cannot see UTIL rows -- honest, and
    # visible as an empty column rather than as misfiled production.
    assert all(not str(r[2]).strip() for r in body)


def test_the_points_caller_hands_in_the_seed_backed_resolver():
    """Which is what makes the live Util column fill."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'output' /
           'points_almanac.py').read_text(encoding='utf-8')
    assert 'slot_key=slot_catalog.canonical_lineup_slot' in src


def test_util_rows_reach_a_u_column_when_the_resolver_is_supplied(
        stub_slot_catalog):
    """End to end for the ruling: a book that displays U, a feed that says
    UTIL, and the seed saying they are one slot."""
    import slot_catalog
    rows, _formats = _configured_tab(
        slot_columns=('C', 'U', 'P'), slot_spellings=('C', 'UTIL', 'P'),
        slot_key=slot_catalog.canonical_lineup_slot)
    header, body = _section_body(rows, 'Team')
    assert header[1:4] == ['C', 'U', 'P']
    assert all(isinstance(r[2], (int, float)) and r[2] > 0 for r in body)


# ---- RULING 3: the acquisition bridge is named for what it is ------------

def test_the_acquisition_bridge_is_not_described_as_warehouse_convergence():
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    src = (repo / 'output' / 'almanac_render.py').read_text(encoding='utf-8')
    start = src.index('def with_standard_acquisition_channels')
    doc = src[start:start + 2600]
    assert 'MLB-249' in doc, 'the owning ticket is not named'
    assert 'NOT the semantic model' in doc or 'not the semantic model' in doc
    changelog = (repo / 'CHANGELOG.md').read_text(encoding='utf-8')
    assert 'not warehouse convergence' in changelog
    assert 'MLB-249' in changelog
