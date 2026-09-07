"""Pure tests for the redesigned Records surfaces (MLB-212 dev render).

Everything here runs on synthetic unit rows; no warehouse. The rules
under test are the ones the spec states once (section numbers in the
test names refer to the build spec on MLB-212).
"""

import pytest

import records_book_logic as L


def _band(key='level_all', grain='week', scope='all', last='Period'):
    return L.Band(key, key, grain, scope, last)


def _row(**kw):
    base = {'season': 2026, 'unit': 5, 'mp': 5, 'team_id': '1', 'team_name': 'Team One',
            'abbrev': 'ONE', 'owner': 'Owner One', 'pid': None, 'pname': None, 'dname': None,
            'pts': 100.0, 'hit_pts': 60.0, 'pit_pts': 40.0, 'ab': 300, 'outs': 200, 'h': 90,
            'b_bb': 30, 'hbp': 2, 'sf': 3, 'tb': 150, 'er': 40, 'p_bb': 20, 'p_h': 60, 'k': 70,
            'b_so': 50}
    base.update(kw)
    return L.add_rates(base)


# ---- section 1: the lens rule ------------------------------------------------

def test_seven_day_league_gets_three_tabs_and_day_drilldown():
    tabs = L.derive_tabs('h2h', seasons_count=2)
    assert [t['key'] for t in tabs] == ['matchup', 'season', 'lifetime']
    assert [b.key for b in tabs[0]['bands']] == ['level_cur', 'level_all', 'day_all']
    assert [b.key for b in tabs[1]['bands']] == ['season_cur', 'season_all']


def test_season_long_league_drops_the_matchup_tab_and_drills_to_weeks():
    tabs = L.derive_tabs('points', seasons_count=26)
    assert [t['key'] for t in tabs] == ['season', 'lifetime']
    bands = tabs[0]['bands']
    assert [b.key for b in bands] == ['season_cur', 'season_all', 'week_all']
    assert bands[2].title == 'Best Weeks All-Time'
    assert bands[0].last_col == 'Runner-up' and bands[1].last_col == 'Season'


def test_one_season_league_collapses_all_time_and_rides_season_on_matchup():
    tabs = L.derive_tabs('h2h', seasons_count=1)
    assert [t['key'] for t in tabs] == ['matchup', 'lifetime']
    keys = [b.key for b in tabs[0]['bands']]
    assert 'level_all' not in keys and 'season_cur' in keys


def test_drilldown_none_yields_two_bands():
    tabs = L.derive_tabs('h2h', drilldown='none', seasons_count=2)
    assert [b.key for b in tabs[0]['bands']] == ['level_cur', 'level_all']


def test_lifetime_tab_always_renders():
    for fmt, n in (('h2h', 1), ('points', 1), ('points', 5)):
        assert L.derive_tabs(fmt, seasons_count=n)[-1]['key'] == 'lifetime'


# ---- section 2.3 / 2.4: polarity and floors ----------------------------------

def test_negative_stat_reads_at_fewest_with_the_category_floor():
    catalog = [{'key': 'b_so', 'display_name': 'Strikeouts (Batter)', 'stat_category': 'hitting',
                'polarity': 'negative'},
               {'key': 'hr', 'display_name': 'Home Runs', 'stat_category': 'hitting',
                'polarity': 'positive'}]
    metrics = {m.key: m for m in L.counting_metrics(catalog, 'hitting')}
    assert metrics['b_so'].good_dir == 'asc' and metrics['b_so'].floor == 'ab'
    assert metrics['hr'].good_dir == 'desc' and metrics['hr'].floor is None
    assert metrics['b_so'].label == 'Strikeouts'


