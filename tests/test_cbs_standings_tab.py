"""CBS Advanced Standings builder (2026-07-17): the finishes-matrix
dressing (trophy markers, the green->yellow->red rank gradient, the
hidden former-franchise row group), the slot/position points grids, and
the MLB affinity chart. Pure layout tests -- the franchise map is
monkeypatched, no warehouse."""
import pytest

import cbs_almanac_sheets as cbs


def _context():
    return {'season_year': 2026, 'latest_period': 16,
            'first_season': 2001, 'last_closed_season': 2025}


def _arc():
    def row(team_id, name, rank):
        return {'period': 16, 'team_id': team_id, 'team_name': name,
                'standings_rank': rank, 'points': 100.0 - rank,
                'period_points': 10.0, 'rank_change': 0,
                'points_behind_leader': float(rank - 1),
                'is_latest_period': True}
    return [row(1, 'Alpha', 1), row(2, 'Beta', 2)]


def _finishes():
    return [
        {'season_year': 2024, 'franchise_id': 1, 'team_name': 'Alpha',
         'division_name': 'East', 'standings_rank': 1, 'is_champion': True},
        {'season_year': 2024, 'franchise_id': 2, 'team_name': 'Beta',
         'division_name': 'West', 'standings_rank': 2, 'is_champion': False},
        {'season_year': 2025, 'franchise_id': 1, 'team_name': 'Alpha',
         'division_name': 'East', 'standings_rank': 2, 'is_champion': False},
        # A franchise that no longer exists -> the hidden former section.
        {'season_year': 2025, 'franchise_id': 9, 'team_name': 'Ghosts',
         'division_name': 'West', 'standings_rank': 1, 'is_champion': True},
    ]


def _franchises():
    return [{'team_id': 1, 'team_name': 'Alpha'},
            {'team_id': 2, 'team_name': 'Beta'}]


def _fmap():
    return {1: {'canonical_id': 1, 'name': 'Alpha', 'abbrev': 'ALP'},
            2: {'canonical_id': 2, 'name': 'Beta', 'abbrev': 'BET'},
            # 3 is a re-minted id of franchise 1 (the Foster's Folly class).
            3: {'canonical_id': 1, 'name': 'Alpha', 'abbrev': 'ALP'},
            9: {'canonical_id': 9, 'name': 'Ghosts', 'abbrev': 'GHO'}}


def _build(monkeypatch, **kwargs):
    monkeypatch.setattr(cbs, 'get_franchise_map', _fmap)
    return cbs.build_standings_rows(
        _context(), _arc(), _finishes(), _franchises(), **kwargs)


