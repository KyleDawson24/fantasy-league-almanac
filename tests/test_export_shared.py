"""The shared almanac export (MLB-301): pure shapers, the presentation
descriptor, and one end-to-end run on the frozen corpus.

The shapers are pinned against hand-built warehouse rows so the contract
(raw total + denominator kind and value + the printed number) is asserted
without a database. The printed numbers are checked against the writer's
own helpers rather than a re-statement of the formula, which is the whole
point of the export: one arithmetic, two consumers.
"""

import json

import pytest

import almanac_render
import export_shared as ex


def _spec(stat_name, category, ppu, abbrev=None):
    return {'stat_name': stat_name, 'stat_category': category,
            'points_per_unit': ppu, 'abbrev': abbrev or stat_name,
            'display_name': stat_name}


HITTING = [_spec('HR', 'hitting', 4.0), _spec('B_SO', 'hitting', -1.0, 'K'),
           _spec('AB', 'hitting', 0.0)]
PITCHING = [_spec('K', 'pitching', 1.0), _spec('OUTS', 'pitching', 0.67)]


def _standings_row(team_id=1, abbrev='SMEL', days=163, std=7, **stats):
    row = {'team_id': team_id, 'team_abbrev': abbrev, 'team_name': f'Team {team_id}',
           'owner_display': 'Owner', 'wins': 19, 'losses': 3, 'ties': 0,
           'matchup_periods_played': 22, 'scoring_days_played': days,
           'standard_matchup_days': std,
           'calculated_hitting_pts': 4000.0, 'calculated_pitching_pts': 3453.5,
           'calculated_points': 7453.5, 'against_calculated_points': 6325.6,
           'hr': 210, 'b_so': 1100, 'ab': 5200, 'k': 1592, 'outs': 4908}
    row.update(stats)
    return row


def test_standings_rows_carry_raw_denominator_and_the_writers_printed_value():
    rows = ex.standings_long_rows('2026', 2026, [_standings_row()], HITTING, PITCHING)
    keys = [r['metric_key'] for r in rows]
    assert keys == ['HR', 'B_SO', 'AB', 'OFFENSE', 'K', 'OUTS', 'DEFENSE', 'TOTAL', 'AGAINST']
    by_key = {r['metric_key']: r for r in rows}
    total = by_key['TOTAL']
    assert total['raw_total'] == 7453.5
    assert total['denominator_kind'] == 'scoring_days_played'
    assert total['denominator_value'] == 163 and total['standard_matchup_days'] == 7
    assert total['per_matchup_value'] == round(7453.5 * 7 / 163, 1) == 320.1
    assert total['per_matchup_value'] == almanac_render._per_week_value(_standings_row(), 7453.5)
    assert total['scoring_lens'] == 'calculated' and total['polarity'] == 1
    assert by_key['AGAINST']['polarity'] == -1
    # A zero-weighted stat is ungraded; a negative-weighted one is green-low.
    assert by_key['AB']['polarity'] == 0 and by_key['AB']['palette'] is None
    assert by_key['B_SO']['polarity'] == -1 and by_key['B_SO']['display_label'] == 'K'
    # OUTS prints as innings (divided by 3 first), the raw total stays outs.
    outs = by_key['OUTS']
    assert outs['raw_total'] == 4908 and outs['raw_unit'] == 'outs'
    assert outs['display_label'] == 'IP'
    assert outs['per_matchup_value'] == round(4908 / 3 * 7 / 163, 1) == 70.3
    assert rows[0]['record'] == '19-3' and rows[0]['rank'] == 1


def test_standings_row_without_denominator_prints_nothing():
    rows = ex.standings_long_rows('2026', 2026, [_standings_row(days=None)], HITTING, PITCHING)
    assert all(r['per_matchup_value'] is None for r in rows)
    assert all(r['raw_total'] is not None for r in rows)


def test_slot_rows_divide_by_matchups_played_not_scoring_days():
    slots = [{'team_id': 5, 'lineup_slot': 'RF', 'slot_pts': 403.3, 'sort_order': 90},
             {'team_id': 5, 'lineup_slot': 'RP', 'slot_pts': 1317.3, 'sort_order': 150}]
    rows = ex.slot_long_rows('2026', 2026, slots, {5: 22}, {5: {'team_abbrev': 'FUBB', 'owner_display': 'O'}})
    rf, rp = rows
    assert rf['denominator_kind'] == 'matchup_periods_played' and rf['denominator_value'] == 22
    assert rf['per_matchup_value'] == round(403.3 / 22, 1) == 18.3
    assert rp['per_matchup_value'] == 59.9 and rp['raw_total'] == 1317.3