def test_rates_are_none_below_the_floor():
    row = L.add_rates({'ab': 100, 'h': 40, 'outs': 100, 'er': 10})
    assert row['avg'] is None and row['era'] is None
    row = L.add_rates({'ab': 225, 'h': 90, 'b_bb': 0, 'hbp': 0, 'sf': 0, 'tb': 100,
                       'outs': 150, 'er': 10, 'p_bb': 0, 'p_h': 0, 'k': 50})
    assert row['avg'] == pytest.approx(0.4)
    assert row['era'] == pytest.approx(1.8)
    assert row['k9'] == pytest.approx(9.0)


def test_fewest_record_skips_rows_under_the_floor():
    pool = L.Pool([_row(team_id='1', abbrev='ONE', b_so=5, ab=100),
                   _row(team_id='2', abbrev='TWO', b_so=30, ab=300)])
    m = L.Metric('b_so', 'Strikeouts', 'count', 'hitting', 'asc', 'int', 'ab', 'negative')
    cell = L.record_cell(pool, m, 'asc', _band(), 2026)
    assert cell.holders[0]['abbrev'] == 'TWO' and cell.value == 30


# ---- section 2.5: never render an empty row ---------------------------------

def test_most_record_nobody_set_is_omitted():
    pool = L.Pool([_row(cyc=0), _row(team_id='2', cyc=0)])
    m = L.Metric('cyc', 'Cycle', 'count', 'hitting', 'desc', 'int')
    assert L.record_cell(pool, m, 'desc', _band(), 2026) is None


def test_record_row_with_no_qualifying_band_is_not_emitted():
    sheet = L.Sheet()
    ctx = L.Context(2026, (2026, 5), 12, {}, lambda r, b: 'Week 5')
    m = L.Metric('cyc', 'Cycle', 'count', 'hitting', 'desc', 'int')
    pools = {('team', 'level_all'): L.Pool([_row(cyc=0)])}
    assert L.record_row(sheet, 'Cycle', m, 'desc', 'team', [_band()], pools, ctx) is False
    assert sheet.rows == []


# ---- section 2.9: ties -------------------------------------------------------

def test_small_tie_lists_abbreviations_and_counts_in_details():
    rows = [_row(team_id='1', abbrev='ONE', owner='A', hr=20, unit=3),
            _row(team_id='2', abbrev='TWO', owner='B', hr=20, unit=7),
            _row(team_id='3', abbrev='THR', owner='C', hr=10)]
    pool = L.Pool(rows)
    m = L.Metric('hr', 'Home Runs', 'count', 'hitting', 'desc', 'int')
    band = _band()
    ctx = L.Context(2026, (2026, 7), 12, {}, lambda r, b: f"Week {r['unit']}")
    cell = L.record_cell(pool, m, 'desc', band, 2026)
    cells, _ = L.side_cells(cell, m, 'team', band, ctx)
    assert cells[0] == 'TWO, ONE'          # most recent instance first
    assert cells[1] == 'B, A'
    assert cells[3] == '20 recorded by 2 teams'
    assert cells[4].startswith('TWO Week 7; ONE Week 3')


def test_big_tie_collapses_to_a_count():
    rows = [_row(team_id=str(i), abbrev=f'T{i}', hr=5) for i in range(6)]
    m = L.Metric('hr', 'Home Runs', 'count', 'hitting', 'desc', 'int')
    cell = L.record_cell(L.Pool(rows), m, 'desc', _band(), 2026)
    cells, _ = L.side_cells(cell, m, 'team', _band(),
                            L.Context(2026, None, 12, {}, lambda r, b: 'Week 5'))
    assert cells[0] == '6 teams tied' and cells[1] == ''
    assert cells[3] == '5 recorded by 6 teams'


def test_a_team_tied_with_itself_lists_once():
    rows = [_row(team_id='1', abbrev='ONE', hr=9, unit=2), _row(team_id='1', abbrev='ONE', hr=9, unit=8)]
    m = L.Metric('hr', 'Home Runs', 'count', 'hitting', 'desc', 'int')
    cell = L.record_cell(L.Pool(rows), m, 'desc', _band(), 2026)
    cells, _ = L.side_cells(cell, m, 'team', _band(),
                            L.Context(2026, None, 12, {}, lambda r, b: 'Week'))
    assert cells[0] == 'ONE'


