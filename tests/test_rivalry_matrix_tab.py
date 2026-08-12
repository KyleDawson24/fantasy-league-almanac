"""The Rivalry Matrix render (MLB-229) -- pure functions, no warehouse.

These take the two mart shapes as plain dicts and assert on the rows the tab
gets. The SQL that produces those shapes is proven separately in
tests/test_franchise_rivalry.py, which builds it for real; what is left to
check here is the densification, and densification is where the two DIFFERENT
kinds of empty cell live:

    a blank diagonal  -- a team has no record against itself
    a 0-0 cell        -- two teams that both play today and have never met

Collapsing those into one appearance is the bug this file exists to catch. It
is not a formatting preference: "they never played" is a fact about the league
that a blank cell hides, and "a team cannot play itself" is not a nil record.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "output") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "output"))

from almanac_logic import (  # noqa: E402
    RIVALRY_MATRIX_LABEL,
    build_advanced_standings_tab_rows,
    build_rivalry_matrix_rows,
    format_rivalry_record,
)


def _axis(key, name, abbrev, order):
    return {'identity_key': key, 'identity_name': name,
            'identity_abbrev': abbrev, 'identity_source': 'franchise_id',
            'active_platform_teams': 1, 'sort_order': order}


AXES = [
    _axis('fid:1', 'Alpha Anchors', 'ALPH', 1),
    _axis('name:Bent Spokes', 'Bent Spokes', 'BENT', 2),
    _axis('fid:2', 'Cedar Crows', 'CROW', 3),
]


def _pair(row, opp, mw, ml, mt, sw, sl, st):
    return {'row_identity_key': row, 'opponent_identity_key': opp,
            'row_team_name': row, 'opponent_team_name': opp,
            'matchup_meetings': mw + ml + mt, 'matchup_wins': mw,
            'matchup_losses': ml, 'matchup_ties': mt,
            'points_for': 0.0, 'points_against': 0.0, 'points_margin': 0.0,
            'season_meetings': sw + sl + st, 'season_wins': sw,
            'season_losses': sl, 'season_ties': st}


# Alpha and Bent Spokes have history both ways. Cedar Crows is an expansion
# team: it appears on the axes and in nobody's ledger.
PAIRS = [
    _pair('fid:1', 'name:Bent Spokes', 5, 2, 1, 3, 1, 0),
    _pair('name:Bent Spokes', 'fid:1', 2, 5, 1, 1, 3, 0),
]


def _grids(rows):
    """The two data grids, split on their section headers."""
    out, current = {}, None
    for row in rows:
        if row and row[0] in ('Head-to-Head Matchups', 'Season Points'):
            current = row[0]
            out[current] = []
        elif current and row and row[0] != 'Team':
            out[current].append(row)
    return out


# ===========================================================================
# The two kinds of empty cell
# ===========================================================================
def test_the_diagonal_is_blank(rows=None):
    """A team has no record against itself, and there is no honest number for
    that cell."""
    grids = _grids(build_rivalry_matrix_rows(AXES, PAIRS))

    for grid in grids.values():
        for index, row in enumerate(grid):
            assert row[1 + index] == '', f'{row[0]} has a diagonal value'


def test_two_teams_that_never_met_read_zero_zero(rows=None):
    """Cedar Crows is on the axes and in nobody's ledger. Its record against
    Alpha is nil, and nil is a fact -- blanking it would hide an expansion
    team's whole situation behind the same appearance as the diagonal."""
    grids = _grids(build_rivalry_matrix_rows(AXES, PAIRS))
    crows = grids['Head-to-Head Matchups'][2]

    assert crows[0] == 'Cedar Crows'
    assert crows[1] == '0-0' and crows[2] == '0-0'
    assert crows[3] == ''


def test_the_blank_diagonal_and_the_zero_cell_are_distinguishable(rows=None):
    """The one assertion that fails if either empty case is made to look like
    the other."""
    grid = _grids(build_rivalry_matrix_rows(AXES, PAIRS))['Season Points']
    diagonal = {grid[i][1 + i] for i in range(len(grid))}
    never_met = grid[2][1]

    assert diagonal == {''}
    assert never_met == '0-0'
    assert never_met not in diagonal


# ===========================================================================
# Cells and reciprocity
# ===========================================================================
def test_a_cell_reads_from_the_row_teams_perspective(rows=None):
    grids = _grids(build_rivalry_matrix_rows(AXES, PAIRS))
    matchups = grids['Head-to-Head Matchups']

    assert matchups[0][0] == 'Alpha Anchors'
    assert matchups[0][2] == '5-2-1'
    assert matchups[1][1] == '2-5-1'


def test_the_two_ledgers_are_independent(rows=None):
    """Same pair, different records: 5-2-1 in matchups and 3-1 across completed
    seasons. A renderer that filled both grids from one source would show the
    same number twice."""
    grids = _grids(build_rivalry_matrix_rows(AXES, PAIRS))

    assert grids['Head-to-Head Matchups'][0][2] == '5-2-1'
    assert grids['Season Points'][0][2] == '3-1'


