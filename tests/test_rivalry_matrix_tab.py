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
    RIVALRY_INDENT_COLS,
    RIVALRY_MATCHUP_LEDGER,
    RIVALRY_MATRIX_LABEL,
    RIVALRY_SEASON_LEDGER,
    build_advanced_standings_tab_rows,
    build_rivalry_matrix_rows,
    format_rivalry_record,
    rivalry_cell_win_pct,
    rivalry_matrix_grid,
)


def _axis(key, name, abbrev, order, league_format='h2h', evidence=True):
    return {'identity_key': key, 'identity_name': name,
            'identity_abbrev': abbrev, 'identity_source': 'franchise_id',
            'active_platform_teams': 1, 'league_format': league_format,
            'has_rivalry_evidence': evidence, 'sort_order': order}


AXES = [
    _axis('fid:1', 'Alpha Anchors', 'ALPH', 1),
    _axis('name:Bent Spokes', 'Bent Spokes', 'BENT', 2),
    _axis('fid:2', 'Cedar Crows', 'CROW', 3),
]

# The same teams in a points league. Identical axes and identical ledger rows,
# so any difference between the two renders is the format dispatch and nothing
# else.
POINTS_AXES = [dict(a, league_format='points') for a in AXES]

# The same teams in a league nothing can be proven about: no schedule capture,
# so the ledger fails closed and every cell would densify to 0-0.
NO_EVIDENCE_AXES = [dict(a, has_rivalry_evidence=False) for a in AXES]


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


def _unindent(row):
    """One block row with its two-column indent stripped.

    The block sits at column C; everything about WHAT it says is unchanged by
    that. So the content tests below work in logical coordinates and the
    indentation tests work on raw rows -- each asserting its own concern
    instead of every cell lookup carrying a +2.
    """
    return list(row[RIVALRY_INDENT_COLS:]) if row else list(row)


def _labels(rows):
    """The first cell of each non-blank block row, indent stripped."""
    return [_unindent(r)[0] for r in rows if r and _unindent(r)]


def _grids(rows):
    """The rendered grid, keyed by its ledger label, in logical coordinates.
    One entry: format dispatch means a league shows the one ledger its format
    gives meaning to. Kept as a mapping so a test names which ledger it
    expected rather than assuming."""
    out, current = {}, None
    for raw in rows:
        row = _unindent(raw)
        if row and row[0] in (RIVALRY_MATCHUP_LEDGER, RIVALRY_SEASON_LEDGER):
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
    crows = grids[RIVALRY_MATCHUP_LEDGER][2]

    assert crows[0] == 'Cedar Crows'
    assert crows[1] == '0-0' and crows[2] == '0-0'
    assert crows[3] == ''


def test_the_blank_diagonal_and_the_zero_cell_are_distinguishable(rows=None):
    """The one assertion that fails if either empty case is made to look like
    the other."""
    grid = _grids(build_rivalry_matrix_rows(
        POINTS_AXES, PAIRS))[RIVALRY_SEASON_LEDGER]
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
    matchups = grids[RIVALRY_MATCHUP_LEDGER]

    assert matchups[0][0] == 'Alpha Anchors'
    assert matchups[0][2] == '5-2-1'
    assert matchups[1][1] == '2-5-1'


def test_the_two_ledgers_are_independent(rows=None):
    """Same pair, same ledger rows, different records: 5-2-1 in matchups and
    3-1 across completed seasons. A renderer that read one lens for both
    formats would show the same number twice."""
    grids = _grids(build_rivalry_matrix_rows(AXES, PAIRS))

    assert grids[RIVALRY_MATCHUP_LEDGER][0][2] == '5-2-1'
    assert _grids(build_rivalry_matrix_rows(
        POINTS_AXES, PAIRS))[RIVALRY_SEASON_LEDGER][0][2] == '3-1'


# ===========================================================================
# Format dispatch
# ===========================================================================
def test_an_h2h_league_shows_only_the_matchup_ledger(rows=None):
    """One matrix, not two. A season-points table under an H2H league's
    head-to-head grid is a product decision nobody made."""
    labels = _labels(build_rivalry_matrix_rows(AXES, PAIRS))

    assert RIVALRY_MATCHUP_LEDGER in labels
    assert RIVALRY_SEASON_LEDGER not in labels


def test_a_points_league_shows_only_the_season_ledger(rows=None):
    """A points league has no matchups at all, so a head-to-head grid there is
    a square of 0-0 meaning "this league does not work that way" -- which is
    not what 0-0 says anywhere else on the tab."""
    labels = _labels(build_rivalry_matrix_rows(POINTS_AXES, PAIRS))

    assert RIVALRY_SEASON_LEDGER in labels
    assert RIVALRY_MATCHUP_LEDGER not in labels


