"""CBS Season History builder (MLB-163): the Outscored / Outscored By
counts that stand in for W-L in a league that never plays a matchup, and
the tab's layout. Pure tests -- rows are handed in, no warehouse.

The counting rule is Kyle's (2026-07-31): an exact tie counts in NEITHER
column, so the integrity check is `outscored + outscored_by + ties = N-1`
and NOT `outscored + outscored_by = N-1`. He called that trap before the
build existed, and the league's own data contains it -- 2024 has two
teams on 9156 apiece. TestTies is that case; it is the reason the naive
check is not good enough, so it is not a hypothetical fixture.
"""
import pytest

import cbs_almanac_sheets as cbs


def _row(fid, name, rank, total, bat=None, pit=None, behind=0,
         champion=False, teams=None, season=2024):
    """One team-season as get_historic_finishes returns it."""
    bat = total / 2 if bat is None else bat
    return {'season_year': season, 'franchise_id': fid, 'team_name': name,
            'division_name': 'East', 'standings_rank': rank,
            'is_champion': champion, 'batting_points': bat,
            'pitching_points': total - bat if pit is None else pit,
            'total_points': total, 'points_behind': behind,
            'teams_in_season': teams}


def _clean_season(n=4):
    """N teams, no ties, ranks 1..N."""
    return [_row(i, f'Team {i}', i, 1000.0 - i, teams=n)
            for i in range(1, n + 1)]


def _tied_season():
    """The 2024 shape: two teams share second on an exact total, and the
    platform awards them joint rank 2 with the next team at rank 4."""
    return [
        _row(1, 'Alpha', 1, 9448.0, teams=4),
        _row(2, 'Mesa Javelinas', 2, 9156.0, teams=4),
        _row(3, 'Baltic White Sox', 2, 9156.0, teams=4),
        _row(4, 'Delta', 4, 8025.0, teams=4),
    ]


def _context():
    return {'season_year': 2026, 'latest_period': 16,
            'first_season': 2001, 'last_closed_season': 2025}


def _fmap():
    return {1: {'canonical_id': 1, 'name': 'Alpha Canonical', 'abbrev': 'ALP'},
            2: {'canonical_id': 2, 'name': 'Mesa Javelinas', 'abbrev': 'MJ'},
            3: {'canonical_id': 3, 'name': 'Baltic White Sox', 'abbrev': 'BWS'},
            4: {'canonical_id': 4, 'name': 'Delta', 'abbrev': 'DEL'}}


def _stats(finishes, **overrides):
    """A team-season stat row per finish, as _rec_agg returns them
    (lowercased stat_name keys). Values scale with franchise_id so the
    record marks land somewhere predictable."""
    out = []
    for r in finishes:
        row = {'season_year': r['season_year'], 'team_id': r['franchise_id']}
        for stat in (cbs._SEASON_HISTORY_HIT_STATS
                     + cbs._SEASON_HISTORY_PIT_STATS):
            row[stat.lower()] = 100.0 - r['franchise_id']
        row.update(overrides)
        out.append(row)
    return out


def _build(finishes, team_stats=None, owners=None):
    return cbs.build_season_history_rows(
        _context(), finishes, _fmap(),
        team_stats=_stats(finishes) if team_stats is None else team_stats,
        owners=owners)


def _col0(name):
    """0-based index of a header column, so tests name columns rather
    than counting them."""
    return cbs._season_history_header().index(name)


def _data_rows(rows):
    """Rows below the header band (title, subtitle, explainer, blank,
    header)."""
    header = next(i for i, r in enumerate(rows)
                  if r and r[0] == 'Season')
    return rows[header + 1:]