def test_ties_are_shown_only_when_they_exist(rows=None):
    """'12-4' is what a reader expects; '12-4-0' reads as a league that has
    ties and this pair happened not to."""
    assert format_rivalry_record(12, 4, 0) == '12-4'
    assert format_rivalry_record(6, 6, 2) == '6-6-2'
    assert format_rivalry_record(0, 0, 0) == '0-0'


# ===========================================================================
# Axes
# ===========================================================================
def test_the_axes_are_identities_and_keep_their_order(rows=None):
    """One row and one column per active identity, in the order the axes model
    gave -- including a configured-name identity that may stand for several
    live platform ids."""
    rows = build_rivalry_matrix_rows(AXES, PAIRS)
    header = next(r for r in rows if r and r[0] == 'Team')

    assert header == ['Team', 'ALPH', 'BENT', 'CROW']
    assert [r[0] for r in _grids(rows)['Season Points']] == [
        'Alpha Anchors', 'Bent Spokes', 'Cedar Crows']


def test_the_grid_is_square(rows=None):
    grids = _grids(build_rivalry_matrix_rows(AXES, PAIRS))

    for grid in grids.values():
        assert len(grid) == len(AXES)
        for row in grid:
            assert len(row) == len(AXES) + 1


def test_rows_sharing_a_display_name_are_disambiguated(rows=None):
    """Two teams with no configured canonical name whose OBSERVED names match
    are deliberately kept apart, and they then arrive wearing the same string.
    Caught in visual QA: the grid showed two identical row labels with no way
    to tell which was which. Column headers are abbrevs and already differ."""
    twins = [
        _axis('fid:5', 'Twin Name FC', 'TWN1', 1),
        _axis('fid:6', 'Twin Name FC', 'TWN2', 2),
        _axis('fid:1', 'Alpha Anchors', 'ALPH', 3),
    ]
    grid = _grids(build_rivalry_matrix_rows(twins, []))['Season Points']

    assert [r[0] for r in grid] == ['Twin Name FC (TWN1)',
                                    'Twin Name FC (TWN2)',
                                    'Alpha Anchors']


def test_unambiguous_rows_are_not_suffixed(rows=None):
    """Tagging every row would clutter the ordinary case to fix the rare one."""
    grid = _grids(build_rivalry_matrix_rows(AXES, PAIRS))['Season Points']

    assert [r[0] for r in grid] == ['Alpha Anchors', 'Bent Spokes',
                                    'Cedar Crows']


def test_no_axes_renders_nothing(rows=None):
    """A league with no active teams -- a fresh install -- gets no block rather
    than an empty banner with a header and no grid under it."""
    assert build_rivalry_matrix_rows([], []) == []


def test_a_ledger_with_no_pairs_still_renders_the_grid(rows=None):
    """Active teams that have never played anyone: every off-diagonal cell is
    0-0 and the block still says so."""
    grids = _grids(build_rivalry_matrix_rows(AXES, []))
    cells = {c for grid in grids.values() for row in grid for c in row[1:]}

    assert cells == {'', '0-0'}


# ===========================================================================
# Placement on Advanced Standings
# ===========================================================================
def test_the_block_lands_on_advanced_standings(rows=None):
    """Placement, wired end to end: the tab builder emits the matrix when it is
    given axes."""
    rows = build_advanced_standings_tab_rows(
        [], [], [], 2026, rivalry_axes=AXES, rivalry_pairs=PAIRS)
    labels = [r[0] for r in rows if r]

    assert RIVALRY_MATRIX_LABEL in labels
    assert 'Head-to-Head Matchups' in labels
    assert 'Season Points' in labels


def test_the_tab_is_unchanged_when_no_rivalry_data_is_passed(rows=None):
    """The kwargs are optional, so a caller that has not been rewired -- or a
    league with no axes -- renders exactly what it did before. This is what
    keeps the goldens honest about what actually moved."""
    before = build_advanced_standings_tab_rows([], [], [], 2026)
    after = build_advanced_standings_tab_rows(
        [], [], [], 2026, rivalry_axes=[], rivalry_pairs=[])

    assert before == after
    assert RIVALRY_MATRIX_LABEL not in [r[0] for r in before if r]


def test_the_matrix_is_the_last_block_on_the_tab(rows=None):
    """A standings answers "who is ahead"; the matrix answers "against whom",
    which is the next question rather than a prior one."""
    rows = build_advanced_standings_tab_rows(
        [], [], [], 2026, rivalry_axes=AXES, rivalry_pairs=PAIRS)
    banner = next(i for i, r in enumerate(rows)
                  if r and r[0] == RIVALRY_MATRIX_LABEL)

    assert all(not r or r[0] != 'Advanced Standings'
               for r in rows[banner:])