# ---- section 2.14: completeness ---------------------------------------------

def test_in_flight_unit_never_holds_a_lowest_record_outside_its_own_season():
    rows = [_row(season=2026, pts=50.0, complete=False, standard=True),
            _row(season=2025, team_id='2', abbrev='TWO', pts=80.0, complete=True, standard=True)]
    m = L.POINTS_METRICS[0]
    cell = L.record_cell(L.Pool(rows), m, 'asc', _band('season_all', 'season'), 2026)
    assert cell.holders[0]['season'] == 2025
    # ...but it does hold a highest record, marked as in flight.
    rows[0]['pts'] = 90.0
    cell = L.record_cell(L.Pool(rows), m, 'desc', _band('season_all', 'season'), 2026)
    assert cell.holders[0]['season'] == 2026 and cell.in_flight
    cells, _ = L.side_cells(cell, m, 'team', _band('season_all', 'season', 'all', 'Season'),
                            L.Context(2026, None, 12, {}, lambda r, b: ''))
    assert cells[2] == '90.0*'


def test_this_season_band_compares_in_flight_with_in_flight_and_carries_no_asterisk():
    rows = [_row(season=2026, pts=50.0, complete=False),
            _row(season=2026, team_id='2', abbrev='TWO', pts=80.0, complete=False)]
    band = _band('season_cur', 'season', 'current', 'Runner-up')
    cell = L.record_cell(L.Pool(rows), L.POINTS_METRICS[0], 'asc', band, 2026, want_runner_up=True)
    assert cell.value == 50.0
    cells, _ = L.side_cells(cell, L.POINTS_METRICS[0], 'team', band,
                            L.Context(2026, None, 12, {}, lambda r, b: ''))
    assert cells[2] == 50.0
    assert cells[4] == 'TWO (80.0)'          # runner-up in place of a season


def test_worst_hitting_points_needs_a_hitter():
    rows = [_row(team_id='1', pid='p1', hit_pts=0.0, ab=0, pname='Pitcher Pete'),
            _row(team_id='2', pid='p2', hit_pts=-4.0, ab=20, pname='Hitter Hank')]
    cell = L.record_cell(L.Pool(rows), L.POINTS_METRICS[1], 'asc', _band(), 2026)
    assert cell.holders[0]['pid'] == 'p2'


def test_player_rate_and_fewest_rows_have_no_week_or_day_cell():
    sheet = L.Sheet()
    ctx = L.Context(2026, None, 12, {}, lambda r, b: 'Week 1')
    row = _row(pid='p1', pname='Some Body', b_so=1, ab=300)
    m = L.Metric('b_so', 'Strikeouts', 'count', 'hitting', 'asc', 'int', 'ab', 'negative')
    pools = {('player', 'level_all'): L.Pool([row])}
    assert L.record_row(sheet, 'Strikeouts', m, 'asc', 'player', [_band()], pools, ctx) is False
    pools = {('player', 'season_all'): L.Pool([row])}
    assert L.record_row(sheet, 'Strikeouts', m, 'asc', 'player',
                        [_band('season_all', 'season', 'all', 'Season')], pools, ctx) is True


# ---- section 5: leaderboard depth and the Halls -----------------------------

def test_slot_depth_halves_each_extra_copy():
    assert L.slot_depth(12, 1) == 12
    assert L.slot_depth(12, 5) == 36
    assert L.slot_depth(12, 6) == 42
    assert L.slot_depth(12, 3) == 24