class TestInvariant:
    """outscored + outscored_by + ties == N - 1, always."""

    @pytest.mark.parametrize('n', [2, 4, 12, 15, 16])
    def test_holds_without_ties(self, n):
        rows = _clean_season(n)
        for outscored, outscored_by, ties in cbs.season_outscored_counts(rows):
            assert outscored + outscored_by + ties == n - 1
            assert ties == 0

    def test_holds_with_ties(self):
        rows = _tied_season()
        counts = cbs.season_outscored_counts(rows)
        for outscored, outscored_by, ties in counts:
            assert outscored + outscored_by + ties == len(rows) - 1

    def test_naive_check_would_fail_on_the_tie(self):
        """The trap, asserted as a trap: dropping Ties from the sum breaks
        on exactly the tied rows and on nothing else."""
        counts = cbs.season_outscored_counts(_tied_season())
        naive_ok = [o + ob == len(counts) - 1 for o, ob, _t in counts]
        assert naive_ok == [True, False, False, True]

    def test_holds_on_every_rendered_row(self):
        """The invariant over the built tab, not just the helper. Checked
        against the season's ACTUAL field size rather than a displayed
        column, so it also catches a season leaking rows across the
        boundary -- the counting is per-season and nothing on the tab
        would show a leak. Two seasons of different shapes, one tied."""
        older = [_row(i, f'Old {i}', i, 800.0 - i, teams=5, season=2001)
                 for i in range(1, 6)]
        rows, _ = _build(_tied_season() + older)
        o, ob, t = (_col0('Outscored'), _col0('Outscored By'), _col0('Ties'))
        by_season = {}
        for row in _data_rows(rows):
            by_season.setdefault(row[0], []).append(row)
        assert {yr: len(rs) for yr, rs in by_season.items()} == {2024: 4,
                                                                2001: 5}
        for season_rows in by_season.values():
            for row in season_rows:
                assert row[o] + row[ob] + row[t] == len(season_rows) - 1


class TestTies:
    def test_tie_counts_in_neither_column(self):
        counts = cbs.season_outscored_counts(_tied_season())
        assert counts[1] == (1, 1, 1)      # Mesa Javelinas
        assert counts[2] == (1, 1, 1)      # Baltic White Sox

    def test_untied_teams_see_the_tied_pair_normally(self):
        counts = cbs.season_outscored_counts(_tied_season())
        assert counts[0] == (3, 0, 0)      # leader outscored all three
        assert counts[3] == (0, 3, 0)      # last outscored by all three

    def test_winner_of_a_16_team_season_reads_15_0(self):
        """Kyle's worked example, at this league's actual size."""
        outscored, outscored_by, ties = cbs.season_outscored_counts(
            _clean_season(16))[0]
        assert (outscored, outscored_by, ties) == (15, 0, 0)

    def test_all_tied_season_is_all_ties(self):
        rows = [_row(i, f'Team {i}', 1, 500.0, teams=4) for i in range(1, 5)]
        assert cbs.season_outscored_counts(rows) == [(0, 0, 3)] * 4


class TestComparisonIsExact:
    def test_near_miss_is_not_a_tie(self):
        """No tolerance: the smallest representable difference still
        separates two teams."""
        rows = [_row(1, 'A', 1, 9156.0, teams=2),
                _row(2, 'B', 2, 9156.000000000002, teams=2)]
        assert cbs.season_outscored_counts(rows) == [(0, 1, 0), (1, 0, 0)]

    def test_counts_do_not_inherit_row_order(self):
        """Same season shuffled -> same answer per team."""
        rows = _tied_season()
        forward = dict(zip([r['franchise_id'] for r in rows],
                           cbs.season_outscored_counts(rows)))
        reversed_rows = list(reversed(rows))
        backward = dict(zip([r['franchise_id'] for r in reversed_rows],
                            cbs.season_outscored_counts(reversed_rows)))
        assert forward == backward

    def test_a_null_total_raises_rather_than_miscounting(self):
        rows = _clean_season(3)
        rows[1]['total_points'] = None
        with pytest.raises(TypeError):
            cbs.season_outscored_counts(rows)


