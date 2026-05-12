"""Tests for output/stat_catalog.py.

Two flavors:
  - Pure-function tests for to_leaderboard_name (no Snowflake; default suite).
  - Warehouse-marked tests that hit Snowflake to verify the helpers return
    the right shape against the post-B1 seed. These check that the helpers
    are a superset of the Python truth (formatters.STAT_DISPLAY,
    formatters.STAT_ABBREV, records._IMPLICIT_POLARITY) so sub-chunk G can
    rewire consumers without losing entries.
"""

import pytest

import stat_catalog
from stat_catalog import (
    SEED_TO_LEADERBOARD,
    get_abbrev_map,
    get_always_tracked,
    get_derived_exprs,
    get_display_map,
    get_polarity_map,
    get_record_candidates,
    to_leaderboard_name,
)


# ---------------------------------------------------------------------------
# Pure-function tests (no Snowflake)
# ---------------------------------------------------------------------------

class TestToLeaderboardName:
    def test_translates_known_pairs(self):
        assert to_leaderboard_name('1B') == 'SINGLES'
        assert to_leaderboard_name('2B') == 'DOUBLES'
        assert to_leaderboard_name('3B') == 'TRIPLES'
        assert to_leaderboard_name('64') == 'SHO'

    def test_passes_through_unmapped(self):
        # Most stat_names match between seed and leaderboard (HR, RBI, K, ...)
        assert to_leaderboard_name('HR') == 'HR'
        assert to_leaderboard_name('B_BB') == 'B_BB'
        assert to_leaderboard_name('K_PER_9') == 'K_PER_9'  # post-B1 rename

    def test_seed_to_leaderboard_minimal_after_b1(self):
        # B1's K/9 -> K_PER_9 rename retired two entries; only the four
        # name-shape mismatches remain that can't be renamed (1B/2B/3B in
        # the breakdown VARIANT, 64 as ESPN's shutout stat ID).
        assert set(SEED_TO_LEADERBOARD) == {'1B', '2B', '3B', '64'}


# ---------------------------------------------------------------------------
# Warehouse tests — hit Snowflake
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope='module')
def _clear_catalog_cache():
    """Make sure the lru_cache doesn't leak fixture state between modules."""
    stat_catalog._load_catalog.cache_clear()
    for fn in (get_display_map, get_abbrev_map, get_polarity_map,
               get_always_tracked, get_record_candidates, get_derived_exprs):
        fn.cache_clear()
    yield


@pytest.mark.warehouse
class TestDisplayMap:
    def test_shape_is_dict(self):
        m = get_display_map()
        assert isinstance(m, dict)
        assert len(m) > 0

    def test_known_stats_present(self):
        m = get_display_map()
        assert m['HR'] == 'Home Runs'
        assert m['RBI'] == 'RBIs'
        assert m['B_BB'] == 'Walks (Batter)'
        assert m['HBP_P'] == 'Hit Batters'  # disambiguated from batter HBP

    def test_translation_applied(self):
        # Seed has '1B', '2B', '3B', '64'; leaderboard names are
        # 'SINGLES', 'DOUBLES', 'TRIPLES', 'SHO'.
        m = get_display_map()
        assert m['SINGLES'] == 'Singles'
        assert m['DOUBLES'] == 'Doubles'
        assert m['TRIPLES'] == 'Triples'
        assert m['SHO'] == 'Shutouts'
        # Seed-side names should NOT appear.
        assert '1B' not in m
        assert '64' not in m

    def test_score_stats_present_post_b1(self):
        # CALCULATED_* and PLATFORM_* were added in B1.
        m = get_display_map()
        assert m['CALCULATED_POINTS'] == 'Total Points'
        assert m['PLATFORM_HITTING_PTS'] == 'Platform Hitting Points'

    def test_python_truth_is_subset(self):
        # Sub-chunk G's rewire requires the seed helper to cover everything
        # formatters.STAT_DISPLAY does. Subset assertion catches drift if
        # someone adds a Python entry without seeding it.
        import formatters
        helper = get_display_map()
        missing = {k: v for k, v in formatters.STAT_DISPLAY.items() if helper.get(k) != v}
        assert not missing, f"STAT_DISPLAY entries not matched by helper: {missing}"


