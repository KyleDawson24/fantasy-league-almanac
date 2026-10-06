"""The screenshot anonymizer's residual audit (MLB-323).

`tools/anonymize_sheet_copy.py` used to prove a copy clean by rescanning it
for the real forms it had APPLIED. That can only confirm the scrub did what
it set out to do: a form dropped as a player collision, or never mapped at
all, is invisible to it by construction. The audit now enumerates every real
form a reader could see -- from the real side alone, independently of the
mapping -- and searches the whole copy for all of them. A hit is a residual
unless the MLB spine proves it sits inside a player's name.

Every name here is invented. Fast and pure: no warehouse, no network, no
Sheets call, and nothing read from the maintainer's real league config.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LEAGUE = 'lg-test'
OTHER = 'lg-other'


def _tool():
    pytest.importorskip("gspread", reason="anonymizer needs the Sheets client")
    path = os.path.join(REPO, "tools", "anonymize_sheet_copy.py")
    spec = importlib.util.spec_from_file_location("anonymize_sheet_copy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _loader(files):
    """A config reader over in-memory rows, keyed by file name."""
    return lambda name: [dict(r) for r in files.get(name, [])]


def _by_year(season, fid, name, league=LEAGUE):
    return {'league_key': league, 'season_year': str(season),
            'franchise_id': str(fid), 'owner_name': name}


def _link(search, label):
    return ('=HYPERLINK("https://www.baseball-reference.com/search/search.fcgi?'
            f'search={search}", "{label}")')


def _inventory(tool, files, kept=(), private=(), league=LEAGUE, roster=()):
    return tool.build_form_inventory(league, list(kept), roster=roster,
                                     load=_loader(files),
                                     private=lambda: list(private))


# --------------------------------------------------------------------------
# The inventory: what could be wrong, not what was changed
# --------------------------------------------------------------------------

def test_audit_catches_a_form_the_mapping_never_touched():
    """The MLB-321 blind spot. Nothing maps this owner, so the old audit --
    a rescan of applied forms -- had nothing to look for and passed."""
    tool = _tool()
    files = {tool.SEED_BY_YEAR: [_by_year(2011, 3, 'Wendell Quigg')]}
    forms, _ = _inventory(tool, files)
    tabs = {'Season History': ([['Owner'], ['Wendell Quigg']], 1, 1)}

    assert tool.Replacer([]).find('Wendell Quigg') == [], \
        "the applied-forms rescan sees nothing when nothing was applied"

    result = tool.audit_tabs(tabs, forms, spine=frozenset())
    assert result['residuals'][tool.CLS_OWNER_FULL] == 1
    assert [where for where, _ in result['cells']] == ['Season History!A2']


def test_tab_titles_and_the_spreadsheet_title_are_searched():
    tool = _tool()
    files = {tool.SEED_BY_YEAR: [_by_year(2011, 3, 'Wendell Quigg')]}
    forms, _ = _inventory(tool, files)
    tabs = {'Wendell Quigg': ([['nothing here']], 1, 1)}
    result = tool.audit_tabs(tabs, forms, frozenset(),
                             spreadsheet_title='Copy of Wendell Quigg book')
    assert sorted(w for w, _ in result['cells']) == [
        'Wendell Quigg [tab title]', '[spreadsheet title]']


def test_inventory_is_scoped_to_the_target_league():
    """A form from the other league's config is not this book's finding;
    the private map has no league column, so its forms are always in."""
    tool = _tool()
    files = {tool.SEED_BY_YEAR: [_by_year(2011, 3, 'Wendell Quigg', league=OTHER)]}
    forms, notes = _inventory(tool, files, private=[('Agnes Thorne', 'Ava Tern')])
    values = {f.value for f in forms}
    assert 'Wendell Quigg' not in values
    assert 'Agnes Thorne' in values
    assert notes['out_of_scope'] >= 1


def test_a_form_the_twin_keeps_is_set_aside_and_reported():
    """Identity pairs are public in the demo fixture, so they are not
    searched -- but they are listed, never dropped silently."""
    tool = _tool()
    files = {tool.SEED_BY_YEAR: [_by_year(2011, 3, 'Wendell Quigg')]}
    forms, notes = _inventory(tool, files, kept=[('Wendell', LEAGUE, 'by_year.owner_name')])
    assert 'Wendell' not in {f.value for f in forms}
    assert 'Wendell' in notes['kept'][tool.CLS_OWNER_FIRST]
    assert 'Wendell Quigg' in {f.value for f in forms}


def test_abbreviations_match_case_sensitively():
    """'CAL' the team is not 'Cal' the player: an abbreviation that matched
    case-insensitively would collide with every player first name."""
    tool = _tool()
    files = {tool.SEED_FRANCHISES: [{'league_key': LEAGUE, 'franchise_id': '4',
                                     'abbrev': 'THAD', 'franchise_name': 'Quigg Quarry'}]}
    forms, _ = _inventory(tool, files)
    tabs = {'Records': ([['Thad Rook (THAD)'], ['Thad Rook']], 1, 1)}
    result = tool.audit_tabs(tabs, forms, frozenset())
    assert result['residuals'][tool.CLS_ABBREV] == 1
    assert [w for w, _ in result['cells']] == ['Records!A1']


# --------------------------------------------------------------------------
# The positive player check
# --------------------------------------------------------------------------

def _thaddeus(tool):
    """An owner whose first name is also an (invented) MLB player's."""
    files = {
        tool.SEED_TEAM_OWNERS: [{'league_key': LEAGUE, 'franchise_id': '2',
                                 'owner_id': 'cbs-thaddeus-vane'}],
        tool.SEED_NICKNAMES: [{'owner_id': 'cbs-thaddeus-vane', 'first_name': 'Thaddeus',
                               'last_name': 'Vane', 'preferred_name': ''}],
    }
    forms, _ = _inventory(tool, files)
    spine = frozenset(tool._fold_name(n) for n in ('Thaddeus Rook', 'Ozzie Plum'))
    return forms, spine