class TestLayout:
    def test_title_and_header(self):
        rows, _ = _build(_clean_season(4))
        assert rows[0] == ['Season History']
        assert rows[4] == cbs._season_history_header()

    def test_column_order_matches_the_ruled_layout(self):
        """Kyle's spec, 2026-08-05, blank cells being the block buffers."""
        header = cbs._season_history_header()
        assert header[:6] == ['Season', 'Franchise', 'Owner(s)', 'Finish',
                              'Margin', '']
        assert header[cbs._SH_PIT0 - 1] == ''
        assert header[cbs._SH_PTS0 - 1:cbs._SH_PTS0 + 4] == [
            '', 'Hitting Points', 'Pitching Points', 'Total Points', '']
        assert header[cbs._SH_COUNTS0:] == [
            'Outscored', 'Outscored By', 'Ties', '',
            'League Avg Hitting', 'League Avg Pitching', 'League Avg Total']

    def test_every_row_is_the_full_width(self):
        """A short row silently shifts every column right of it."""
        rows, _ = _build(_clean_season(4))
        assert len(cbs._season_history_header()) == cbs._SEASON_HISTORY_WIDTH
        for row in _data_rows(rows):
            assert len(row) == cbs._SEASON_HISTORY_WIDTH

    def test_stat_blocks_carry_the_scored_set(self):
        header = cbs._season_history_header()
        hitting = header[cbs._SH_HIT0:cbs._SH_PIT0 - 1]
        pitching = header[cbs._SH_PIT0:cbs._SH_PTS0 - 1]
        assert hitting == ['H', '2B', '3B', 'HR', 'XBH', 'TB', 'R', 'RBI',
                           'SB', 'B_BB']
        # OUTS rides as IP, the book's convention everywhere else.
        assert pitching == ['W', 'QS', 'K', 'SV', 'HLD', 'CG', 'IP',
                            'ER', 'P_H', 'P_BB']

    def test_explainer_uses_the_house_token(self):
        """Italic, size 9, never bold (MLB-170)."""
        _rows, formats = _build(_clean_season(4))
        spec = next(f for f in formats if f.get('range', '').startswith('A3:'))
        assert spec['format']['textFormat'] == {
            'bold': False, 'italic': True, 'fontSize': 9}

    def test_no_merges(self):
        """Nothing on this tab merges, so the writer's unmerge pass has
        nothing to undo -- keep it that way or the merge lattice becomes
        live state (the Trades-tab lesson)."""
        _rows, formats = _build(_clean_season(4))
        assert not [f for f in formats if f.get('merge')]

    def test_newest_season_first_then_finish_order(self):
        finishes = (_clean_season(3)
                    + [_row(i, f'Old {i}', i, 800.0 - i, teams=3, season=2001)
                       for i in range(1, 4)])
        rows, _ = _build(finishes)
        data = _data_rows(rows)
        assert [r[0] for r in data] == [2024, 2024, 2024, 2001, 2001, 2001]
        assert [r[3] for r in data] == [1, 2, 3, 1, 2, 3]

    def test_champion_gets_the_trophy_on_the_finish_cell(self):
        finishes = _clean_season(3)
        finishes[0]['is_champion'] = True
        rows, _ = _build(finishes)
        assert _data_rows(rows)[0][_col0('Finish')] == '🏆 1'

    def test_franchise_column_is_the_stitched_current_name(self):
        """Names drift and get reused, so the row carries the canonical
        identity rather than what the platform published that year."""
        finishes = _clean_season(1)
        finishes[0]['team_name'] = 'Junk Drawer All-Stars'
        rows, _ = _build(finishes)
        assert _data_rows(rows)[0][_col0('Franchise')] == 'Alpha Canonical'

    def test_owner_is_resolved_per_season_not_per_franchise(self):
        """Today's owner beside a 2003 season would misattribute it."""
        finishes = (_clean_season(1)
                    + [_row(1, 'A', 1, 800.0, teams=1, season=2001)])
        rows, _ = _build(finishes, owners={(2024, 1): 'New Owner',
                                           (2001, 1): 'Old Owner'})
        data = _data_rows(rows)
        assert [r[_col0('Owner(s)')] for r in data] == ['New Owner',
                                                        'Old Owner']

    def test_owner_blank_when_unknown(self):
        rows, _ = _build(_clean_season(1), owners={})
        assert _data_rows(rows)[0][_col0('Owner(s)')] == ''

    def test_two_franchises_sharing_a_canonical_name_fall_back(self):
        """CBS has DISTINCT franchises that share one canonical name (14
        and 17 are both 'Bent Spokes', both in the league 2004-2008).
        Without a fallback the season renders two identical labels."""
        finishes = [_row(2, 'Bent Spokes', 1, 900.0, teams=2),
                    _row(3, 'Hit-and-Rum', 2, 800.0, teams=2)]
        fmap = {2: {'canonical_id': 2, 'name': 'Bent Spokes', 'abbrev': 'BENT'},
                3: {'canonical_id': 3, 'name': 'Bent Spokes', 'abbrev': 'BENT'}}
        rows, _ = cbs.build_season_history_rows(
            _context(), finishes, fmap, team_stats=_stats(finishes))
        labels = [r[_col0('Franchise')] for r in _data_rows(rows)]
        assert labels == ['Bent Spokes', 'Hit-and-Rum']

    def test_the_fallback_only_fires_on_the_clashing_season(self):
        """A franchise that shares a name in one season still reads
        canonically in seasons where the other fork is absent."""
        finishes = [_row(2, 'Old Label', 1, 900.0, teams=1, season=2001)]
        fmap = {2: {'canonical_id': 2, 'name': 'Bent Spokes', 'abbrev': 'BENT'},
                3: {'canonical_id': 3, 'name': 'Bent Spokes', 'abbrev': 'BENT'}}
        rows, _ = cbs.build_season_history_rows(
            _context(), finishes, fmap, team_stats=_stats(finishes))
        assert _data_rows(rows)[0][_col0('Franchise')] == 'Bent Spokes'

    def test_margin_is_negative_points_behind(self):
        """Winner total minus this team's, so the champion reads 0 and
        everyone else is negative (Kyle's spec)."""
        finishes = [_row(1, 'A', 1, 1000.0, behind=0, teams=2),
                    _row(2, 'B', 2, 700.0, behind=300, teams=2)]
        rows, _ = _build(finishes)
        assert [r[_col0('Margin')] for r in _data_rows(rows)] == [0, -300]

    def test_league_averages_are_per_season(self):
        finishes = ([_row(1, 'A', 1, 1000.0, bat=600.0, teams=2, season=2024),
                     _row(2, 'B', 2, 500.0, bat=200.0, teams=2, season=2024)]
                    + [_row(1, 'A', 1, 100.0, bat=40.0, teams=1, season=2001)])
        rows, _ = _build(finishes)
        data = _data_rows(rows)
        avg = slice(_col0('League Avg Hitting'), _col0('League Avg Total') + 1)
        assert [r[avg] for r in data[:2]] == [[400, 350, 750]] * 2
        assert data[2][avg] == [40, 60, 100]

    def test_points_columns_carry_no_decimals(self):
        finishes = [_row(1, 'A', 1, 1000.5, bat=600.25, behind=0, teams=1)]
        rows, _ = _build(finishes)
        row = _data_rows(rows)[0]
        for name in ('Hitting Points', 'Pitching Points', 'Total Points',
                     'Margin', 'League Avg Hitting', 'League Avg Pitching',
                     'League Avg Total'):
            assert isinstance(row[_col0(name)], int), name

    def test_innings_render_in_baseball_notation(self):
        """7 outs is 2.1, never 2.333."""
        finishes = _clean_season(1)
        stats = _stats(finishes, outs=3013.0)
        rows, _ = _build(finishes, team_stats=stats)
        assert _data_rows(rows)[0][_col0('IP')] == '1004.1'

    def test_a_season_with_no_attribution_renders_blank_not_zero(self):
        """A zero would read as 'they hit no home runs'."""
        rows, _ = _build(_clean_season(2), team_stats=[])
        row = _data_rows(rows)[0]
        for name in ('HR', 'TB', 'K', 'IP'):
            assert row[_col0(name)] == '', name
        # ...while the awarded points still render.
        assert row[_col0('Total Points')] == 999

    def test_empty_finishes_render_a_header_only_tab(self):
        rows, _ = _build([])
        assert _data_rows(rows) == []

    def test_an_empty_tab_emits_no_gradients_or_record_marks(self):
        """No data rows means no range to scale -- a gradient over an
        empty span is a malformed request, not a no-op."""
        _rows, formats = _build([])
        assert not [f for f in formats if 'gradient' in f]