def test_slot_rows_refuse_to_disagree_with_the_writers_printed_value():
    slots = [{'team_id': 5, 'lineup_slot': 'RP', 'slot_pts': 2292.6, 'sort_order': 150}]
    ok = ex.slot_long_rows('all_time', None, slots, {5: 45}, {}, printed_by_key={(5, 'RP'): 50.9})
    assert ok[0]['per_matchup_value'] == 50.9
    with pytest.raises(ex.ExportError):
        ex.slot_long_rows('all_time', None, slots, {5: 45}, {}, printed_by_key={(5, 'RP'): 51.0})


def test_acquisition_rows_have_no_denominator_and_keep_the_writers_rounding():
    acq = [{'team_id': 1, 'team_abbrev': 'A', 'owner_display': 'a',
            'keeper_active_pts': 10.04, 'draft_active_pts': 0, 'fa_add_active_pts': 0,
            'trade_active_pts': 0, 'acquired_active_pts': 10.04, 'dropped_active_pts': 1,
            'traded_away_active_pts': 0, 'lost_active_pts': 1, 'fa_delta_active_pts': -1.0,
            'trade_delta_active_pts': 0,
            'keeper_rostered_pts': 20, 'draft_rostered_pts': 0, 'fa_add_rostered_pts': 0,
            'trade_rostered_pts': 0, 'acquired_rostered_pts': 20, 'dropped_rostered_pts': 0,
            'traded_away_rostered_pts': 0, 'lost_rostered_pts': 0, 'fa_delta_rostered_pts': 0,
            'trade_delta_rostered_pts': 0},
           {'team_id': 2, 'team_abbrev': 'B', 'owner_display': 'b',
            **{f'{c}_{s}': 30.0 if c in ('keeper', 'acquired') else 0
               for c in ('keeper', 'draft', 'fa_add', 'trade', 'acquired', 'dropped',
                         'traded_away', 'lost', 'fa_delta', 'trade_delta')
               for s in ('active_pts', 'rostered_pts')}}]
    rows = ex.acquisition_long_rows('2026', 2026, acq)
    assert len(rows) == 2 * 2 * 10
    keeper = next(r for r in rows if r['team_id'] == 1 and r['lens'] == 'active' and r['channel_key'] == 'keeper')
    assert keeper['raw_total'] == 10.04 and keeper['printed_value'] == 10.0
    assert keeper['denominator_kind'] == 'none' and keeper['denominator_value'] is None
    assert keeper['polarity'] == 1 and keeper['palette'] == 'three_good_high'
    lost = next(r for r in rows if r['team_id'] == 1 and r['lens'] == 'active' and r['channel_key'] == 'lost')
    assert lost['polarity'] == -1 and lost['palette'] == 'three_good_low'
    net = next(r for r in rows if r['team_id'] == 1 and r['lens'] == 'active' and r['channel_key'] == 'fa_delta')
    assert net['palette'] == 'diverging_zero'
    # The block is ranked by Acquired total under each lens (B leads on active).
    assert next(r for r in rows if r['team_id'] == 2 and r['lens'] == 'active')['lens_rank'] == 1
    assert next(r for r in rows if r['team_id'] == 1 and r['lens'] == 'rostered')['lens_rank'] == 2


