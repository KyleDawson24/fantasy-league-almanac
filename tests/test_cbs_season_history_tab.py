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
        _row(2, 'Mesa Joses', 2, 9156.0, teams=4),
        _row(3, 'Betty White Sox', 2, 9156.0, teams=4),
        _row(4, 'Delta', 4, 8025.0, teams=4),
    ]


def _context():
    return {'season_year': 2026, 'latest_period': 16,
            'first_season': 2001, 'last_closed_season': 2025}


def _fmap():
    return {1: {'canonical_id': 1, 'name': 'Alpha Canonical', 'abbrev': 'ALP'},
            2: {'canonical_id': 2, 'name': 'Mesa Joses', 'abbrev': 'MJ'},
            3: {'canonical_id': 3, 'name': 'Betty White Sox', 'abbrev': 'BWS'},
            4: {'canonical_id': 4, 'name': 'Delta', 'abbrev': 'DEL'}}


def _build(finishes):
    return cbs.build_season_history_rows(_context(), finishes, _fmap())


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
        """The invariant over the built tab, not just the helper -- the
        counts and the Teams column have to agree cell-for-cell. Two
        seasons of different shapes, one of them tied, because the
        counting is per-season and a leak across the boundary is exactly
        what this catches."""
        older = [_row(i, f'Old {i}', i, 800.0 - i, teams=4, season=2001)
                 for i in range(1, 5)]
        rows, _ = _build(_tied_season() + older)
        checked = 0
        for row in _data_rows(rows):
            teams, outscored, outscored_by, ties = (row[4], row[9], row[10],
                                                    row[11])
            assert outscored + outscored_by + ties == teams - 1
            checked += 1
        assert checked == 8


class TestTies:
    def test_tie_counts_in_neither_column(self):
        counts = cbs.season_outscored_counts(_tied_season())
        assert counts[1] == (1, 1, 1)      # Mesa Joses
        assert counts[2] == (1, 1, 1)      # Betty White Sox

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
        assert rows[4] == cbs._SEASON_HISTORY_HEADER

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
        assert _data_rows(rows)[0][3] == '🏆 1'

    def test_both_name_columns(self):
        """Franchise = the stitched current name, Team That Season = what
        the platform published that year (names drift and get reused, so
        the tab carries both)."""
        finishes = _clean_season(1)
        finishes[0]['team_name'] = 'Junk Drawer All-Stars'
        rows, _ = _build(finishes)
        assert _data_rows(rows)[0][1:3] == ['Alpha Canonical',
                                            'Junk Drawer All-Stars']

    def test_league_averages_are_per_season(self):
        finishes = ([_row(1, 'A', 1, 1000.0, bat=600.0, teams=2, season=2024),
                     _row(2, 'B', 2, 500.0, bat=200.0, teams=2, season=2024)]
                    + [_row(1, 'A', 1, 100.0, bat=40.0, teams=1, season=2001)])
        rows, _ = _build(finishes)
        data = _data_rows(rows)
        assert [r[12:15] for r in data[:2]] == [[400, 350, 750]] * 2
        assert data[2][12:15] == [40, 60, 100]

    def test_empty_finishes_render_a_header_only_tab(self):
        rows, _ = _build([])
        assert _data_rows(rows) == []


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

    def test_every_points_cell_equals_the_published_value(self, awarded):
        rows, _ = cbs.build_season_history_rows(
            _context(), cbs.get_historic_finishes(), cbs.get_franchise_map())
        rendered = {(r[0], r[2]): r for r in _data_rows(rows)}
        # (season, as-published name) is unique in the source BY DESIGN --
        # it is the map every other CBS UI family resolves against. Assert
        # it, because a collision would let a mismatch hide behind a
        # dict key rather than fail.
        assert len(rendered) == len(awarded), 'row count must match the source'
        for src in awarded:
            key = (int(src['season_year']), src['team_name'])
            row = rendered[key]
            assert row[5] == int(src['batting_points'])
            assert row[6] == int(src['pitching_points'])
            assert row[7] == int(src['total_points'])
            assert row[8] == int(src['points_behind'])
            assert row[4] == int(src['teams_in_season'])

    def test_display_rounding_is_lossless(self, awarded):
        """The tab renders whole numbers. That is only honest because CBS
        published whole numbers -- assert it rather than assume it, since
        a fractional source value would round silently."""
        for src in awarded:
            for col in ('batting_points', 'pitching_points', 'total_points',
                        'points_behind'):
                assert float(src[col]) == int(src[col]), (col, src)

    def test_the_invariant_holds_across_every_real_season(self, awarded):
        rows, _ = cbs.build_season_history_rows(
            _context(), cbs.get_historic_finishes(), cbs.get_franchise_map())
        data = _data_rows(rows)
        assert len(data) == 395
        for row in data:
            assert row[9] + row[10] + row[11] == row[4] - 1

    def test_the_2024_tie_is_present_and_counted_in_neither(self, awarded):
        """The real tie, asserted against real data: if a future re-parse
        of the standings pages breaks it, the ties case this tab was
        designed around disappears silently."""
        rows, _ = cbs.build_season_history_rows(
            _context(), cbs.get_historic_finishes(), cbs.get_franchise_map())
        tied = [r for r in _data_rows(rows) if r[0] == 2024 and r[11] > 0]
        assert len(tied) == 2
        assert {r[7] for r in tied} == {9156}
        for row in tied:
            assert (row[9], row[10], row[11]) == (12, 2, 1)