class TestHighlighting:
    """Kyle's ruling, 2026-08-05: Matchup History's rules verbatim --
    three-stop polarity scale, scaled all-time, gold on the all-time
    records, and NOTHING on Ties."""

    def _letter(self, name):
        import gspread
        return gspread.utils.rowcol_to_a1(1, _col0(name) + 1)[:-1]

    def _gradients(self, formats):
        """{column letter: gradient rule}."""
        return {f['range'].split(':')[0].rstrip('0123456789'): f['gradient']
                for f in formats if 'gradient' in f}

    def _marks(self, formats, name):
        letter = self._letter(name)
        return [f for f in formats
                if f.get('format', {}).get('textFormat', {}).get('bold')
                and f['range'].split(':')[0].rstrip('0123456789') == letter]

    def test_ties_column_gets_no_gradient(self):
        """The explicit ruling: a tie is neither good nor bad."""
        _rows, formats = _build(_clean_season(4))
        assert self._letter('Ties') not in self._gradients(formats)

    def test_graded_columns_are_exactly_the_polarity_ones(self):
        _rows, formats = _build(_clean_season(4))
        graded = set(self._gradients(formats))
        expected = {self._letter(n) for n in (
            ['Margin'] + cbs._SEASON_HISTORY_HIT_STATS
            + ['W', 'QS', 'K', 'SV', 'HLD', 'CG', 'IP', 'ER', 'P_H', 'P_BB']
            + ['Hitting Points', 'Pitching Points', 'Total Points']
            + ['Outscored', 'Outscored By']
            + ['League Avg Hitting', 'League Avg Pitching',
               'League Avg Total'])}
        assert graded == expected
        # Ties and every buffer stay ungraded.
        assert len(graded) == cbs._SEASON_HISTORY_WIDTH - 10

    def test_polarity_gradients_have_three_stops(self):
        _rows, formats = _build(_clean_season(4))
        margin = self._letter('Margin')
        for col, rule in self._gradients(formats).items():
            if col == margin:
                continue
            assert set(rule) == {'minpoint', 'midpoint', 'maxpoint'}, col

    def test_margin_is_two_stop_red_to_white(self):
        """Every value is <= 0, so there is no positive half for a
        three-stop scale to describe (Kyle: 'red to white')."""
        _rows, formats = _build(_clean_season(4))
        rule = self._gradients(formats)[self._letter('Margin')]
        assert set(rule) == {'minpoint', 'maxpoint'}
        assert rule['minpoint']['color'] == cbs._SCALE_RED
        assert rule['maxpoint']['color'] == cbs._WHITE

    def test_fewer_is_better_columns_are_reversed(self):
        """Outscored By reads best at zero, and ER / hits / walks allowed
        are negative-polarity stats -- green sits at the MINIMUM for those
        and at the maximum everywhere else."""
        _rows, formats = _build(_clean_season(4))
        grad = self._gradients(formats)
        green = cbs._SCALE_GREEN
        for name in ('Outscored By', 'ER', 'P_H', 'P_BB'):
            assert grad[self._letter(name)]['minpoint']['color'] == green, name
        for name in ('Total Points', 'Outscored', 'HR', 'K', 'IP'):
            assert grad[self._letter(name)]['maxpoint']['color'] == green, name

    def test_gradients_span_every_season_not_one(self):
        """All-time scaling: one rule per column over the whole tab. Kyle
        ruled this as the points-LEAGUE default, extensible to anyone
        else's points league, rather than an era-aware variant."""
        finishes = (_clean_season(3)
                    + [_row(i, f'Old {i}', i, 800.0 - i, teams=3, season=2001)
                       for i in range(1, 4)])
        rows, formats = _build(finishes)
        col = self._letter('Total Points')
        spec = next(f for f in formats
                    if 'gradient' in f and f['range'].startswith(f'{col}'))
        assert spec['range'] == f'{col}6:{col}{len(rows)}'

    def test_sole_record_holder_gets_gold_and_bold(self):
        rows, formats = _build(_clean_season(4))
        marks = self._marks(formats, 'Total Points')
        assert len(marks) == 1
        assert marks[0]['format']['textFormat'] == {
            'bold': True, 'foregroundColor': cbs._RECORD_GOLD}
        # ...and it lands on the row actually holding the max.
        top = int(marks[0]['range'].split(':')[0][len(
            self._letter('Total Points')):])
        assert rows[top - 1][_col0('Total Points')] == 999

    def test_shared_record_bolds_every_holder_but_golds_none(self):
        """Outscored tops out at N-1 in every season, so the mark is
        shared -- bold, no gold. Kyle: 'a lot of ties there, but fine'."""
        finishes = (_clean_season(4)
                    + [_row(i, f'Old {i}', i, 800.0 - i, teams=4, season=2001)
                       for i in range(1, 5)])
        _rows, formats = _build(finishes)
        marks = self._marks(formats, 'Outscored')
        assert len(marks) == 2
        for mark in marks:
            assert mark['format']['textFormat'] == {'bold': True}

    def test_no_record_mark_on_the_fewer_is_better_columns(self):
        """Their best value is 0 and every champion holds it -- a record
        25 rows wide is not a record, so those columns are grade-only.
        Same for the negative pitching stats: most earned runs allowed is
        not an achievement."""
        _rows, formats = _build(_clean_season(4))
        for name in ('Margin', 'Outscored By', 'ER', 'P_H', 'P_BB'):
            assert not self._marks(formats, name), name

    def test_stat_records_are_marked(self):
        _rows, formats = _build(_clean_season(4))
        for name in ('HR', 'TB', 'K', 'IP'):
            assert self._marks(formats, name), name

    def test_an_all_blank_stat_column_is_never_gilded(self):
        """A season with no attribution reads 0.0 numerically -- gilding
        an empty cell would invent a record."""
        _rows, formats = _build(_clean_season(4), team_stats=[])
        for name in ('HR', 'TB', 'K', 'IP'):
            assert not self._marks(formats, name), name