def test_an_unknown_format_renders_nothing(rows=None):
    """An install that has captured neither signal yet. A matrix whose meaning
    cannot be stated is worse than no matrix, so this renders none rather than
    defaulting to head-to-head."""
    unknown = [dict(a, league_format='unknown') for a in AXES]

    assert build_rivalry_matrix_rows(unknown, PAIRS) == []
    assert rivalry_matrix_grid(unknown, PAIRS) is None


def test_the_format_comes_from_the_axes_by_default(rows=None):
    """The renderer does not decide the format and does not ask the platform:
    it reads what the axes carry, which the warehouse derived from data
    presence."""
    assert rivalry_matrix_grid(AXES, PAIRS)['ledger'] == RIVALRY_MATCHUP_LEDGER
    assert rivalry_matrix_grid(POINTS_AXES, PAIRS)['ledger'] == \
        RIVALRY_SEASON_LEDGER


def test_the_explainer_describes_the_ledger_being_shown(rows=None):
    """Each ledger promises something different -- completed matchups versus
    completed shared seasons -- and a caption describing the other one is a
    quietly wrong sheet."""
    h2h = rivalry_matrix_grid(AXES, PAIRS)['explainer']
    points = rivalry_matrix_grid(POINTS_AXES, PAIRS)['explainer']

    assert 'Completed matchups only' in h2h
    assert 'still being played' in h2h
    assert 'completed season BOTH teams' in points
    assert 'was not in counts for nobody' in points


def test_both_books_render_the_same_contract(rows=None):
    """The shared half of a two-book feature: the ESPN builder returns rows and
    the CBS builder accumulates rows plus format ranges, so only the LAYOUT is
    per-book. The cells, the ledger choice, the header and the explainer all
    come from one function, which is what stops the two workbooks from drifting
    into two different products."""
    matrix = rivalry_matrix_grid(POINTS_AXES, PAIRS)
    espn_rows = [_unindent(r) for r in
                 build_rivalry_matrix_rows(POINTS_AXES, PAIRS)]

    assert matrix['header'] in espn_rows
    for grid_row in matrix['grid']:
        assert grid_row in espn_rows
    assert [matrix['ledger']] in espn_rows


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
    header = next(_unindent(r) for r in rows if _unindent(r)[:1] == ['Team'])

    assert header == ['Team', 'ALPH', 'BENT', 'CROW']
    assert [r[0] for r in _grids(rows)[RIVALRY_MATCHUP_LEDGER]] == [
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
    grid = _grids(build_rivalry_matrix_rows(twins, []))[RIVALRY_MATCHUP_LEDGER]

    assert [r[0] for r in grid] == ['Twin Name FC (TWN1)',
                                    'Twin Name FC (TWN2)',
                                    'Alpha Anchors']


def test_unambiguous_rows_are_not_suffixed(rows=None):
    """Tagging every row would clutter the ordinary case to fix the rare one."""
    grid = _grids(build_rivalry_matrix_rows(AXES, PAIRS))[RIVALRY_MATCHUP_LEDGER]

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
    flat = [str(c) for r in rows for c in r]

    assert RIVALRY_MATRIX_LABEL in flat
    assert RIVALRY_MATCHUP_LEDGER in flat
    assert RIVALRY_SEASON_LEDGER not in flat


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
                  if RIVALRY_MATRIX_LABEL in r)

    assert all(not r or r[0] != 'Advanced Standings'
               for r in rows[banner:])


# ===========================================================================
# Unknown evidence versus proven zero
# ===========================================================================
#
# The two look the same to a careless renderer and mean opposite things:
#
#   "we cannot prove any result yet"  -- nothing is known
#   "they have played nobody"          -- something is known, and it is nil
#
# Densifying an empty ledger produces the second from the first. These are the
# assertions that stop it.


def test_no_evidence_renders_no_cells_at_all(rows=None):
    """Not one 0-0, and no header either -- nothing a reader could mistake for
    a record. The banner stays so the section is findable."""
    rows = build_rivalry_matrix_rows(NO_EVIDENCE_AXES, [])
    flat = [str(c) for r in rows for c in r]

    assert RIVALRY_MATRIX_LABEL in flat
    assert '0-0' not in flat
    assert 'Team' not in flat
    assert _grids(rows) == {}


def test_no_evidence_says_why_and_says_it_loudly(rows=None):
    """A conspicuous state, not a quiet blank. It has to say the difference out
    loud: nothing is known, as opposed to nobody having won."""
    rows = build_rivalry_matrix_rows(NO_EVIDENCE_AXES, [])
    notice = next(c for r in rows for c in r if 'UNAVAILABLE' in str(c))

    assert 'RIVALRY RESULTS UNAVAILABLE' in notice
    assert 'not a record of nobody winning' in notice
    assert 'nothing is known yet' in notice