class TestFinishesMatrix:
    def test_tab_title_is_advanced_standings(self, monkeypatch):
        rows, _ = _build(monkeypatch)
        assert rows[0] == ['Advanced Standings']
        assert cbs.STANDINGS_TAB == 'Advanced Standings'

    def test_champions_render_trophies_with_scale_green_fill(self, monkeypatch):
        rows, formats = _build(monkeypatch)
        trophy_cells = [c for row in rows for c in row if c == '🏆']
        assert len(trophy_cells) == 2          # Alpha 2024 + Ghosts 2025
        fills = [s for s in formats
                 if s.get('format', {}).get('backgroundColor') == cbs._FINISH_GREEN]
        assert len(fills) == 2

    def test_finish_matrices_carry_rank_gradient_and_centering(self, monkeypatch):
        rows, formats = _build(monkeypatch)
        gradients = [s for s in formats if 'gradient' in s]
        # ONE auto-scaled rule per year COLUMN -- 2 closed seasons + the
        # in-flight column -- each spanning BOTH matrices.
        assert len(gradients) == 3
        for spec in gradients:
            assert len(spec['ranges']) == 2
            g = spec['gradient']
            assert g['minpoint'] == {'type': 'MIN', 'color': cbs._FINISH_GREEN}
            assert g['maxpoint'] == {'type': 'MAX', 'color': cbs._FINISH_RED}
        year_cols = sorted({r.split(':')[0].rstrip('0123456789')
                            for spec in gradients for r in spec['ranges']})
        assert len(year_cols) == 3
        centered = [s for s in formats
                    if s.get('format', {}).get('horizontalAlignment') == 'CENTER']
        assert len(centered) == 2

    def test_division_champions_get_green_borders(self, monkeypatch):
        rows, formats = _build(monkeypatch)
        borders = [s for s in formats if s.get('format', {}).get('borders')]
        # Alpha best-in-East 2024+2025, Beta best-in-West 2024, Ghosts
        # best-in-West 2025 -> four bordered cells.
        assert len(borders) == 4
        side = borders[0]['format']['borders']['top']
        assert side['color'] == {'red': 0.0, 'green': 1.0, 'blue': 0.0}

    def test_div_avg_columns_and_titles_then_avg_sort(self, monkeypatch):
        rows, _ = _build(monkeypatch)
        hdr_idx = rows.index(['Franchise', 'Titles', 'Div', 'Avg',
                              '2024', '2025', '2026'])
        alpha, beta = rows[hdr_idx + 1], rows[hdr_idx + 2]
        # Alpha: 1 title, 2 division titles (best-in-East both years),
        # avg finish (1+2)/2; sorts first on titles. The in-flight 2026
        # column shows the CURRENT rank as a plain number (1, no trophy)
        # and counts toward none of Titles/Div/Avg.
        assert alpha[:4] == ['Alpha', 1, 2, 1.5]
        assert alpha[4:] == ['🏆', 2, 1]
        # Beta: no titles, but best-in-West 2024 (Ghosts hadn't joined).
        assert beta[:4] == ['Beta', '', 1, 2.0]
        assert beta[6] == 2

    def test_former_franchises_fold_into_hidden_row_group(self, monkeypatch):
        rows, formats = _build(monkeypatch)
        navy_idx = next(i for i, r in enumerate(rows)
                        if r and str(r[0]).startswith('FORMER FRANCHISES'))
        (hide,) = [s['hide_rows'] for s in formats if 'hide_rows' in s]
        # Header + one defunct data row hide (0-based half-open); the navy
        # band itself stays visible as the expand cue.
        assert hide == (navy_idx + 1, navy_idx + 3)
        assert rows[navy_idx + 2][0] == 'Ghosts'


class TestSlotGrids:
    def test_alltime_paces_split_pitcher_and_capture_eras(self, monkeypatch):
        rows, formats = _build(
            monkeypatch,
            slot_rows=[{'team_id': 1, 'lineup_slot': 'C', 'slot_pts': 5.0}],
            alltime_slot_rows=[
                {'team_id': 1, 'lineup_slot': 'C', 'season_year': 2026,
                 'slot_pts': 10.0},
                # Re-minted member id rolls into the canonical franchise.
                {'team_id': 3, 'lineup_slot': 'C', 'season_year': 2026,
                 'slot_pts': 5.0},
            ],
            alltime_pitching_rows=[
                {'team_id': 1, 'p_pts': 500.0},
                {'team_id': 3, 'p_pts': 100.0},
            ],
            season_days=[
                {'season_year': 2024, 'days': 100},
                {'season_year': 2025, 'days': 100},
                {'season_year': 2026, 'days': 50},
            ],
        )
        # Both sides share the full slot vocabulary (Records alignment).
        hdr = rows.index(['Team', 'C', '1B', '2B', '3B', 'SS', 'OF', 'DH',
                          'U', 'P', '', 'C', '1B', '2B', '3B', 'SS', 'OF',
                          'DH', 'U', 'P'])
        alpha = rows[hdr + 1]
        assert alpha[0] == 'Alpha'
        assert alpha[1] == 5.0                 # season total, deployed slot
        # Standard season N = median closed days = 100. Hitter slots pace
        # over the CAPTURE era only (2026: 50 days -> 0.5 seasons):
        # (10 + 5) / 0.5.
        assert alpha[11] == 30.0
        # P paces over the franchise's FULL membership (2024+2025+2026 =
        # 250 days -> 2.5 seasons): (500 + 100) / 2.5.
        assert alpha[19] == 240.0
        beta = rows[hdr + 2]
        assert beta[0] == 'Beta' and beta[1] == ''
        assert beta[19] == ''                  # no pitching rows supplied
        # One gradient per value column (9 + 9) plus one per finish-year
        # column (2 closed + the in-flight 2026).
        assert len([s for s in formats if 'gradient' in s]) == 18 + 3