@pytest.mark.warehouse
class TestAwardedLensReconciliation:
    """The requirement that cannot be checked by looking at the tab.

    Kyle ruled on 2026-08-01 that canonical outcomes run on the AWARDED
    lens, and this tab is entirely canonical outcomes. The failure mode
    it guards is invisible: a Season History built on the reconstructed
    default is internally consistent and renders perfectly -- it is just
    not this league's past. Reconstructed disagrees with awarded on 307
    of 395 team-seasons and would move 15 of 25 championships, and
    nothing else in the build or the goldens would say a word.

    So the check is against the SOURCE, cell by cell: every points cell
    on the rendered tab equals CBS's own published year-end value.
    """

    @pytest.fixture(autouse=True)
    def _cbs(self):
        import db
        previous = db.league_key()
        db.set_league('cbs-bsb')
        try:
            yield
        finally:
            db.set_league(previous)

    @pytest.fixture
    def awarded(self):
        import db
        return db.query_snowflake(
            "SELECT season_year, franchise_id, team_name, standings_rank,"
            "       batting_points, pitching_points, total_points,"
            "       points_behind, teams_in_season"
            " FROM stg_cbs__ui_standings WHERE league_key = 'cbs-bsb'")

    @pytest.fixture
    def rendered(self):
        rows, _ = cbs.build_season_history_rows(
            _context(), cbs.get_historic_finishes(), cbs.get_franchise_map(),
            team_stats=cbs.get_season_history_stats(),
            owners=cbs.get_season_owners())
        return rows, _data_rows(rows)

    def _paired(self, awarded, data):
        """(source row, rendered row) pairs, matched by the builder's own
        documented ordering -- season descending, then standings_rank,
        then franchise_id.

        Deliberately NOT matched on the Franchise label: two distinct
        franchises can share a canonical name (14/17 'Bent Spokes'), so
        the builder disambiguates those seasons, and a test that redid
        that resolution would be asserting its own copy of the logic.
        Ordering is a contract the builder states in its docstring."""
        expected = sorted(awarded, key=lambda r: (-int(r['season_year']),
                                                  int(r['standings_rank']),
                                                  int(r['franchise_id'])))
        assert len(expected) == len(data), 'row count must match the source'
        pairs = list(zip(expected, data))
        for src, row in pairs:
            assert row[0] == int(src['season_year']), 'ordering drifted'
        return pairs

    def test_every_points_cell_equals_the_published_value(self, awarded,
                                                          rendered):
        _rows, data = rendered
        for src, row in self._paired(awarded, data):
            assert row[_col0('Hitting Points')] == int(src['batting_points'])
            assert row[_col0('Pitching Points')] == int(src['pitching_points'])
            assert row[_col0('Total Points')] == int(src['total_points'])
            # Margin is the awarded points_behind, re-signed.
            assert row[_col0('Margin')] == -int(src['points_behind'])

    def test_the_stat_columns_do_not_disturb_the_awarded_ones(self, awarded,
                                                              rendered):
        """The tab carries two lenses side by side. This asserts the seam:
        the awarded trio still adds up on every row with the
        reconstructed stat blocks present, so a future change to the stat
        aggregation can never silently move a points column."""
        _rows, data = rendered
        pairs = self._paired(awarded, data)
        for _src, row in pairs:
            assert (row[_col0('Hitting Points')]
                    + row[_col0('Pitching Points')]
                    == row[_col0('Total Points')])
        assert len(pairs) == 395

    def test_no_season_renders_two_identically_labelled_rows(self, rendered):
        """The 14/17 fork: 'Bent Spokes' is the canonical name of two
        genuinely distinct franchises, both in the league 2004-2008."""
        _rows, data = rendered
        by_season = {}
        for row in data:
            by_season.setdefault(row[0], []).append(row[_col0('Franchise')])
        for year, labels in by_season.items():
            assert len(labels) == len(set(labels)), (
                f'{year} renders a duplicate franchise label')

    def test_display_rounding_is_lossless(self, awarded):
        """The tab renders whole numbers. That is only honest because CBS
        published whole numbers -- assert it rather than assume it, since
        a fractional source value would round silently."""
        for src in awarded:
            for col in ('batting_points', 'pitching_points', 'total_points',
                        'points_behind'):
                assert float(src[col]) == int(src[col]), (col, src)

    def test_the_invariant_holds_across_every_real_season(self, rendered):
        rows, _ = rendered
        data = _data_rows(rows)
        assert len(data) == 395
        o, ob, t = (_col0('Outscored'), _col0('Outscored By'), _col0('Ties'))
        by_season = {}
        for row in data:
            by_season.setdefault(row[0], []).append(row)
        assert len(by_season) == 25
        for season_rows in by_season.values():
            for row in season_rows:
                assert row[o] + row[ob] + row[t] == len(season_rows) - 1

    def test_the_2024_tie_is_present_and_counted_in_neither(self, rendered):
        """The real tie, asserted against real data: if a future re-parse
        of the standings pages breaks it, the ties case this tab was
        designed around disappears silently."""
        rows, _ = rendered
        tied = [r for r in _data_rows(rows)
                if r[0] == 2024 and r[_col0('Ties')] > 0]
        assert len(tied) == 2
        assert {r[_col0('Total Points')] for r in tied} == {9156}
        for row in tied:
            assert (row[_col0('Outscored')], row[_col0('Outscored By')],
                    row[_col0('Ties')]) == (12, 2, 1)

    def test_the_pre_2004_seasons_are_not_blank(self, rendered):
        """The correction that produced this layout (Kyle, 2026-08-05):
        2001-2003 have no start-share table, which is why they got the
        day-level reconstruction instead of the shortcut -- so their stat
        cells must be populated, not empty. A regression here would look
        like tidy missing data rather than a bug."""
        _rows, data = rendered
        early = [r for r in data if r[0] <= 2003]
        assert len({r[0] for r in early}) == 3
        for name in ('HR', 'TB', 'R', 'K', 'IP'):
            populated = [r for r in early if r[_col0(name)] not in ('', 0)]
            assert len(populated) == len(early), (
                f'{name} blank on {len(early) - len(populated)} of '
                f'{len(early)} pre-2004 team-seasons')