def test_player_collision_is_excluded_by_proof_and_reported():
    """The owner's first name inside a PROVEN player cell is not a residual,
    and the report says which player let it through."""
    tool = _tool()
    forms, spine = _thaddeus(tool)
    tabs = {'Records': ([
        [_link('Thaddeus+Rook', 'Thaddeus Rook')],     # link, label = key
        [_link('Thaddeus+Rook', 'T Rook (K)')],        # the board's short form
        [_link('Thaddeus+Rook', 'Thaddeus Rook (412)')],
        ['Thaddeus Rook: 1,127, Ozzie Plum: 12.5'],    # contributor list
    ], 1, 1)}
    result = tool.audit_tabs(tabs, forms, spine)
    assert sum(result['residuals'].values()) == 0
    assert result['excluded_cells'] == 4
    assert set(result['excluded'][tool.CLS_OWNER_FIRST]) == {'Thaddeus Rook'}


def test_unproven_player_shaped_cells_still_count():
    """Proof is all-or-nothing and never comes from the sheet itself."""
    tool = _tool()
    forms, spine = _thaddeus(tool)
    tabs = {'Records': ([
        ['Thaddeus'],                                       # a bare name: 1
        [_link('Thaddeus+Rook', 'Thad the Hammer')],         # nickname label: 1 (URL)
        [_link('Thaddeus+Ruck', 'Thaddeus Ruck')],           # off the spine: 2
        ['Thaddeus Rook: 41, Quentin Fogg: 12'],             # one name unproven: 1
        ['including Thaddeus Rook and, worst of all...'],    # prose never qualifies: 1
    ], 1, 1)}
    result = tool.audit_tabs(tabs, forms, spine)
    assert result['residuals'][tool.CLS_OWNER_FIRST] == 6
    assert result['excluded_cells'] == 0


def test_no_spine_means_no_exclusions():
    """Without the MLB spine on disk nothing can be proven; the audit fails
    closed rather than trusting the sheet's own links."""
    tool = _tool()
    forms, _ = _thaddeus(tool)
    tabs = {'Records': ([[_link('Thaddeus+Rook', 'Thaddeus Rook')]], 1, 1)}
    result = tool.audit_tabs(tabs, forms, frozenset())
    assert result['residuals'][tool.CLS_OWNER_FIRST] == 2


def test_player_label_mirror_matches_the_renderer():
    """The proof mirrors output/almanac_render.draft_initial_text instead of
    importing it. If the two drift, labels stop proving (fail closed) --
    this test makes the drift visible first."""
    tool = _tool()
    almanac_render = pytest.importorskip("almanac_render")
    for name in ('Thaddeus Rook', 'J.D. Quill', 'CC Fenwick', 'Ozzie',
                 'Ozzie Plum (Batter)', 'A.J. Moss', 'quentin fogg', ''):
        assert tool._draft_initial_text(name) == almanac_render.draft_initial_text(name), name


# --------------------------------------------------------------------------
# Co-owner labels, derived the warehouse's way
# --------------------------------------------------------------------------

def test_co_owner_labels_follow_the_warehouse():
    """dim_team_owner joins co-owners' FIRST names with ', ' in first-name
    order; the first name is the first token of the owner's LATEST name."""
    tool = _tool()
    files = {tool.SEED_BY_YEAR: [
        _by_year(2010, 6, 'Wendell Quigg'), _by_year(2010, 6, 'Agnes Thorne'),
        _by_year(2012, 9, 'Agnes Thorne'),
        # Same person, renamed later: the owner_alias fold makes it one id,
        # and its latest spelling supplies the first name.
        _by_year(2011, 6, 'Wendell Quigg'), _by_year(2011, 6, 'Bertram Oake'),
        _by_year(2013, 9, 'Bert Oake'),
    ], tool.SEED_ALIAS: [{'league_key': LEAGUE, 'owner_id': 'cbs-bertram-oake',
                          'canonical_owner_id': 'cbs-bert-oake', 'preferred_name': ''}]}
    labels = tool.co_owner_labels(_loader(files))
    assert labels == {(LEAGUE, '2010', '6'): ['Agnes, Wendell'],
                      (LEAGUE, '2011', '6'): ['Bert, Wendell']}

    forms, _ = _inventory(tool, files)
    co = {f.value for f in forms if f.cls == tool.CLS_CO_OWNER}
    assert co == {'Agnes, Wendell', 'Agnes & Wendell', 'Bert, Wendell', 'Bert & Wendell'}


def test_simulate_scrub_suffixes_a_renamed_title_like_the_live_pass():
    tool = _tool()
    rep = tool.Replacer([('Wendell Quigg', 'Walt Quill')])
    tabs = {'Walt Quill': ([['x']], 1, 1), 'Wendell Quigg': ([['Wendell Quigg']], 1, 1)}
    after = tool.simulate_scrub(tabs, rep)
    assert list(after) == ['Walt Quill', 'Walt Quill (2)']
    assert after['Walt Quill (2)'][0] == [['Walt Quill']]
