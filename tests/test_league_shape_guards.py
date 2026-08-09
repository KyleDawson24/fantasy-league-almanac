"""Guards for league shapes the project was never run against --
MLB-222 C-7 (an unorderable CBS draft period label) and C-8 (a standings
side table wider than the blocks it writes into).

Both took down a whole build rather than the one section they belonged
to, which is what puts them on the crash list. Pure tests -- no warehouse,
no network, no capture directory.

C-6 (the unmapped ESPN lineup slot id) lives with the other
serialize_box_scores crash path in tests/test_extract_bye_week.py.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

import almanac_sheets

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# C-7 -- a draft period label that is not a number
# ---------------------------------------------------------------------------
def _capture_module():
    """cbs_ui_capture by path: it is not on the suite's import surface.

    extract/ goes on sys.path first because the module imports its sibling
    `cbs_capture` by bare name -- the house pattern extract.py documents at
    its own import block."""
    extract_dir = str(_REPO_ROOT / "extract")
    if extract_dir not in sys.path:
        sys.path.insert(0, extract_dir)
    spec = importlib.util.spec_from_file_location(
        "cbs_ui_capture_under_test",
        _REPO_ROOT / "extract" / "cbs_ui_capture.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_preseason_leads():
    cap = _capture_module()
    assert cap._period_order("Pre-season") == 0
    assert cap._period_order("pre-season") == 0


def test_numbered_supplemental_drafts_keep_their_order():
    cap = _capture_module()
    assert cap._period_order("1") == 1
    assert cap._period_order("2") == 2
    assert cap._period_order("11") == 11


def test_unorderable_label_sorts_last_instead_of_raising():
    """'Supplemental' / 'Post-season' used to raise ValueError out of a
    bare int() and take down the WHOLE catalog -- every year's drafts,
    not just this one."""
    cap = _capture_module()
    for label in ("Supplemental", "Post-season", "", "Week 3"):
        assert cap._period_order(label) == cap._UNORDERED_PERIOD


def test_unorderable_label_sorts_after_every_real_period():
    """Last, not first -- an unreadable label must not displace
    Pre-season at the head of the stitching order."""
    cap = _capture_module()
    assert cap._period_order("Supplemental") > cap._period_order("Pre-season")
    assert cap._period_order("Supplemental") > cap._period_order("99")


# ---------------------------------------------------------------------------
# C-8 -- the standings side table runs past the rows it writes into
# ---------------------------------------------------------------------------
def _standings_team(team_id):
    return {
        'team_id': team_id, 'team_abbrev': f'T{team_id:02d}',
        'team_name': f'Team {team_id}', 'owner_display': f'Owner {team_id}',
        'wins': 2, 'losses': 1, 'ties': 0,
        'matchup_periods_played': 2, 'scoring_days_played': 16,
        'standard_matchup_days': 8,
        'calculated_hitting_pts': 100.0, 'calculated_pitching_pts': 50.0,
        'calculated_points': 150.0, 'against_calculated_points': 120.0,
        'hr': 10, 'b_so': 20, 'outs': 60, 'l': 4,
    }


def _spec(stat, category, points=1.0):
    return {'stat_name': stat, 'stat_category': category,
            'display_name': stat, 'points_per_unit': points,
            'column_label': stat, 'polarity': 'most'}


def _big_league(n_teams=30, periods=23):
    """30 teams over a 23-period season -- the shape the audit named.
    max(18, 1 + 23) = 24 rows available, n_teams - 2 = 28 needed."""
    standings = [_standings_team(i) for i in range(1, n_teams + 1)]
    arc = [{'team_id': t['team_id'], 'team_abbrev': t['team_abbrev'],
            'period': p, 'standings_rank': (t['team_id'] % n_teams) + 1}
           for t in standings for p in range(1, periods + 1)]
    finishes = [{'team_id': t['team_id'], 'season_year': 2025,
                 'finish': (t['team_id'] % n_teams) + 1,
                 'wins': 10, 'losses': 8, 'ties': 0,
                 'is_champion': t['team_id'] == 1}
                for t in standings]
    return standings, arc, finishes


def test_thirty_team_standings_side_table_does_not_run_off_the_end():
    """The regression itself: rows[2 + k] raised IndexError once the side
    table was taller than the left-hand blocks."""
    standings, arc, finishes = _big_league()
    rows = almanac_sheets.build_advanced_standings_tab_rows(
        standings, [], [_spec('HR', 'hitting'), _spec('L', 'pitching')],
        2026, rank_arc_rows=arc, finishes_rows=finishes)
    assert rows[0] == ['Advanced Standings: 2026']


def test_every_team_gets_a_side_table_row():
    """Padding has to ADD the rows, not truncate the table to fit -- all
    30 owners appear in the finishes matrix."""
    standings, arc, finishes = _big_league()
    rows = almanac_sheets.build_advanced_standings_tab_rows(
        standings, [], [_spec('HR', 'hitting'), _spec('L', 'pitching')],
        2026, rank_arc_rows=arc, finishes_rows=finishes)

    flat = {str(c) for row in rows for c in row}
    for team_id in range(1, 31):
        assert f'Owner {team_id}' in flat, f'Owner {team_id} lost from the tab'


def test_fourteen_team_shape_is_unchanged():
    """Kyle's own league shape must be untouched by the pad -- it never
    needed one (max(18, 1+17) = 18 >= 12)."""
    standings, arc, finishes = _big_league(n_teams=14, periods=17)
    rows = almanac_sheets.build_advanced_standings_tab_rows(
        standings, [], [_spec('HR', 'hitting'), _spec('L', 'pitching')],
        2026, rank_arc_rows=arc, finishes_rows=finishes)

    flat = {str(c) for row in rows for c in row}
    for team_id in range(1, 15):
        assert f'Owner {team_id}' in flat
