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


# --------------------------------------------------------------------------
# Labels are mapped whole; single names keep the player-collision drop
# --------------------------------------------------------------------------

REAL_COOWNED = [_by_year(2010, 6, 'Wendell Quigg'), _by_year(2010, 6, 'Agnes Thorne')]
TWIN_COOWNED = [_by_year(2010, 6, 'Walt Quill'), _by_year(2010, 6, 'Ava Tern')]
# An (invented) MLB player who shares the first owner's first name.
PLAYER = 'Wendell Rook'


def test_combined_label_maps_to_the_twin_label_in_both_spellings():
    tool = _tool()
    pairs = tool.co_owner_label_pairs(_loader({tool.SEED_BY_YEAR: REAL_COOWNED}),
                                      _loader({tool.SEED_BY_YEAR: TWIN_COOWNED}))
    assert pairs == [('Agnes, Wendell', 'Ava, Walt', LEAGUE),
                     ('Agnes & Wendell', 'Ava & Walt', LEAGUE)]


def test_combined_label_is_replaced_in_one_step_despite_a_player_collision():
    """The single first name collides with a player and is dropped, as
    before; the label no longer depends on it."""
    tool = _tool()
    files = {tool.SEED_BY_YEAR: REAL_COOWNED}
    labels = [(r, a) for r, a, _ in tool.co_owner_label_pairs(
        _loader(files), _loader({tool.SEED_BY_YEAR: TWIN_COOWNED}))]
    singles = [('Wendell', 'Walt'), ('Agnes', 'Ava')]
    safe, dropped = tool.split_player_safe(labels + singles, {PLAYER})
    assert dropped == [('Wendell', 'Walt')]

    tabs = {'Season Records': ([['Holder'], ['Agnes & Wendell'], ['Agnes, Wendell'],
                                [_link('Wendell+Rook', PLAYER)]], 1, 1)}
    after = tool.simulate_scrub(tabs, tool.Replacer(safe))
    assert [row[0] for row in after['Season Records'][0][1:3]] == ['Ava & Walt', 'Ava, Walt']

    forms, _ = _inventory(tool, files)
    spine = frozenset({tool._fold_name(PLAYER)})
    result = tool.audit_tabs(after, forms, spine)
    assert sum(result['residuals'].values()) == 0
    assert result['excluded'][tool.CLS_OWNER_FIRST] == {PLAYER: 2}

    # The old way -- first names one at a time -- left half a real label,
    # and the inventory audit is what sees it.
    half = tool.simulate_scrub(tabs, tool.Replacer([('Agnes', 'Ava')]))
    assert half['Season Records'][0][1][0] == 'Ava & Wendell'
    stale = tool.audit_tabs(half, forms, spine)
    assert stale['residuals'][tool.CLS_OWNER_FIRST] == 2


def test_first_name_only_label_left_by_the_drop_is_caught():
    """An owner the history knows only by a first name renders as that bare
    first name. If it collides with a player, the single-token drop leaves
    it visible -- and the audit reports it rather than passing."""
    tool = _tool()
    files = {tool.SEED_BY_YEAR: [_by_year(2009, 4, 'Wendell')]}
    forms, _ = _inventory(tool, files)
    tabs = {'Home': ([['Owner'], ['Wendell'], [_link('Wendell+Rook', PLAYER)]], 1, 1)}
    spine = frozenset({tool._fold_name(PLAYER)})

    safe, dropped = tool.split_player_safe([('Wendell', 'Walt')], {PLAYER})
    assert dropped and not safe
    result = tool.audit_tabs(tool.simulate_scrub(tabs, tool.Replacer(safe)), forms, spine)
    assert result['residuals'][tool.CLS_OWNER_FIRST] == 1
    assert [w for w, _ in result['cells']] == ['Home!A2']

    # With no player to collide with, the bare name is replaced and clean.
    safe, _ = tool.split_player_safe([('Wendell', 'Walt')], set())
    clean = tool.audit_tabs(tool.simulate_scrub(tabs, tool.Replacer(safe)), forms, spine)
    assert sum(clean['residuals'].values()) == 0


def test_multi_token_forms_are_never_dropped_as_player_collisions():
    """A full name equal to a player's is still an owner identity in an
    owner cell; player cells are protected by never being written."""
    tool = _tool()
    safe, dropped = tool.split_player_safe([(PLAYER, 'Walt Quill')], {PLAYER})
    assert safe == [(PLAYER, 'Walt Quill')] and not dropped


# --------------------------------------------------------------------------
# Abbreviations: case-sensitive, never vetoed by a player, scoped by league
# --------------------------------------------------------------------------

def test_abbreviation_pairs_replace_case_sensitively():
    """'THAD' the team and 'Thad' the person are different strings; at equal
    length the abbreviation wins its own spelling and nothing else."""
    tool = _tool()
    rep = tool.Replacer([tool.Pair('THAD', 'TMAX', True), ('Thad', 'Walt')])
    assert rep.sub('Thad Rook (THAD)') == ('Walt Rook (TMAX)', 2)
    assert rep.sub('THADS and Thadd') == ('THADS and Thadd', 0)