def test_hall_boards_split_by_discipline_and_rank_25():
    rows = []
    for i in range(30):
        rows.append({'pid': f'h{i}', 'pts': 100 + i, 'hit_pts': 100 + i, 'pit_pts': 0})
        rows.append({'pid': f'p{i}', 'pts': 50 + i, 'hit_pts': 0, 'pit_pts': 50 + i})
    overall, hitters, pitchers = L.hall_boards(rows)
    assert len(overall) == len(hitters) == len(pitchers) == 25
    assert overall[0][0]['pid'] == 'h29' and overall[0][2] == 'hitting'
    assert pitchers[0][0]['pid'] == 'p29'
    assert all(r['pit_pts'] == 0 for r, _, _ in hitters)


def test_shame_cells_carry_the_three_terms_and_a_percentage():
    row = {'pid': 'x', 'pname': 'Bench Bob', 'dname': 'Bench Bob', 'benched_hit': 100,
           'unrostered_hit': 50, 'neg_hit': 10, 'hit_pts': 240, 'bench_by_hit': 'ONE (100)'}
    cells = L.shame_cells(row, 'hitting')
    assert cells[1] == 'ONE (100)'
    assert cells[2] == 160.0
    assert cells[3] == '100 benched · 50 unrostered · 10 negative · 40% of career wasted'


def test_wasted_breakdown_orders_terms_largest_first():
    row = L.add_wasted({'benched_hit': 10, 'benched_pit': 5, 'unrostered_hit': 40,
                        'unrostered_pit': 0, 'neg_hit': 1, 'neg_pit': 2})
    assert row['wasted'] == 58 and row['wasted_hit'] == 51
    assert L.wasted_breakdown(row) == '40 unrostered · 15 benched · 3 negative'


# ---- section 2.6: shading and buffers ---------------------------------------

def test_sheet_never_stacks_three_shaded_rows():
    sheet = L.Sheet()
    sheet.section('Score Records', ['A', 'B'], ['Holder', 'Owner', 'Value', 'Details', 'Period'])
    with pytest.raises(AssertionError):
        sheet.add(['x'], shaded=True)


def test_no_blank_row_directly_after_a_banner():
    sheet = L.Sheet()
    sheet.title('T')
    sheet.banner('TEAM RECORDS')
    sheet.section('Score Records', ['A'], ['Holder', 'Owner', 'Value', 'Details', 'Period'])
    assert sheet.rows[-3][0] == 'TEAM RECORDS'
    assert sheet.rows[-2][0] == 'Score Records'


def test_period_tab_has_the_required_sections_in_order():
    tab = L.derive_tabs('h2h', seasons_count=2)[0]
    ctx = L.Context(2026, (2026, 5), 12, {}, lambda r, b: f"Week {r['unit']}")
    catalog = [{'key': 'hr', 'display_name': 'Home Runs', 'stat_category': 'hitting',
                'polarity': 'positive'},
               {'key': 'k', 'display_name': 'Strikeouts (Pitcher)', 'stat_category': 'pitching',
                'polarity': 'positive'}]
    team = _row(hr=12, k=40)
    player = _row(pid='p1', pname='Some Body', dname='Some Body', hr=4, k=12)
    pools = {}
    for key in ('level_cur', 'level_all', 'day_all'):
        pools[('team', key)] = L.Pool([team])
        pools[('player', key)] = L.Pool([player])
    sheet = L.build_period_tab(tab, pools, ctx, catalog, [])
    labels = [r[0] for r in sheet.rows]
    order = ['Matchup Records', 'TEAM RECORDS', 'Score Records', 'Hitting Records',
             'Pitching Records', 'Lineup Slot Records', 'PLAYER RECORDS', 'Score Records',
             'Best Performances', 'Hitting Records', 'Pitching Records', 'Lineup Slot Records']
    idx = -1
    for label in order:
        idx = labels.index(label, idx + 1)
    assert 'Home Runs' in labels and 'Strikeouts' in labels
    assert sheet.jump_targets['m-tscore'] == labels.index('Score Records') + 1
