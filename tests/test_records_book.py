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
    assert cells[2] == 20
    assert cells[3] == '2 times; first Week 3, last Week 7'
    assert cells[4].startswith('TWO Week 7; ONE Week 3')


def test_big_tie_collapses_to_a_count():
    rows = [_row(team_id=str(i), abbrev=f'T{i}', hr=5) for i in range(6)]
    m = L.Metric('hr', 'Home Runs', 'count', 'hitting', 'desc', 'int')
    cell = L.record_cell(L.Pool(rows), m, 'desc', _band(), 2026)
    cells, _ = L.side_cells(cell, m, 'team', _band(),
                            L.Context(2026, None, 12, {}, lambda r, b: 'Week 5'))
    assert cells[0] == '6 teams tied' and cells[1] == ''
    assert cells[3] == '6 times; first Week 5, last Week 5'


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


def test_player_slot_rows_match_the_starting_lineup_and_team_rows_stay_lumped():
    slots = [{'label': 'SP', 'count': 2, 'category': 'pitching'}]
    ctx = L.Context(2026, None, 12, {}, lambda r, b: 'Week')
    rows = [_row(pid=f'p{i}', pname=f'P {i}', dname=f'P {i}', slot='SP', pts=50.0 - i, unit=i)
            for i in range(3)]
    sheet = L.Sheet()
    L.slot_rows(sheet, 'player', [_band()], {('player_slot', 'level_all'): L.Pool(rows)}, ctx, slots)
    assert [r[0] for r in sheet.rows] == ['SP 1', 'SP 2']
    assert sheet.rows[0][3] == 50.0 and sheet.rows[1][3] == 49.0
    sheet = L.Sheet()
    L.slot_rows(sheet, 'team', [_band()], {('team_slot', 'level_all'): L.Pool(rows)}, ctx, slots)
    assert [r[0] for r in sheet.rows] == ['SP']


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
    order = ['Matchup Records', 'TEAM RECORDS', 'Score Records', 'Best Performances',
             'Hitting Records', 'Pitching Records', 'Lineup Slot Records', 'PLAYER RECORDS',
             'Score Records', 'Best Performances', 'Hitting Records', 'Pitching Records',
             'Lineup Slot Records']
    idx = -1
    for label in order:
        idx = labels.index(label, idx + 1)
    assert 'Home Runs' in labels and 'Strikeouts' in labels
    assert sheet.jump_targets['m-tscore'] == labels.index('Score Records') + 1
    # Kyle 09-11: team sections close at the bad end; players do not.
    assert labels.count('Fewest Home Runs') == 1 and labels.count('Lowest AVG') == 1
    assert labels.index('Fewest Home Runs') < labels.index('PLAYER RECORDS')
    worst = next(r for r in sheet.rows if r[0] == 'Worst')
    assert worst[1] == 'Worst Weeks This Season' and worst[7] == 'Worst Weeks All-Time'
    assert labels.count('Best Performances') == 2 and 'Total' in labels
    assert all(collapsed for _, _, collapsed in sheet.groups)
    assert not any('\u2014' in str(c) for r in sheet.rows for c in r)


# ---- section 2.12 (09-09): stat lines and hybrid placement ------------------

OHTANI = dict(hit_pts=3000.0, pit_pts=1000.0, ab=2000, h=600, b_bb=200, hbp=10, sf=20,
              tb=1200, hr=412, rbi=380, r=300, outs=630, k=300, er=100, p_bb=60, p_h=150, w=30)
PPU = {'hr': 4.0, 'outs': 0.67, 'rbi': 1.0, 'r': 1.0, 'k': 1.0, 'w': 3.0}


def test_stat_line_leads_with_the_dominant_rates_then_ranks_both_disciplines_by_points():
    assert L.stat_line(dict(OHTANI), PPU) == '.300/.363/.600 · 412 HR, 210.0 IP, 380 RBI'