def test_a_player_first_name_cannot_veto_an_abbreviation():
    """The CAL case: an abbreviation used to be dropped because a player's
    first name matched it case-insensitively."""
    tool = _tool()
    pairs = [tool.Pair('THAD', 'TMAX', True), ('Thad', 'Walt')]
    safe, dropped = tool.split_player_safe(pairs, {'Thad Rook'})
    assert safe == [tool.Pair('THAD', 'TMAX', True)]
    assert dropped == [('Thad', 'Walt')]


def test_two_letter_abbreviations_are_matched_on_word_boundaries():
    tool = _tool()
    assert tool._classify_private('QX') == tool.CLS_ABBREV
    assert tool._classify_private('Quigg Quarry') == tool.CLS_SCRUB_MAP
    rep = tool.Replacer([tool.Pair('QX', 'TMQX', True)])
    assert rep.sub('(QX) QXL Qx') == ('(TMQX) QXL Qx', 1)


def test_pairs_are_scoped_and_the_targets_kept_form_wins():
    """The private map has no league column. Its row for one league's 'QX'
    must not rename the other league's team whose twin keeps 'QX'."""
    tool = _tool()
    tagged = [('QX', 'TMQX', 'private_map', None, True),
              ('Wendell Quigg', 'Walt Quill', 'by_year.owner_name', LEAGUE, False),
              ('Agnes Thorne', 'Ava Tern', 'by_year.owner_name', OTHER, False)]
    kept = [('QX', OTHER, 'franchises.abbrev')]

    here, _, _ = tool.scope_pairs(tagged, kept, LEAGUE)
    assert here == [tool.Pair('QX', 'TMQX', True), tool.Pair('Wendell Quigg', 'Walt Quill')]

    there, kept_over, _ = tool.scope_pairs(tagged, kept, OTHER)
    assert there == [tool.Pair('Agnes Thorne', 'Ava Tern')]
    assert kept_over == [('QX', 'private_map')]


# --------------------------------------------------------------------------
# No twin, no run
# --------------------------------------------------------------------------

def _team_page(name, abbrev=None):
    a1 = f'{name} ({abbrev})' if abbrev else name
    return ([[a1, '', 'Points glossary'], ['Optimal Lineups, through 9/28/2026'],
             ["Points are calculated according to current season's scoring."]], 1, 1)


def test_team_pages_are_read_from_the_renderers_header():
    tool = _tool()
    tabs = {'QQRY': _team_page("Quigg's Quarry", 'QQRY'),
            'Agnes Acres': _team_page('Agnes Acres'),
            'Records': ([["Quigg's Quarry (QQRY)"], ['Best Team Points']], 1, 1)}
    assert tool.team_pages(tabs) == [('QQRY', "Quigg's Quarry", 'QQRY'),
                                     ('Agnes Acres', 'Agnes Acres', '')]


def test_a_renamed_team_with_no_twin_refuses_and_names_it():
    """The MLB-321 case: a team renamed after the map was written has no
    twin row. The tool stops and says which team, rather than publishing
    it unchanged or inventing a twin."""
    tool = _tool()
    tabs = {'WQ': _team_page('Walt World', 'WQ'),
            'QQRY': _team_page("Quigg's Quarry", 'QQRY')}
    pairs = [tool.Pair('WQ', 'TMWQ', True)]
    with pytest.raises(SystemExit) as refusal:
        tool.check_twins(tabs, [], pairs, [], LEAGUE)
    message = str(refusal.value)
    assert "'QQRY' has no twin" in message
    assert '''"Quigg's Quarry" has no twin''' in message
    assert 'WQ' not in message.replace('QQRY', '')


def test_a_twinned_abbreviation_keeps_its_team_name_verbatim(capsys):
    """Team names are public content (Kyle's 2026-08-10 ruling); a team
    whose abbreviation has a twin runs, and its kept name is listed."""
    tool = _tool()
    tabs = {'QQRY': _team_page("Quigg's Quarry", 'QQRY')}
    tool.check_twins(tabs, [], [tool.Pair('QQRY', 'TMQQ', True)], [], LEAGUE)
    assert "\"Quigg's Quarry\"" in capsys.readouterr().out


def test_a_copy_scrubbed_once_already_is_twinned():
    tool = _tool()
    tabs = {'TMQQ': _team_page('Walt World', 'TMQQ')}
    tool.check_twins(tabs, [], [tool.Pair('QQRY', 'TMQQ', True),
                                ("Quigg's Quarry", 'Walt World')], [], LEAGUE)


def test_an_owner_with_no_twin_refuses_and_is_never_named():
    """A platform-served owner the config never held (a member who left)
    has no twin; the refusal carries a digest, not the name."""
    tool = _tool()
    roster = [{'league_key': LEAGUE, 'team_name': 'Walt World', 'team_abbrev': 'WQ',
               'owner_display': 'Wendell Quigg'}]
    forms, _ = _inventory(tool, {}, roster=roster)
    with pytest.raises(SystemExit) as refusal:
        tool.check_twins({}, forms, [], [], LEAGUE)
    message = str(refusal.value)
    assert tool._ref('Wendell Quigg') in message
    assert 'Wendell' not in message and 'Quigg' not in message