class TestAffinityChart:
    def test_columns_are_percent_shares_on_canonical_franchises(self, monkeypatch):
        rows, formats = _build(
            monkeypatch,
            affinity_rows=[
                {'team_id': 1, 'mlb_team_id': 108,
                 'mlb_team_name': 'Los Angeles Angels',
                 'season_wt': 30.0, 'alltime_wt': 60.0},
                # The re-minted member id: rolls into Alpha's column.
                {'team_id': 3, 'mlb_team_id': 108,
                 'mlb_team_name': 'Los Angeles Angels',
                 'season_wt': 0.0, 'alltime_wt': 40.0},
                {'team_id': 1, 'mlb_team_id': 147,
                 'mlb_team_name': 'New York Yankees',
                 'season_wt': 10.0, 'alltime_wt': 0.0},
                {'team_id': 2, 'mlb_team_id': 147,
                 'mlb_team_name': 'New York Yankees',
                 'season_wt': 5.0, 'alltime_wt': 25.0},
            ],
        )
        hdr = rows.index(['MLB Team', 'ALP', 'BET', '', 'ALP', 'BET'])
        angels = next(r for r in rows[hdr + 1:] if r[0] == 'Los Angeles Angels')
        yankees = next(r for r in rows[hdr + 1:] if r[0] == 'New York Yankees')
        # FRACTIONS (percent display comes from the number format): Alpha
        # season 30 + 10 games -> .75/.25; all-time (60+40) vs 0.
        assert angels == ['Los Angeles Angels', 0.75, '', '', 1.0, '']
        assert yankees == ['New York Yankees', 0.25, 1.0, '', '', 1.0]
        # Red -> WHITE -> green, one rule PER BLOCK (each matrix scales to
        # its own spread), anchored at 0 = the scale red.
        share = [s for s in formats
                 if 'gradient' in s
                 and s['gradient']['minpoint'].get('value') == '0']
        assert len(share) == 2
        for spec in share:
            assert 'range' in spec and 'ranges' not in spec
            assert spec['gradient']['minpoint']['color'] == cbs._SCALE_RED
            assert spec['gradient']['midpoint']['color'] == cbs._WHITE
            assert spec['gradient']['maxpoint']['color'] == cbs._SCALE_GREEN
        # Light-gray base (true zero/null reads as 'nothing here'), whole-
        # percent display, centered -- one spec per block.
        bases = [s for s in formats
                 if s.get('format', {}).get('backgroundColor') == cbs._LIGHT_GRAY
                 and s['format'].get('numberFormat', {}).get('pattern') == '0%'
                 and s['format'].get('horizontalAlignment') == 'CENTER']
        assert len(bases) == 2
        # Each club's biggest devotee bolds per block: single-cell bold
        # specs for Angels(ALP season + ALP all-time) and Yankees(BET x2).
        cell_bolds = [s for s in formats
                      if s.get('format') == {'textFormat': {'bold': True}}
                      and ':' in s['range']
                      and s['range'].split(':')[0] == s['range'].split(':')[1]]
        assert len(cell_bolds) == 4

    def test_affinity_absent_without_rows(self, monkeypatch):
        rows, _ = _build(monkeypatch)
        assert not any(r and r[0] == 'MLB Affinity Chart' for r in rows)