def test_affinity_rows_share_denominator_and_row_max_with_ties():
    standings = [{'team_id': 1, 'team_abbrev': 'A', 'owner_display': 'a'},
                 {'team_id': 2, 'team_abbrev': 'B', 'owner_display': 'b'}]
    affinity = [{'team_id': 1, 'pro_team': 'NYY', 'season_wt': 50, 'alltime_wt': 50},
                {'team_id': 1, 'pro_team': 'Bos', 'season_wt': 50, 'alltime_wt': 150},
                {'team_id': 2, 'pro_team': 'NYY', 'season_wt': 10, 'alltime_wt': 10},
                {'team_id': 2, 'pro_team': 'Bos', 'season_wt': 10, 'alltime_wt': 90},
                {'team_id': 9, 'pro_team': 'NYY', 'season_wt': 999, 'alltime_wt': 999}]  # not a standings team
    rows = ex.affinity_long_rows(affinity, standings)
    season = {(r['club_name'], r['team_abbrev']): r for r in rows if r['scope'] == 'season'}
    assert season[('New York Yankees', 'A')]['share'] == 0.5
    assert season[('New York Yankees', 'A')]['denominator_value'] == 100
    assert season[('New York Yankees', 'A')]['denominator_kind'] == 'team_involvement_weight'
    # Both teams sit at 0.5 for the Yankees this season: a tie, both bold.
    assert season[('New York Yankees', 'A')]['is_row_max'] and season[('New York Yankees', 'B')]['is_row_max']
    alltime = {(r['club_name'], r['team_abbrev']): r for r in rows if r['scope'] == 'all_time'}
    # All-time the Red Sox row belongs to B (0.9 beats 0.75); the Yankees row to A.
    assert alltime[('Boston Red Sox', 'A')]['share'] == 0.75 and not alltime[('Boston Red Sox', 'A')]['is_row_max']
    assert alltime[('Boston Red Sox', 'B')]['share'] == 0.9 and alltime[('Boston Red Sox', 'B')]['is_row_max']
    assert alltime[('New York Yankees', 'A')]['is_row_max'] and not alltime[('New York Yankees', 'B')]['is_row_max']
    assert all(r['team_id'] != 9 for r in rows)
    assert [r['club_name'] for r in rows if r['scope'] == 'season'][:2] == ['Boston Red Sox', 'Boston Red Sox']


def test_finish_rows_and_summary_follow_the_builder():
    finishes = [
        {'season_year': 2025, 'team_id': 1, 'team_abbrev': 'A', 'owner_display': 'a',
         'wins': 14, 'losses': 9, 'ties': 0, 'finish': 7, 'final_rank': 1, 'is_champion': True},
        {'season_year': 2025, 'team_id': 2, 'team_abbrev': 'B', 'owner_display': 'b',
         'wins': 16, 'losses': 7, 'ties': 0, 'finish': 1, 'final_rank': 2, 'is_champion': False},
        {'season_year': 2026, 'team_id': 1, 'team_abbrev': 'A', 'owner_display': 'a',
         'wins': 10, 'losses': 12, 'ties': 0, 'finish': 8, 'final_rank': None, 'is_champion': False},
        {'season_year': 2026, 'team_id': 2, 'team_abbrev': 'B', 'owner_display': 'b',
         'wins': 12, 'losses': 10, 'ties': 0, 'finish': 3, 'final_rank': None, 'is_champion': False},
    ]
    rows = ex.finish_rows(finishes, 2026)
    cells = {(r['season_year'], r['team_abbrev']): r['printed_cell'] for r in rows}
    assert cells[(2025, 'A')] == '🏆 7' and cells[(2025, 'B')] == '🥈 1'
    assert cells[(2026, 'A')] == 8 and cells[(2026, 'B')] == 3
    standings = [{'team_id': 2, 'team_abbrev': 'B', 'owner_display': 'b'},
                 {'team_id': 1, 'team_abbrev': 'A', 'owner_display': 'a'}]
    summary = ex.finish_summary_rows(finishes, standings, 2026, rank_arc_rows=[])
    assert [s['team_abbrev'] for s in summary] == ['A', 'B']          # titles first
    a, b = summary
    assert a['titles'] == 1 and a['win_pct'] == round(24 / 45, 3) and a['avg_finish'] == 7.5
    assert b['titles'] == 0 and b['win_pct'] == round(28 / 45, 3) and b['avg_finish'] == 2.0
    assert a['current_season_rank'] == 8 and b['current_season_rank'] == 3


def test_all_play_summary_sums_shares_per_matchup():
    mart = [
        {'season_year': 2025, 'matchup_period': 1, 'team_id': 1, 'team_abbrev': 'A', 'team_name': 'Alpha',
         'all_play_wins': 3, 'all_play_losses': 0, 'all_play_ties': 0,
         'expected_win_share': 1.0, 'expected_loss_share': 0.0, 'expected_tie_share': 0.0},
        {'season_year': 2026, 'matchup_period': 1, 'team_id': 1, 'team_abbrev': 'A2', 'team_name': 'Alpha II',
         'all_play_wins': 4, 'all_play_losses': 0, 'all_play_ties': 1,
         'expected_win_share': 0.8, 'expected_loss_share': 0.0, 'expected_tie_share': 0.2},
    ]
    rows = {r['scope']: r for r in ex.all_play_summary_rows(mart)}
    assert rows['2025']['all_play_record'] == '3-0' and rows['2025']['expected_record_printed'] == '1.0-0.0'
    assert rows['2026']['all_play_record'] == '4-0-1' and rows['2026']['expected_record_printed'] == '0.8-0.0-0.2'
    at = rows['all_time']
    assert at['matchups'] == 2 and at['expected_wins'] == pytest.approx(1.8)
    assert at['expected_win_pct'] == pytest.approx(0.9) and at['expected_win_pct_printed'] == '90.0%'
    assert at['team_abbrev'] == 'A2'                                     # latest label
    assert at['scoring_lens'] == 'platform'