@pytest.mark.warehouse
class TestAbbrevMap:
    def test_known_abbrevs(self):
        m = get_abbrev_map()
        assert m['B_BB'] == 'BB'       # batter walks
        assert m['B_SO'] == 'K'        # batter strikeouts
        assert m['HBP_P'] == 'HBP'     # pitcher hit-batters (collides with batter HBP intentionally)
        assert m['OUTS'] == 'IP'

    def test_python_truth_is_subset(self):
        import formatters
        helper = get_abbrev_map()
        missing = {k: v for k, v in formatters.STAT_ABBREV.items() if helper.get(k) != v}
        assert not missing, f"STAT_ABBREV entries not matched: {missing}"


@pytest.mark.warehouse
class TestPolarityMap:
    def test_known_polarities(self):
        m = get_polarity_map()
        assert m['HR'] == 'positive'
        assert m['ER'] == 'negative'
        assert m['ERA'] == 'negative'
        assert m['WHIP'] == 'negative'
        assert m['CALCULATED_POINTS'] == 'positive'
        assert m['WASTED_POINTS'] == 'negative'

    def test_no_neutral_for_record_candidates(self):
        # Anything tracked as a record should have non-neutral polarity,
        # otherwise the consumer side can't pick a direction.
        m = get_polarity_map()
        for stat in get_record_candidates():
            pol = m.get(stat)
            assert pol in ('positive', 'negative'), \
                f"{stat} is a record candidate but polarity={pol!r}"


@pytest.mark.warehouse
class TestAlwaysTracked:
    def test_pre_b1_set_preserved(self):
        # B1-fix locked these as the canonical force-surface set. Any
        # change should be a deliberate decision.
        expected = {'H', 'TB', 'XBH', 'SF', 'ER', 'PA'}
        assert get_always_tracked() == expected

    def test_new_b1_rows_not_force_surfaced(self):
        # PLATFORM_*, WASTED_POINTS, rates, derived (except PA) should NOT
        # short-circuit should_track_record in v1.0 -- they only surface if
        # the polarity rule passes.
        ts = get_always_tracked()
        for stat in ('PLATFORM_POINTS', 'PLATFORM_HITTING_PTS', 'PLATFORM_PITCHING_PTS',
                     'CALCULATED_POINTS', 'CALCULATED_HITTING_PTS', 'CALCULATED_PITCHING_PTS',
                     'WASTED_POINTS', 'ERA', 'WHIP',
                     'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9',
                     'SB_CS', 'W_L', 'SV_BLSV'):
            assert stat not in ts, f"{stat} unexpectedly in always_tracked"


@pytest.mark.warehouse
class TestRecordCandidates:
    def test_size_matches_b1_expectation(self):
        # Per B1's gap report: 57 record candidates (the regen script's
        # totals line). Anchored here so future seed edits surface as
        # explicit count changes.
        assert len(get_record_candidates()) == 57

    def test_known_record_stats(self):
        rc = get_record_candidates()
        # Counting stats with positive polarity.
        for stat in ('HR', 'RBI', 'R', 'SB', 'K', 'W', 'SV'):
            assert stat in rc
        # Score stats added in B1.
        for stat in ('CALCULATED_POINTS', 'PLATFORM_POINTS'):
            assert stat in rc
        # Rate stats.
        for stat in ('ERA', 'WHIP', 'K_PER_9'):
            assert stat in rc
        # Derived stats.
        for stat in ('PA', 'SB_CS', 'W_L', 'SV_BLSV'):
            assert stat in rc


@pytest.mark.warehouse
class TestDerivedExprs:
    def test_four_derived_stats(self):
        d = get_derived_exprs()
        assert d['PA'] == 'ab + b_bb + hbp + sf'
        assert d['SB_CS'] == 'sb - cs'
        assert d['W_L'] == 'w - l'
        assert d['SV_BLSV'] == 'sv - blsv'

    def test_only_derived_have_expr(self):
        d = get_derived_exprs()
        # Rate stats, score stats, WASTED_POINTS are computed at mart or
        # carried on the fct -- they don't have a Jinja-embeddable expr.
        for stat in ('ERA', 'WHIP', 'CALCULATED_POINTS', 'WASTED_POINTS',
                     'HR', 'RBI'):
            assert stat not in d, f"{stat} unexpectedly has derivation_expr"