class TestRankChart:
    def test_toggles_helper_and_chart_spec(self, monkeypatch):
        rows, formats = _build(monkeypatch)
        # Round 7: the snapshot table AND the visible rank matrix are both
        # gone -- the chart is the section, fed by the hidden helper.
        assert not any(r and 'STANDINGS —' in str(r[0]) for r in rows)
        assert ['Team', 'P16'] not in rows
        chk_idx = next(i for i, r in enumerate(rows)
                       if r and r[0] == '(check to plot)')
        # Kyle's default scheme: individual boxes OFF, the ALL master ON
        # -- uncheck ALL, check one team, see one line.
        assert rows[chk_idx][1:] == [False, False, True]
        assert rows[chk_idx - 1][:4] == ['Chart teams:', 'ALP', 'BET', 'ALL']
        (chk,) = [s['checkboxes'] for s in formats if 'checkboxes' in s]
        assert chk == f'B{chk_idx + 1}:D{chk_idx + 1}'
        (chart,) = [s['chart'] for s in formats if 'chart' in s]
        assert len(chart['series_cols']) == 2
        assert chart['view_max'] == 3          # n_teams + 1 rank flip
        # Hidden block = Period + 2 formula cols + 2 raw-rank cols.
        (hide_cols,) = [s['hide_cols'] for s in formats if 'hide_cols' in s]
        assert hide_cols == (36, 41)
        helper_hdr = rows[chart['first_row']]
        assert helper_hdr[36:] == ['Period', 'ALP', 'BET', 'ALP', 'BET']
        data = rows[chart['first_row'] + 1]
        # Formulas gate on OR(ALL, own box) and read the SAME-ROW raw
        # ranks stored to their right; the raw values are plain ints.
        assert data[37].startswith('=IF(AND(OR($D$')
        assert '3-' in data[37] and data[37].endswith('NA())')
        assert data[39:41] == [1, 2]           # Alpha rank 1, Beta rank 2


class TestDetailedStandings:
    def test_alltime_paces_on_the_franchise_spine(self, monkeypatch):
        rows, _ = _build(
            monkeypatch,
            season_days=[{'season_year': 2024, 'days': 100},
                         {'season_year': 2025, 'days': 100},
                         {'season_year': 2026, 'days': 50}],
            detailed_alltime_rows=[
                {'team_id': 1, 'h': 100.0, 'hr': 10.0, 'hit_pts': 500.0,
                 'k': 200.0, 'outs': 300.0, 'pit_pts': 400.0,
                 'total_pts': 900.0},
                # Re-minted member id rolls into the canonical franchise.
                {'team_id': 3, 'hit_pts': 100.0, 'total_pts': 100.0},
            ])
        hdr_idx = next(i for i, r in enumerate(rows)
                       if r and r[0] == 'Franchise' and 'Hit Pts' in r)
        hdr = rows[hdr_idx]
        assert hdr[:3] == ['Franchise', 'H', '2B']
        assert 'IP' in hdr and 'BB' in hdr and 'Total' in hdr
        alpha = rows[hdr_idx + 1]
        # Alpha membership = (100+100+50)/100 = 2.5 season-equivalents.
        assert alpha[0] == 'Alpha'
        assert alpha[1] == 40.0                     # H pace 100/2.5
        assert alpha[hdr.index('IP')] == 40.0       # outs 300 -> 100 IP
        assert alpha[hdr.index('Total')] == 400.0   # (900+100)/2.5
        beta = rows[hdr_idx + 2]
        # A franchise with no attributed rows paces at zero, not blank
        # (real zeros are legitimate in a paced table).
        assert beta[0] == 'Beta' and beta[1] == 0.0