def test_presentation_descriptor_is_generated_from_the_writers_gradients():
    d = ex.presentation_descriptor(HITTING, PITCHING)
    metrics = d['tables']['detailed_standings']['metrics']
    assert [m['metric_key'] for m in metrics] == ['HR', 'B_SO', 'AB', 'OFFENSE', 'K', 'OUTS', 'DEFENSE', 'TOTAL', 'AGAINST']
    by_key = {m['metric_key']: m for m in metrics}
    # Two 'K' labels stay distinct by stat key and category.
    assert by_key['B_SO']['display_label'] == 'K' and by_key['B_SO']['polarity'] == -1
    assert by_key['K']['display_label'] == 'K' and by_key['K']['polarity'] == 1
    assert by_key['AB']['polarity'] == 0 and by_key['AB']['palette'] is None
    assert by_key['AGAINST']['polarity'] == -1 and by_key['AGAINST']['palette'] == 'three_good_low'
    # The column index is the writer's, checked against standings_header.
    header = almanac_render.standings_header(HITTING, PITCHING)
    for m in metrics:
        assert header[m['sheet_column_index']] == m['display_label']
    channels = d['tables']['acquisition_channels']['channels']
    assert len(channels) == 20 and {c['half'] for c in channels} == {'season', 'all_time'}
    assert {c['channel_key']: c['palette'] for c in channels if c['half'] == 'season'} == {
        'keeper': 'three_good_high', 'draft': 'three_good_high', 'fa_add': 'three_good_high',
        'trade': 'three_good_high', 'acquired': 'three_good_high',
        'dropped': 'three_good_low', 'traded_away': 'three_good_low', 'lost': 'three_good_low',
        'fa_delta': 'diverging_zero', 'trade_delta': 'diverging_zero'}
    assert d['tables']['season_finishes']['palette'] == 'finish'
    assert d['tables']['affinities']['bold_row_max'] and d['tables']['affinities']['bold_ties']
    assert set(d['palettes']) == {'three_good_high', 'three_good_low', 'diverging_zero', 'finish', 'affinity_block'}
    json.dumps(d)                                                        # serializable as data


def test_descriptor_refuses_a_misaligned_walk(monkeypatch):
    monkeypatch.setattr(almanac_render, 'standings_gradient_columns',
                        lambda h, p: [(4, 'most')])
    with pytest.raises(ex.ExportError):
        ex.presentation_descriptor(HITTING, PITCHING)


@pytest.mark.warehouse
def test_export_runs_end_to_end_on_the_frozen_corpus(corpus_db, tmp_path):
    """The whole export off the frozen wk22 fixture: the acceptance numbers
    for all-play, the two denominators side by side, a deterministic
    manifest, and a byte-identical re-run."""
    manifest, out_dir = ex.run_export('espn-main', duckdb_path=str(corpus_db),
                                      out_root=str(tmp_path), snapshot_ts='t1')
    assert manifest['performance_cutoff'] == '2026-09-06'
    assert manifest['source']['input_fixture']['freeze_id'] == 'wk22-2026'
    summary = json.loads((out_dir / 'all_play_summary.json').read_text(encoding='utf-8'))['rows']
    smel = next(r for r in summary if r['scope'] == '2026' and r['team_abbrev'] == 'SMEL')
    assert smel['all_play_record'] == '225-61'
    assert smel['expected_record_printed'] == '17.3-4.7'
    assert smel['expected_win_pct_printed'] == '78.7%'
    kinds = {name: entry['denominator_kinds'][0] for name, entry in manifest['tables'].items()
             if 'denominator_kinds' in entry}
    assert kinds['detailed_standings'] == 'scoring_days_played'
    assert kinds['points_by_lineup_slot'] == 'matchup_periods_played'
    assert kinds['acquisition_channels'] == 'none'
    first = {p.name: p.read_bytes() for p in out_dir.iterdir()}
    _, again = ex.run_export('espn-main', duckdb_path=str(corpus_db),
                             out_root=str(tmp_path / 'again'), snapshot_ts='t1')
    assert {p.name: p.read_bytes() for p in again.iterdir()} == first