class _NoWrites:
    """A spreadsheet whose every write fails the test."""
    title = 'ANON copy'

    def __getattr__(self, name):
        if name in ('update_title', 'batch_update', 'values_batch_update'):
            raise AssertionError(f'{name} was called before the refusal')
        raise AttributeError(name)

    def worksheets(self):
        return []


def test_the_refusal_comes_before_any_write(monkeypatch):
    """Driven through main(): a team with no twin stops the run before latch
    (b)'s rename -- the first write -- or any cell is touched."""
    tool = _tool()
    tabs = {'QQRY': _team_page("Quigg's Quarry", 'QQRY')}
    sheet = _NoWrites()

    class _Client:
        def open_by_key(self, key):
            return sheet

    monkeypatch.setattr(tool, '_load_env', lambda: None)
    monkeypatch.setattr(tool, 'build_mapping', lambda **kw: ([], {'kept': []}))
    monkeypatch.setattr(tool, 'platform_roster', lambda: ([], []))
    monkeypatch.setattr(tool, 'load_player_spine', lambda root: frozenset())
    monkeypatch.setattr(tool, '_protected_sheet_ids', lambda: {'real-dev-id': 'SHEETS_DEV_ID'})
    monkeypatch.setattr(tool, '_client', lambda: _Client())
    monkeypatch.setattr(tool, '_read_tabs', lambda spreadsheet, titles: tabs)
    league = sorted(tool.load_registry()['leagues'])[0]
    monkeypatch.setattr('sys.argv', ['anonymize_sheet_copy.py', '--league', league,
                                     '--target', 'throwaway-copy-id'])
    with pytest.raises(SystemExit) as refusal:
        tool.main()
    assert 'REFUSING before any write' in str(refusal.value)
    assert "'QQRY' has no twin" in str(refusal.value)


# --------------------------------------------------------------------------
# After a clean audit: the revision-history steps
# --------------------------------------------------------------------------

def test_publish_checklist_names_the_three_steps_in_order():
    tool = _tool()
    text = '\n'.join(tool.publish_checklist(['copy-one']))
    assert 'https://docs.google.com/spreadsheets/d/copy-one/edit' in text
    assert 'version history' in text
    steps = [text.index(s) for s in ('1. Make a Drive copy', '2. Share THAT new copy',
                                     '3. Delete THIS scrubbed copy')]
    assert steps == sorted(steps)


def _clean_run(monkeypatch, tool, *extra):
    """main() over an empty book with nothing to find: the audit passes."""
    class _Sheet:
        title = 'ANON copy'

        def worksheets(self):
            return []

    class _Client:
        def open_by_key(self, key):
            return _Sheet()

    empty = lambda name: []  # noqa: E731
    inventory = tool.build_form_inventory
    monkeypatch.setattr(tool, '_load_env', lambda: None)
    monkeypatch.setattr(tool, 'build_mapping', lambda **kw: ([], {'kept': []}))
    monkeypatch.setattr(tool, 'platform_roster', lambda: ([], [('lg', 'snap', 'today')]))
    monkeypatch.setattr(tool, 'build_form_inventory',
                        lambda league, kept, roster: inventory(league, kept, roster,
                                                               load=empty, private=list))
    monkeypatch.setattr(tool, 'load_player_spine', lambda root: frozenset())
    monkeypatch.setattr(tool, '_protected_sheet_ids', lambda: {'real-dev-id': 'SHEETS_DEV_ID'})
    monkeypatch.setattr(tool, '_client', lambda: _Client())
    monkeypatch.setattr(tool, '_read_tabs', lambda spreadsheet, titles: {})
    league = sorted(tool.load_registry()['leagues'])[0]
    monkeypatch.setattr('sys.argv', ['anonymize_sheet_copy.py', '--league', league,
                                     '--target', 'copy-one', *extra])
    return tool.main()


def test_a_clean_run_ends_with_the_checklist(monkeypatch, capsys):
    tool = _tool()
    assert _clean_run(monkeypatch, tool) == 0
    out = capsys.readouterr().out
    assert 'zero residuals' in out
    assert out.index('AUDIT PASSED') < out.index('REMAINING STEPS')


def test_a_dry_run_prints_no_checklist(monkeypatch, capsys):
    tool = _tool()
    assert _clean_run(monkeypatch, tool, '--dry-run') == 0
    assert 'REMAINING STEPS' not in capsys.readouterr().out


def test_simulate_scrub_suffixes_a_renamed_title_like_the_live_pass():
    tool = _tool()
    rep = tool.Replacer([('Wendell Quigg', 'Walt Quill')])
    tabs = {'Walt Quill': ([['x']], 1, 1), 'Wendell Quigg': ([['Wendell Quigg']], 1, 1)}
    after = tool.simulate_scrub(tabs, rep)
    assert list(after) == ['Walt Quill', 'Walt Quill (2)']
    assert after['Walt Quill (2)'][0] == [['Walt Quill']]