def test_pitcher_dominant_line_leads_with_the_home_tab_slash_and_keeps_ip_as_a_stat():
    row = dict(OHTANI, hit_pts=100.0, pit_pts=900.0, l=4, sv=2)
    assert L.dominant_role(row) == 'pitching'
    lead, counts = L.stat_line(row, PPU).split(' · ', 1)
    assert lead == '30-4-2/4.29/1.00'                  # W-L-Sv/ERA/WHIP, no labels
    assert counts == '412 HR, 210.0 IP, 380 RBI'
    assert L.stat_cell_format(row)['horizontalAlignment'] == 'LEFT'
    assert L.stat_cell_format(dict(OHTANI))['horizontalAlignment'] == 'RIGHT'


def test_nothing_defaults_to_hitting():
    assert L.dominant_role({'hit_pts': 0, 'pit_pts': 0, 'ab': 0, 'outs': 30}) == 'pitching'
    assert L.dominant_role({'hit_pts': 0, 'pit_pts': 0}) is None
    assert L.stat_line({'hit_pts': 0, 'pit_pts': 0}, PPU) == ''


def test_hybrid_lands_on_one_discipline_board_and_once_on_overall():
    rows = [{'pid': 'sho', 'pts': 190, 'hit_pts': 100, 'pit_pts': 90},
            {'pid': 'h', 'pts': 80, 'hit_pts': 80, 'pit_pts': 0},
            {'pid': 'p', 'pts': 70, 'hit_pts': 0, 'pit_pts': 70}]
    overall, hitters, pitchers = L.hall_boards(rows)
    assert [r['pid'] for r, _, _ in overall] == ['sho', 'h', 'p']
    assert [r['pid'] for r, _, _ in hitters] == ['sho', 'h']
    assert [r['pid'] for r, _, _ in pitchers] == ['p']


# ---- section 2.7 / 5.1 (09-09): years of service, franchises column ---------

def test_years_of_service_counts_then_lists_hyphenated_runs():
    assert L.years_of_service_text([2019, 2007, 2005, 2006, 2001]) == '5: 2001, 2005–2007, 2019'
    assert L.years_of_service_text({2026}) == '1: 2026'
    assert L.years_of_service_text([]) == ''


def test_franchises_column_is_abbreviations_only():
    row = {'by_team': {'1': 1993.0, '2': 1308.0, '3': 1263.0, '4': 0.2},
           'team_labels': {'1': 'SED', '2': 'CSC', '3': 'FULT'}}
    assert L.franchises_text(row) == 'SED, CSC, FULT'


# ---- section 2.9 (09-09): mass ties ------------------------------------------

def test_mass_tie_reads_no_record_beside_a_real_band_and_never_alone():
    rows = [_row(team_id=str(i), abbrev=f'T{i}', unit=i, hld=1) for i in range(1, 4)]
    m = L.Metric('hld', 'Holds', 'count', 'pitching', 'desc', 'int')
    cell = L.record_cell(L.Pool(rows), m, 'desc', _band(), 2026, mass_tie_limit=3)
    assert cell.mass_tie and cell.tie_n == 3
    assert not L.record_cell(L.Pool(rows), m, 'desc', _band(), 2026, mass_tie_limit=4).mass_tie
    # A lone holder is never a mass tie, even on a one-period band.
    solo = [_row(team_id='1', hld=2)]
    assert not L.record_cell(L.Pool(solo), m, 'desc', _band(), 2026, mass_tie_limit=1).mass_tie
    ctx = L.Context(2026, None, 12, {}, lambda r, b: 'Week', period_counts={'week': 3, 'day': 99})
    week, day = _band(), _band('day_all', 'day', 'all')
    sheet = L.Sheet()
    # Every band a mass tie -> no row at all.
    assert L.record_row(sheet, 'Holds', m, 'desc', 'team', [week], {('team', 'level_all'): L.Pool(rows)}, ctx) is False
    # One real band -> the tied band says so instead of going blank.
    pools = {('team', 'level_all'): L.Pool(rows), ('team', 'day_all'): L.Pool(rows)}
    assert L.record_row(sheet, 'Holds', m, 'desc', 'team', [week, day], pools, ctx) is True
    out = sheet.rows[-1]
    assert out[1] == 'no record' and out[3] == 1
    assert out[4] == '3 times'
    assert out[7] == 'T3, T2, T1'                        # team holders are abbreviations
    span = L.Pool(rows).top('hld', 'desc', 1)[0]
    assert span.tie_first['unit'] == 1 and span.tie_last['unit'] == 3