def test_unknown_evidence_and_proven_zero_cannot_render_identically(rows=None):
    """THE ASSERTION THIS SECTION EXISTS FOR. Same axes, same empty ledger --
    the only difference is whether anything is provable. A renderer that
    densified regardless would produce byte-identical output for a league with
    no history and a league whose teams have genuinely never met."""
    unknown = build_rivalry_matrix_rows(NO_EVIDENCE_AXES, [])
    proven_zero = build_rivalry_matrix_rows(AXES, [])

    assert unknown != proven_zero
    assert '0-0' in [str(c) for r in proven_zero for c in r]
    assert '0-0' not in [str(c) for r in unknown for c in r]


def test_a_genuine_never_met_pair_still_reads_zero_zero(rows=None):
    """Evidence exists, so nil IS the record. Cedar Crows is an expansion team
    in a league with proven history -- 0-0 is a fact about it, and suppressing
    that would hide the very thing the matrix is for."""
    grid = _grids(build_rivalry_matrix_rows(AXES, PAIRS))[RIVALRY_MATCHUP_LEDGER]

    assert grid[2][1] == '0-0'
    assert grid[2][3] == ''


def test_the_evidence_gate_is_independent_of_the_ledger_having_rows(rows=None):
    """A league can have provable history AND an empty ledger -- every pair
    genuinely new to each other. That still renders a grid, because 0-0 means
    something there."""
    rows = build_rivalry_matrix_rows(AXES, [])
    cells = {str(c) for r in _grids(rows)[RIVALRY_MATCHUP_LEDGER]
             for c in r[1:]}

    assert cells == {'', '0-0'}


def test_no_evidence_suppresses_the_points_ledger_too(rows=None):
    """Format-independent: a points league with nothing provable makes the same
    false claim if its seasons are densified."""
    axes = [dict(a, league_format='points', has_rivalry_evidence=False)
            for a in AXES]
    flat = [str(c) for r in build_rivalry_matrix_rows(axes, []) for c in r]

    assert '0-0' not in flat
    assert any('UNAVAILABLE' in c for c in flat)


def test_the_tab_still_carries_the_section_when_evidence_is_missing(rows=None):
    """Wired end to end: the block is on Advanced Standings either way, so a
    reader who has heard the matrix exists finds it and learns why it is empty
    rather than finding nothing and wondering whether the publish broke."""
    rows = build_advanced_standings_tab_rows(
        [], [], [], 2026, rivalry_axes=NO_EVIDENCE_AXES, rivalry_pairs=[])
    flat = [str(c) for r in rows for c in r]

    assert RIVALRY_MATRIX_LABEL in flat
    assert RIVALRY_MATCHUP_LEDGER not in flat


# ===========================================================================
# Indentation and cell shading (visual pass)
# ===========================================================================
#
# The block sits two columns right, so its content begins in column C. Every
# line of it moves together -- banner, explainer, ledger label, header, team
# labels and cells -- because a title at the margin with a table wandering off
# under it does not read as one object.


def _first_col(row):
    """0-based index of the first non-empty cell, or None for a blank row."""
    return next((i for i, c in enumerate(row) if str(c) != ''), None)


def test_every_line_of_the_block_starts_in_column_c(rows=None):
    rows = build_rivalry_matrix_rows(AXES, PAIRS)
    starts = {_first_col(r) for r in rows if _first_col(r) is not None}

    assert starts == {RIVALRY_INDENT_COLS}


def test_the_banner_moves_with_the_block(rows=None):
    """The title indents too. Its navy band still runs from column A across
    the tab -- that band is a full-width divider -- so what moved is the label
    on it, not the band."""
    rows = build_rivalry_matrix_rows(AXES, PAIRS)
    banner = next(r for r in rows if RIVALRY_MATRIX_LABEL in r)

    assert banner[RIVALRY_INDENT_COLS] == RIVALRY_MATRIX_LABEL
    assert banner[:RIVALRY_INDENT_COLS] == [''] * RIVALRY_INDENT_COLS


def test_the_unavailable_state_is_indented_too(rows=None):
    """Both states, or the block jumps left when a league has no history."""
    rows = build_rivalry_matrix_rows(NO_EVIDENCE_AXES, [])
    starts = {_first_col(r) for r in rows if _first_col(r) is not None}

    assert starts == {RIVALRY_INDENT_COLS}


