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

SEASON_TOTALS = [
    {'team_id': 1, 'team_name': 'Harbor Otters', 'team_abbrev': 'HAR',
     'owner_display': 'Owner unavailable', 'calculated_points': 5361.0,
     'calculated_hitting_pts': 2796.0, 'calculated_pitching_pts': 2565.0,
     'negative_points': 328.0, 'platform_points': 5361.0, 'points_rank': 1},
    {'team_id': 2, 'team_name': 'Granite Owls', 'team_abbrev': 'GRN',
     'owner_display': 'Owner unavailable', 'calculated_points': 4641.0,
     'calculated_hitting_pts': 2614.0, 'calculated_pitching_pts': 2027.0,
     'negative_points': 309.0, 'platform_points': 4641.0, 'points_rank': 2},
    {'team_id': 3, 'team_name': 'Gale Ridge Giants', 'team_abbrev': 'GRN',
     'owner_display': 'Owner unavailable', 'calculated_points': 3669.0,
     'calculated_hitting_pts': 2174.0, 'calculated_pitching_pts': 1495.0,
     'negative_points': 415.0, 'platform_points': 3669.0, 'points_rank': 3},
]

SLOT_ROWS = [
    {'team_id': 1, 'team_name': 'Harbor Otters', 'lineup_slot': 'C',
     'slot_calculated_points': 308.0, 'sort_order': 1,
     'is_active_lineup_slot': True, 'starter_count': 1},
    {'team_id': 1, 'team_name': 'Harbor Otters', 'lineup_slot': 'P',
     'slot_calculated_points': 2565.0, 'sort_order': 8,
     'is_active_lineup_slot': True, 'starter_count': 7},
    {'team_id': 2, 'team_name': 'Granite Owls', 'lineup_slot': 'C',
     'slot_calculated_points': 242.0, 'sort_order': 1,
     'is_active_lineup_slot': True, 'starter_count': 1},
    {'team_id': 2, 'team_name': 'Granite Owls', 'lineup_slot': 'P',
     'slot_calculated_points': 2027.0, 'sort_order': 8,
     'is_active_lineup_slot': True, 'starter_count': 7},
]


def _cells(rows):
    return [str(cell) for row in rows for cell in row]


# --------------------------------------------------------------------------
# Advanced Standings: current-season totals, not a W-L model
# --------------------------------------------------------------------------

def test_advanced_standings_is_populated_for_an_unfinished_first_season():
    """No completed matchup, no completed season, real numbers anyway."""
    rows = almanac_logic.build_points_standings_tab_rows(
        SEASON_TOTALS, SLOT_ROWS, 2026)
    flat = _cells(rows)
    assert 'Harbor Otters' in flat
    assert '5361' in flat, 'the season total did not reach the table'
    assert '2565' in flat, 'lineup-slot production did not reach the table'
    assert 'Points by Lineup Slot' in flat


def test_advanced_standings_uses_no_h2h_win_loss_model():
    """`mart_team_season_standings` is empty for this format by design.
    Rendering its shape would have produced a grid of blanks under a W-L
    header."""
    rows = almanac_logic.build_points_standings_tab_rows(
        SEASON_TOTALS, SLOT_ROWS, 2026)
    header = next(r for r in rows if r and r[0] == 'Rank')
    assert 'W' not in header and 'L' not in header
    assert 'Record' not in header
    assert 'Total Points' in header
    body = ' '.join(_cells(rows)).lower()
    assert 'there are no matchups and no w-l record' in body


def test_behind_leader_is_measured_from_the_leader():
    rows = almanac_logic.build_points_standings_tab_rows(
        SEASON_TOTALS, SLOT_ROWS, 2026)
    data = [r for r in rows if r and r[0] in (1, 2, 3)]
    assert data[0][-1] == 0
    assert data[1][-1] == 5361 - 4641


def test_absent_points_render_blank_rather_than_zero():
    """"No data" and "zero points" are different answers."""
    assert almanac_logic._points_cell(None) == ''
    assert almanac_logic._points_cell(0) == 0


def test_an_empty_season_says_so_instead_of_rendering_an_empty_grid():
    rows = almanac_logic.build_points_standings_tab_rows([], [], 2026)
    flat = ' '.join(_cells(rows))
    assert 'No team production captured' in flat
    assert 'No lineup-slot production captured' in flat


# --------------------------------------------------------------------------
# The Rivalry Matrix keeps its completed-season requirement
# --------------------------------------------------------------------------

def test_an_unfinished_season_is_not_reinterpreted_as_a_rivalry_result():
    rows = almanac_logic.build_points_standings_tab_rows(
        SEASON_TOTALS, SLOT_ROWS, 2026,
        rivalry_note=almanac_logic.RIVALRY_UNAVAILABLE_NOTE)
    flat = ' '.join(_cells(rows))
    assert 'Rivalry Matrix: unavailable' in flat
    assert 'COMPLETED seasons' in flat


def test_a_completed_season_gets_no_unavailable_note():
    rows = almanac_logic.build_points_standings_tab_rows(
        SEASON_TOTALS, SLOT_ROWS, 2026, rivalry_note=None)
    assert 'Rivalry Matrix: unavailable' not in ' '.join(_cells(rows))


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
    monkeypatch.setattr(espn_points_data, 'season_totals',
                        lambda season: SEASON_TOTALS)
    monkeypatch.setattr(espn_points_data, 'slot_production',
                        lambda season: SLOT_ROWS)
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
    titles = [title for title, _ in tabs]
    assert TEAM_WEEKS_TAB not in titles, f'Matchup History was built: {titles}'


def test_an_in_flight_first_season_still_gets_home_records_and_standings(
        assembled):
    """No completed matchup, no completed season, a full book anyway."""
    tabs, _context, _titles = assembled.build_all_tabs(include_trades=False)
    by_title = dict(tabs)
    assert 'Home' in by_title and 'Records' in by_title
    assert 'Advanced Standings' in by_title

    assert any('Team of the Month' in str(c)
               for row in by_title['Home'] for c in row)
    assert by_title['Records'], 'the Records tab came out empty'
    standings = [str(c) for row in by_title['Advanced Standings'] for c in row]
    assert '5361' in standings, 'season totals did not reach Advanced Standings'


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
    standings = almanac_logic.build_points_standings_tab_rows(
        SEASON_TOTALS, SLOT_ROWS, 2026, caveat=caveat)
    assert caveat in _cells(home)
    assert caveat in _cells(standings)


def test_no_caveat_leaves_both_tabs_exactly_as_they_were():
    """An ordinary league must not gain a blank row where the warning
    would have gone."""
    with_none = almanac_logic.build_points_standings_tab_rows(
        SEASON_TOTALS, SLOT_ROWS, 2026, caveat=None)
    assert all(r != [None] and r != [''] for r in with_none[:4])
    assert with_none[2] == []


def test_the_warning_does_not_change_any_number():
    """Detected and stated -- never silently corrected. The totals with the
    caveat must equal the totals without it."""
    without = almanac_logic.build_points_standings_tab_rows(
        SEASON_TOTALS, SLOT_ROWS, 2026)
    with_note = almanac_logic.build_points_standings_tab_rows(
        SEASON_TOTALS, SLOT_ROWS, 2026, caveat='anything at all')
    numbers = lambda rows: [c for r in rows for c in r
                            if isinstance(c, (int, float))]
    assert numbers(without) == numbers(with_note)


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