def test_context_supplies_period_counts_per_band():
    ctx = L.Context(2026, None, 12, {}, lambda r, b: '', period_counts={'week': 40})
    assert ctx.period_count(_band()) == 40
    assert ctx.period_count(_band('level_cur', 'week', 'current')) == 40     # grain-wide
    assert ctx.period_count(_band('season_all', 'season', 'all', 'Season')) is None


# ---- section 2.17 (09-09): team figures per standard matchup ----------------

def test_per_matchup_rows_divide_counts_and_keep_raw_for_floors():
    rows = L.per_matchup_rows([_row(pts=220.0, hr=44, ab=300, pa=352, periods_played=22)],
                              lambda r: r['periods_played'], extra_cols=['pa'])
    row = rows[0]
    assert row['pts'] == 10.0 and row['hr'] == 2.0 and row['units'] == 22
    assert row['pa'] == 16.0 and row['raw']['pa'] == 352      # derived stats divide too
    assert row['raw']['ab'] == 300 and row['avg'] == pytest.approx(90 / 300)
    assert L.Pool(rows).top('b_so', 'asc', 1, floor='ab')      # 300 raw AB clear the floor
    hr = L.Metric('hr', 'Home Runs', 'count', 'hitting', 'desc', 'int')
    assert L.value_cell(hr, row['hr'], per_unit=True) == 2.0
    assert L.per_matchup_rows([_row()], lambda r: None) == []


def test_per_matchup_details_lead_with_raw_total_and_matchup_count():
    row = L.per_matchup_rows([_row(hr=44, periods_played=22)], lambda r: r['periods_played'])[0]
    m = L.Metric('hr', 'Home Runs', 'count', 'hitting', 'desc', 'int')
    ctx = L.Context(2026, None, 12, {}, lambda r, b: '', contributors=lambda r, mm: [('Judge', 20)])
    assert L._details_for(row, m, 'team', ctx) == '44 over 22 matchups · Judge: 20'


# ---- section 2.15 (Kyle, 09-09): recency marks ------------------------------

def _marks(sheet, n):
    hl = it = False
    for spec in sheet.formats:
        if 'format' in spec and spec['range'].endswith(f':F{n}'):
            hl |= spec['format'].get('backgroundColor') == L._HL
            it |= bool(spec['format'].get('textFormat', {}).get('italic'))
    return hl, it


def _rec(band, row, latest=(2026, 22)):
    sheet = L.Sheet()
    sheet.add(['x'])
    ctx = L.Context(2026, latest, 12, {}, lambda r, b: '')
    L._recency(sheet, 1, 0, L.RecordCell(1.0, [row], 1), band, ctx)
    return _marks(sheet, 1)


def test_this_season_band_marks_only_the_last_matchup_with_both():
    cur = _band('level_cur', 'week', 'current')
    assert _rec(cur, _row(season=2026, unit=22)) == (True, True)
    assert _rec(cur, _row(season=2026, unit=5)) == (False, False)


def test_all_time_band_highlights_this_season_and_italicizes_the_last_matchup():
    allt = _band('level_all', 'week', 'all')
    assert _rec(allt, _row(season=2026, unit=22)) == (True, True)
    assert _rec(allt, _row(season=2026, unit=5)) == (True, False)
    assert _rec(allt, _row(season=2024, unit=22)) == (False, False)
    day = _band('day_all', 'day', 'all')
    assert _rec(day, _row(season=2026, unit=150, mp=22)) == (True, True)
    assert _rec(day, _row(season=2026, unit=30, mp=5)) == (True, False)


def test_season_grain_boards_highlight_the_most_recent_season_only():
    anyb = _band('season_all', 'season', 'all', 'Season')
    assert _rec(anyb, _row(season=2026, unit=None)) == (True, False)
    assert _rec(anyb, _row(season=2025, unit=None)) == (False, False)
    cur = _band('season_cur', 'season', 'current', 'Runner-up')
    assert _rec(cur, _row(season=2026, unit=None)) == (False, False)


