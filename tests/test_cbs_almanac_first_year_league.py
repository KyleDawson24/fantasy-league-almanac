"""A CBS league with no closed seasons and no captured draft -- MLB-222
C-2/C-3/C-4.

Every case here took down the ENTIRE almanac build rather than the one
tab it belonged to, which is what makes them crash paths rather than
cosmetic gaps. All three are pure layout/guard tests: no warehouse, no
network, same injected-input style as test_cbs_draft_recap_tab.py and
test_cbs_standings_tab.py.

The draft case is not hypothetical. The draft_assembly_plan seed's last
row is 2026, so the first 2027 standings row makes season_year 2027 with
no picks behind it -- the build breaks on its own the first time the
league rolls over.
"""
import pytest

import cbs_almanac_sheets as cbs


# ---------------------------------------------------------------------------
# C-2 -- max() over an empty draft
# ---------------------------------------------------------------------------
def _pick(year, overall, rnd, team, name, pts):
    return {'season_year': year, 'draft_label': 'Test', 'round_num': rnd,
            'round_pick': None, 'overall_pick': overall, 'list_seq': 1,
            'team_name_raw': team, 'player_cbs_id': '1',
            'player_name_raw': name, 'pos_team_raw': None,
            'page_total_fpts': None, 'page_active_fpts': None,
            'order_tier': 'true', 'calc_total': float(pts),
            'calc_hitting': float(pts), 'calc_pitching': 0.0,
            'resolution': 'id', 'twoway_sum': False}


def _fmap():
    return {1: {'name': 'Alpha', 'abbrev': 'ALP'},
            2: {'name': 'Beta', 'abbrev': 'BET'}}


def _history_through_2026():
    picks = [_pick(2026, 1, 1, 'Alpha', 'Stud One', 400),
             _pick(2026, 2, 1, 'Beta', 'Bust One', 10)]
    report = {2026: {'picks': 2, 'order': 'true', 'rounds': 1, 'note': None,
                     'resolution': {'id': 2}}}
    return picks, report


def test_season_with_no_captured_draft_still_builds():
    """The 2027 rollover. Before the fix this raised
    ValueError: max() arg is an empty sequence."""
    rows, _ = cbs.build_draft_recap_rows(
        2027, _fmap(), history=_history_through_2026())
    assert rows[0] == [cbs.DRAFT_TAB]


def test_empty_draft_season_says_so_instead_of_a_zero_pick_draft():
    """An honest empty state, not ' stitched as one 0-pick draft'."""
    rows, _ = cbs.build_draft_recap_rows(
        2027, _fmap(), history=_history_through_2026())
    flat = ['\t'.join(str(c) for c in r) for r in rows]
    assert any('No draft captured for this season yet.' in l for l in flat)
    assert not any('0-pick draft' in l for l in flat)


def test_prior_season_draft_still_renders_normally():
    """The guard must not blank a season that DOES have picks."""
    rows, _ = cbs.build_draft_recap_rows(
        2026, _fmap(), history=_history_through_2026())
    flat = ['\t'.join(str(c) for c in r) for r in rows]
    assert any(l.startswith('Draft Board - 2026') for l in flat)
    assert any('stitched as one 2-pick draft' in l for l in flat)


# ---------------------------------------------------------------------------
# C-3 -- int(None) on the all-time acquisition block
# ---------------------------------------------------------------------------
def test_alltime_acquisitions_are_empty_before_any_season_closes():
    """Returns empty WITHOUT touching the warehouse -- the guard has to
    fire before the query is built, or a first-year league still pays for
    a round trip that cannot have rows. If this ever reaches
    query_snowflake the test fails on the connection, not on an assert."""
    assert cbs.get_acquisition_channels_alltime(None) == []


# ---------------------------------------------------------------------------
# C-4 -- seasons[0] on an empty finishes matrix
# ---------------------------------------------------------------------------
def _context(first_season=2026):
    # first_season == season_year is what get_context() now returns when
    # stg_cbs__ui_standings is empty; last_closed_season stays None.
    return {'season_year': 2026, 'latest_period': 16,
            'first_season': first_season, 'last_closed_season': None}


def _arc():
    def row(team_id, name, rank):
        return {'period': 16, 'team_id': team_id, 'team_name': name,
                'standings_rank': rank, 'points': 100.0 - rank,
                'period_points': 10.0, 'rank_change': 0,
                'points_behind_leader': float(rank - 1),
                'is_latest_period': True}
    return [row(1, 'Alpha', 1), row(2, 'Beta', 2)]


def _franchises():
    return [{'team_id': 1, 'team_name': 'Alpha'},
            {'team_id': 2, 'team_name': 'Beta'}]


def _fmap_standings():
    return {1: {'canonical_id': 1, 'name': 'Alpha', 'abbrev': 'ALP'},
            2: {'canonical_id': 2, 'name': 'Beta', 'abbrev': 'BET'}}


def test_standings_build_with_no_closed_seasons(monkeypatch):
    """finishes=[] is a league in its first year. Before the fix
    seasons[0] raised IndexError."""
    monkeypatch.setattr(cbs, 'get_franchise_map', _fmap_standings)
    rows, _ = cbs.build_standings_rows(
        _context(), _arc(), [], _franchises())
    assert rows[0] == ['Advanced Standings']


def test_season_finishes_scope_names_the_season_not_an_open_range(monkeypatch):
    """With nothing closed the era scope is the season in flight alone --
    never '2026–2026' and never a range with no left edge."""
    monkeypatch.setattr(cbs, 'get_franchise_map', _fmap_standings)
    rows, _ = cbs.build_standings_rows(
        _context(), _arc(), [], _franchises())
    flat = ['\t'.join(str(c) for c in r) for r in rows]
    assert any('SEASON FINISHES' in l for l in flat)
    assert not any('None' in l for l in flat), \
        'a None leaked into rendered copy'


def test_no_none_leaks_into_any_rendered_banner(monkeypatch):
    """The C-4 tail: six era banners and the gameplay-days note all
    interpolated context['first_season'] / n_std directly, so a first-year
    league rendered the literal strings 'None-2026' and '= None gameplay
    days'. Nothing rendered may contain the word None."""
    monkeypatch.setattr(cbs, 'get_franchise_map', _fmap_standings)
    rows, _ = cbs.build_standings_rows(
        _context(), _arc(), [], _franchises(), season_days=[])
    for row in rows:
        for cell in row:
            assert 'None' not in str(cell), f'None leaked: {cell!r}'