class TestAcquisitionBlocks:
    @staticmethod
    def _acq_row(team_id, **vals):
        row = {'team_id': team_id}
        for lens in ('active', 'rostered'):
            for k in ('opening', 'fa_add', 'trade', 'acquired', 'dropped',
                      'traded_away', 'lost'):
                row[f'{k}_{lens}_pts'] = 0.0
        row.update(vals)
        return row

    _HALF = ['Opening', 'Pickup', 'Trade', 'Total', '',
             'Release', 'Trade', 'Total', '', 'FA', 'Trade']

    def test_lenses_rank_rollup_and_nets(self, monkeypatch):
        acq = [
            self._acq_row(1, opening_active_pts=100.0,
                          fa_add_active_pts=50.0,
                          acquired_active_pts=150.0,
                          dropped_active_pts=20.0, lost_active_pts=20.0),
            # Re-minted member id: rolls into Alpha's canonical row.
            self._acq_row(3, opening_active_pts=40.0,
                          acquired_active_pts=40.0),
            self._acq_row(2, trade_active_pts=300.0,
                          acquired_active_pts=300.0,
                          traded_away_active_pts=10.0,
                          lost_active_pts=10.0),
        ]
        rows, formats = _build(monkeypatch, acquisition_rows=acq)

        labels = [r[0] for r in rows if r and str(r[0]).startswith(
            ('Active Lens', 'Rostered Lens'))]
        assert len(labels) == 2
        hdr_idx = rows.index(['Team', *self._HALF, '', *self._HALF])
        beta, alpha = rows[hdr_idx + 1], rows[hdr_idx + 2]
        # Ranked by the SEASON half's Total: Beta (300) over Alpha (190).
        assert beta[0] == 'Beta' and beta[4] == 300.0
        assert alpha[0] == 'Alpha'
        assert alpha[1] == 140.0               # 100 + member id 3's 40
        assert alpha[2] == 50.0                # Pickup (FA) before Trade
        assert alpha[4] == 190.0
        assert alpha[10] == 30.0               # Net FA = 50 - 20 released
        assert beta[11] == 290.0               # Net Trade = 300 - 10
        # No all-time rows -> the right half renders blank.
        assert alpha[13:24] == [''] * 11
        # Gradient polarity: 9 per lens season-half (no all-time half),
        # on top of the 3 finish-year rules.
        acq_gradients = [s for s in formats if 'gradient' in s]
        assert len(acq_gradients) == 9 * 2 + 3
        # The band row carries the group labels over both halves.
        band = next(r for r in rows
                    if len(r) > 13 and r[1] == 'Points Acquired Via')
        assert band[6] == 'Points Lost Via'
        assert band[10] == 'Net Points via'
        assert band[13] == 'Points Acquired Via'
        # Band groups merge (3 groups x 2 halves).
        merges = [s for s in formats if s.get('merge')]
        assert len(merges) == 6 * 2            # per lens table

    def test_alltime_mirror_shares_the_row_spine(self, monkeypatch):
        season = [self._acq_row(1, opening_active_pts=100.0,
                                acquired_active_pts=100.0)]
        hist = [self._acq_row(1, opening_active_pts=900.0,
                              trade_active_pts=50.0,
                              acquired_active_pts=950.0),
                # Defunct franchise: bucketed but never rendered (the
                # spine is the ACTIVE canonical franchises).
                self._acq_row(9, acquired_active_pts=500.0)]
        rows, _ = _build(monkeypatch, acquisition_rows=season,
                         alltime_acquisition_rows=hist)

        # MLB-142 round 2: the era scopes ride the navy banner once for
        # both lens tables; the per-table era rows are gone.
        banner = next(r for r in rows
                      if r and r[0] == 'PRODUCTION BY ACQUISITION CHANNEL')
        assert banner[2] == 'Current Season'
        assert banner[13] == 'All-Time (2001-2026)'
        hdr_idx = rows.index(['Team', *self._HALF, '', *self._HALF])
        alpha = rows[hdr_idx + 1]
        assert alpha[0] == 'Alpha'
        # Season half left; all-time half right = history + this season.
        assert alpha[1] == 100.0 and alpha[4] == 100.0
        assert alpha[13] == 1000.0             # 900 + 100 opening
        assert alpha[15] == 50.0               # trade, history only
        assert alpha[16] == 1050.0             # total
        assert not any(r and r[0] == 'Ghosts' for r in rows[hdr_idx:])