# ---- section 5 (09-09): the Lifetime tab's shape ----------------------------

def _lifetime_data():
    labels = {'1': 'ONE', '9999': '####'}

    def pl(pid, team, abbrev, pts, seasons=(2025, 2026), **kw):
        base = dict(_row(pid=pid, pname=pid, dname=pid, team_id=team, cid=team, abbrev=abbrev,
                         pts=pts, hit_pts=pts, pit_pts=0.0, hr=10, games=100),
                    seasons=set(seasons), by_team={team: pts}, team_labels=labels,
                    benched_hit=0, benched_pit=0, unrostered_hit=0, unrostered_pit=0,
                    neg_hit=0, neg_pit=0, **kw)
        return L.add_wasted(base)

    fr = {('a', '1'): pl('a', '1', 'ONE', 500.0), ('b', '9999'): pl('b', '9999', '####', 900.0)}
    league = {'a': pl('a', '1', 'ONE', 500.0), 'b': pl('b', '9999', '####', 900.0)}
    ps = [pl('a', '1', 'ONE', 300.0, seasons=(2026,), season=2026),
          pl('b', '9999', '####', 400.0, seasons=(2025,), season=2025)]
    team = [L.add_wasted(dict(_row(team_id='1', cid='1', abbrev='ONE', team_name='Team One',
                                   pts=5000.0, seasons={2025, 2026}), top_players={}))]
    avg = L.per_matchup_rows([dict(t) for t in team], lambda r: 44)
    return {'player_franchise': fr, 'player_league': league, 'player_season': ps,
            'shame': list(league.values()), 'team_total': team, 'team_avg': avg,
            'slot_franchise': {}, 'slot_league': {}, 'slot_season': [], 'per_matchup': True}


def test_lifetime_tab_drops_the_score_section_and_fences_the_sentinel_by_franchise():
    tab = L.derive_tabs('h2h', seasons_count=2)[-1]
    ctx = L.Context(2026, (2026, 22), 12, {'hr': 4.0}, lambda r, b: '')
    catalog = [{'key': 'hr', 'display_name': 'Home Runs', 'stat_category': 'hitting',
                'polarity': 'positive'}]
    sheet = L.build_lifetime_tab(tab, _lifetime_data(), ctx, catalog, [])
    labels = [r[0] for r in sheet.rows]
    assert 'Score Records' not in labels
    order = ['LEAGUE HALL OF FAME', 'FRANCHISE HALL OF FAME', 'Jump to:', 'PLAYER RECORDS',
             'Hitting Records', 'Pitching Records', 'Lineup Slot Records',
             'WASTED HALL OF SHAME', 'TEAM RECORDS', 'Team Score Records']
    idx = -1
    for label in order:
        idx = labels.index(label, idx + 1)
    league_hof = sheet.rows[labels.index('LEAGUE HALL OF FAME'):labels.index('FRANCHISE HALL OF FAME')]
    franchise_hof = sheet.rows[labels.index('FRANCHISE HALL OF FAME'):labels.index('Jump to:')]
    assert any('####' in str(c) for r in league_hof for c in r)
    assert not any('####' in str(c) for r in franchise_hof for c in r)
    text = '\n'.join('\t'.join(str(c) for c in r) for r in sheet.rows)
    assert "League's Top Point Producers -- All Teams, active-slots only" in text
    assert 'top 25 careers with given franchise' in text
    assert 'Years of Service' in text and 'Span' not in text and '2: 2025–2026' in text
    assert '\u2014' not in text and all(collapsed for _, _, collapsed in sheet.groups)
    assert 'Average per Matchup' in text and '5,000.0 over 44 matchups' in text
    hdr = next(r for r in sheet.rows if r[0] == 'Rank' and r[2] == 'Franchises')
    assert hdr[5] == 'Years of Service'
    jump = next(spec['jump'] for spec in sheet.formats if 'jump' in spec)
    assert [k for _, k in jump['items']] == ['l-hof', 'l-hit', 'l-pit', 'l-slot', 'l-hos', 'l-team']