def test_the_grid_keeps_its_shape_under_the_indent(rows=None):
    """The shift is a translation, not a reshape: same labels, same cells, two
    columns right."""
    rows = build_rivalry_matrix_rows(AXES, PAIRS)
    header_at = next(i for i, r in enumerate(rows)
                     if _unindent(r)[:1] == ['Team'])
    grid = rows[header_at + 1:]

    assert [r[RIVALRY_INDENT_COLS] for r in grid] == [
        'Alpha Anchors', 'Bent Spokes', 'Cedar Crows']
    assert grid[0][RIVALRY_INDENT_COLS + 2] == '5-2-1'
    for row in grid:
        assert len(row) == len(AXES) + 1 + RIVALRY_INDENT_COLS


def test_the_indent_is_shared_by_both_books(rows=None):
    """One constant, so the two workbooks cannot drift apart on it."""
    import cbs_almanac_sheets

    assert cbs_almanac_sheets.RIVALRY_INDENT_COLS == RIVALRY_INDENT_COLS


# -- cell shading -----------------------------------------------------------
def test_win_percentage_counts_a_tie_as_half(rows=None):
    assert rivalry_cell_win_pct('5-2-1') == pytest.approx(5.5 / 8)
    assert rivalry_cell_win_pct('6-6') == pytest.approx(0.5)
    assert rivalry_cell_win_pct('12-4-0') == pytest.approx(0.75)


def test_cells_with_no_percentage_get_no_colour(rows=None):
    """A blank diagonal has no record, and 0-0 has no decisions -- 0-0 is not
    0.000, and shading it deep red would invent a drubbing out of two teams
    that have never played."""
    assert rivalry_cell_win_pct('') is None
    assert rivalry_cell_win_pct('0-0') is None
    assert rivalry_cell_win_pct('0-0-0') is None
    assert rivalry_cell_win_pct(None) is None
    assert rivalry_cell_win_pct('not a record') is None


def test_reciprocal_cells_shade_to_opposite_sides(rows=None):
    """A .688 and its mirror .312 sit equal distances either side of the white
    midpoint, which is what makes the grid readable diagonally."""
    forward = rivalry_cell_win_pct('5-2-1')
    reverse = rivalry_cell_win_pct('2-5-1')

    assert forward + reverse == pytest.approx(1.0)
    assert forward > 0.5 > reverse


def test_the_shading_does_not_touch_the_text(rows=None):
    """Colour is a second channel on the record, not a replacement for it."""
    grid = _grids(build_rivalry_matrix_rows(AXES, PAIRS))[RIVALRY_MATCHUP_LEDGER]

    assert grid[0][2] == '5-2-1'
    assert grid[1][1] == '2-5-1'


# -- the ESPN write layer's shading specs ------------------------------------
#
# Private import, same doctrine as cbs_almanac_sheets importing
# _draft_gradient_color: the shading is one house scale used by two books, and
# testing it through a Sheets publish is not testing at all.
from almanac_write import _rivalry_shade_specs  # noqa: E402


def _espn_shades():
    rows = build_rivalry_matrix_rows(AXES, PAIRS)
    ledger_at = next(i for i, r in enumerate(rows)
                     if _unindent(r)[:1] == [RIVALRY_MATCHUP_LEDGER])
    return rows, ledger_at, _rivalry_shade_specs(rows, ledger_at)


def test_the_espn_grid_shades_only_its_decided_cells(rows=None):
    """Two decided cells in this fixture -- Alpha's 5-2-1 and its mirror. The
    diagonal and the never-met 0-0s get nothing."""
    _rows, _ledger, specs = _espn_shades()

    assert len(specs) == 2
    assert all(set(s['format']) == {'backgroundColor'} for s in specs)


def test_the_espn_shades_land_either_side_of_neutral(rows=None):
    """.688 and .312 -- one green side, one red side, on the house gradient
    whose midpoint is white at .500."""
    _rows, _ledger, specs = _espn_shades()
    by_range = {s['range']: s['format']['backgroundColor'] for s in specs}
    winning = next(c for c in by_range.values() if c['green'] > c['red'])
    losing = next(c for c in by_range.values() if c['red'] > c['green'])

    assert winning['green'] > winning['red']
    assert losing['red'] > losing['green']


def test_the_espn_shades_sit_in_the_indented_columns(rows=None):
    """Cells start at column D -- one right of the team labels in C."""
    _rows, _ledger, specs = _espn_shades()
    cols = {''.join(c for c in s['range'] if c.isalpha()) for s in specs}

    assert cols <= {'D', 'E', 'F'}
    assert 'A' not in cols and 'B' not in cols


def test_an_all_zero_espn_grid_shades_nothing(rows=None):
    """Every pair genuinely new to each other: 0-0 everywhere, no decisions,
    no colour."""
    rows = build_rivalry_matrix_rows(AXES, [])
    ledger_at = next(i for i, r in enumerate(rows)
                     if _unindent(r)[:1] == [RIVALRY_MATCHUP_LEDGER])

    assert _rivalry_shade_specs(rows, ledger_at) == []
