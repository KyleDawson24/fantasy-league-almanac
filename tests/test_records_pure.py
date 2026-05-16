"""Pure-function tests for output/records.py.

Covers ordinal arithmetic, the polarity-aware filter rules, the player-
stat-value extractor for derived stats, the tie-collapse algorithm
(non-saturated cases — saturated case requires a Snowflake call to
count_value_occurrences and is out of scope for pure tests), and the
effective-polarity merge logic (with the seed-fetching call patched).

Importing records does NOT fire any queries; it just calls load_dotenv.
The functions under test are themselves pure or are tested via patches.
"""

import pytest

import records


# ---------- ordinal ----------

class TestOrdinal:
    def test_first(self):
        assert records.ordinal(1) == "1st"

    def test_second(self):
        assert records.ordinal(2) == "2nd"

    def test_third(self):
        assert records.ordinal(3) == "3rd"

    def test_fourth(self):
        assert records.ordinal(4) == "4th"

    def test_eleven_is_th(self):
        assert records.ordinal(11) == "11th"

    def test_twelve_is_th(self):
        assert records.ordinal(12) == "12th"

    def test_thirteen_is_th(self):
        assert records.ordinal(13) == "13th"

    def test_twentyone_is_st(self):
        assert records.ordinal(21) == "21st"

    def test_twentytwo_is_nd(self):
        assert records.ordinal(22) == "22nd"

    def test_twentythree_is_rd(self):
        assert records.ordinal(23) == "23rd"

    def test_one_hundred_one_is_st(self):
        assert records.ordinal(101) == "101st"

    def test_one_hundred_eleven_is_th(self):
        assert records.ordinal(111) == "111th"

    def test_one_thousand(self):
        assert records.ordinal(1000) == "1000th"


# ---------- _player_stat_value ----------

class TestPlayerStatValue:
    def test_direct_column(self):
        # Most stats are a direct lookup of the lowercased column name.
        assert records._player_stat_value({'hr': 5}, 'HR') == 5

    def test_missing_column_returns_zero(self):
        # `or 0` fallback in the impl.
        assert records._player_stat_value({}, 'HR') == 0

    def test_none_value_returns_zero(self):
        assert records._player_stat_value({'hr': None}, 'HR') == 0

    def test_pa_derived_stat(self):
        # PA = AB + B_BB + HBP + SF
        row = {'ab': 23, 'b_bb': 2, 'hbp': 1, 'sf': 0}
        assert records._player_stat_value(row, 'PA') == 26

    def test_pa_with_missing_keys(self):
        row = {'ab': 5}
        assert records._player_stat_value(row, 'PA') == 5

    def test_sb_cs_derived_stat(self):
        # Net stolen bases (can be negative).
        row = {'sb': 3, 'cs': 5}
        assert records._player_stat_value(row, 'SB_CS') == -2

    def test_w_l_derived_stat(self):
        row = {'w': 2, 'l': 1}
        assert records._player_stat_value(row, 'W_L') == 1

    def test_sv_blsv_derived_stat(self):
        row = {'sv': 4, 'blsv': 1}
        assert records._player_stat_value(row, 'SV_BLSV') == 3

    def test_no_breakdown_stats_return_none(self):
        # Rate stats and WASTED_POINTS have no per-player breakdown story.
        for stat in ('ERA', 'WHIP', 'K_PER_9', 'WASTED_POINTS', 'HR_PER_9'):
            assert records._player_stat_value({'era': 3.5}, stat) is None, (
                f"{stat} should return None"
            )


# ---------- best_or_worst_label ----------

class TestBestOrWorstLabel:
    def test_positive_stat_most_is_best(self):
        polarity = {'HR': 'positive'}
        assert records.best_or_worst_label('HR', 'most', polarity) == 'Best'

    def test_positive_stat_fewest_is_worst(self):
        polarity = {'HR': 'positive'}
        assert records.best_or_worst_label('HR', 'fewest', polarity) == 'Worst'

    def test_negative_stat_most_is_worst(self):
        polarity = {'ER': 'negative'}
        # Most ER allowed is the worst pitching outcome.
        assert records.best_or_worst_label('ER', 'most', polarity) == 'Worst'

    def test_negative_stat_fewest_is_best(self):
        polarity = {'ER': 'negative'}
        assert records.best_or_worst_label('ER', 'fewest', polarity) == 'Best'

    def test_unknown_stat_defaults_positive(self):
        # Stats not in the map default to positive (most common case).
        assert records.best_or_worst_label('MYSTERY_STAT', 'most', {}) == 'Best'
        assert records.best_or_worst_label('MYSTERY_STAT', 'fewest', {}) == 'Worst'


# ---------- should_track_record ----------

class TestShouldTrackRecord:
    """Polarity-aware filter rules:
      Player grain: only score-level stats; both directions.
      Team score columns: both directions.
      Team auto_tracked stat: both directions regardless of polarity
        (force-surface for stats not in the league's scoring settings).
      Team positive stat: both directions.
      Team negative stat: 'most' (most-of-bad) only.
      Neutral / unknown: skipped entirely.
    """

    def setup_method(self):
        self.polarity = {
            'HR': 'positive',
            'ER': 'negative',
            'NEUTRAL_STAT': 'neutral',
        }
        self.auto_tracked = frozenset({'TB'})

    def test_player_grain_score_stat_passes(self):
        for direction in ('most', 'fewest'):
            assert records.should_track_record(
                'player', 'CALCULATED_POINTS', direction,
                self.polarity, self.auto_tracked,
            )

    def test_player_grain_individual_stat_dropped(self):
        # Player records are score-level only.
        for direction in ('most', 'fewest'):
            assert not records.should_track_record(
                'player', 'HR', direction,
                self.polarity, self.auto_tracked,
            )

    def test_team_score_stat_both_directions(self):
        for direction in ('most', 'fewest'):
            assert records.should_track_record(
                'team', 'CALCULATED_POINTS', direction,
                self.polarity, self.auto_tracked,
            )

    def test_team_auto_tracked_both_directions(self):
        # TB is in the auto_tracked set (auto_tracked=true) even though not
        # in the polarity dict -- a stat not in scoring settings still surfaces.
        for direction in ('most', 'fewest'):
            assert records.should_track_record(
                'team', 'TB', direction,
                self.polarity, self.auto_tracked,
            )

    def test_team_positive_stat_both_directions(self):
        for direction in ('most', 'fewest'):
            assert records.should_track_record(
                'team', 'HR', direction,
                self.polarity, self.auto_tracked,
            )

    def test_team_negative_stat_most_only(self):
        # Most ER allowed is a record. Fewest ER allowed is *trivially* zero
        # for many teams every week — not a meaningful record.
        assert records.should_track_record(
            'team', 'ER', 'most',
            self.polarity, self.auto_tracked,
        )
        assert not records.should_track_record(
            'team', 'ER', 'fewest',
            self.polarity, self.auto_tracked,
        )

    def test_team_neutral_stat_dropped(self):
        for direction in ('most', 'fewest'):
            assert not records.should_track_record(
                'team', 'NEUTRAL_STAT', direction,
                self.polarity, self.auto_tracked,
            )

    def test_team_unknown_stat_dropped(self):
        for direction in ('most', 'fewest'):
            assert not records.should_track_record(
                'team', 'NOT_IN_POLARITY', direction,
                self.polarity, self.auto_tracked,
            )

    def test_auto_tracked_default_none_safe(self):
        # auto_tracked=None → polarity rule applies normally.
        assert records.should_track_record(
            'team', 'HR', 'most', self.polarity, None,
        )
        assert not records.should_track_record(
            'team', 'ER', 'fewest', self.polarity, None,
        )


# ---------- _orchestrator_filter ----------

class TestOrchestratorFilter:
    """Extends should_track_record by also passing team-grain non-seed
    stats (rate stats, WASTED_POINTS, derived counts) in BOTH directions.
    """

    def setup_method(self):
        self.polarity = {'HR': 'positive', 'ER': 'negative'}
        self.auto_tracked = frozenset({'TB'})

    def test_passes_should_track_record_passers(self):
        # Any (grain, stat, direction) that passes should_track_record
        # passes _orchestrator_filter too.
        assert records._orchestrator_filter(
            'team', 'HR', 'most', self.polarity, self.auto_tracked,
        )

    def test_team_grain_rate_stat_both_directions(self):
        for direction in ('most', 'fewest'):
            assert records._orchestrator_filter(
                'team', 'ERA', direction, self.polarity, self.auto_tracked,
            )
            assert records._orchestrator_filter(
                'team', 'WHIP', direction, self.polarity, self.auto_tracked,
            )

    def test_team_grain_wasted_points_both_directions(self):
        for direction in ('most', 'fewest'):
            assert records._orchestrator_filter(
                'team', 'WASTED_POINTS', direction,
                self.polarity, self.auto_tracked,
            )

    def test_team_grain_derived_counting_stat_both_directions(self):
        for direction in ('most', 'fewest'):
            for stat in ('PA', 'SB_CS', 'W_L', 'SV_BLSV'):
                assert records._orchestrator_filter(
                    'team', stat, direction,
                    self.polarity, self.auto_tracked,
                )

    def test_player_grain_non_seed_stat_dropped(self):
        # Non-seed stats only pass at TEAM grain — player grain is locked
        # to score stats by should_track_record.
        for direction in ('most', 'fewest'):
            assert not records._orchestrator_filter(
                'player', 'ERA', direction,
                self.polarity, self.auto_tracked,
            )


# ---------- _collapse_one_group (non-saturated cases) ----------

def _row(rank, value, **identity):
    """Build a leaderboard-shaped row with sensible defaults for
    collapse tests. identity overrides team_id / player_id / etc."""
    base = {
        'entity_grain': 'team',
        'stat_name': 'HR',
        'record_direction': 'most',
        'rank': rank,
        'stat_value': value,
        'season_year': 2025,
        'matchup_period': 5,
        'team_id': identity.get('team_id', 1),
        'team_name': identity.get('team_name', 'Team A'),
        'team_abbrev': identity.get('team_abbrev', 'TA'),
        'owner_name': identity.get('owner_name', 'Owner A'),
        'player_id': identity.get('player_id'),
        'player_name': identity.get('player_name'),
        'display_name': identity.get('display_name'),
    }
    base.update(identity)
    return base


class TestCollapseOneGroupNonSaturated:
    def test_all_distinct_under_cap(self):
        group = [_row(1, 10), _row(2, 8), _row(3, 6)]
        result = records._collapse_one_group(group, max_n=5)
        assert len(result) == 3
        assert all(not r.get('is_collapsed') for r in result)
        assert [r['rank'] for r in result] == [1, 2, 3]

    def test_distinct_filling_cap_exactly(self):
        group = [_row(i, 10 - i) for i in range(1, 6)]  # ranks 1..5, distinct values
        result = records._collapse_one_group(group, max_n=5)
        assert len(result) == 5
        assert all(not r.get('is_collapsed') for r in result)

    def test_distinct_over_cap_truncates(self):
        # 7 distinct values, max_n=5 → returns first 5 (rank 6,7 don't fit
        # but it's not a tier overflow — the loop just stops at used==max_n)
        group = [_row(i, 10 - i) for i in range(1, 8)]
        result = records._collapse_one_group(group, max_n=5)
        assert len(result) == 5
        assert [r['rank'] for r in result] == [1, 2, 3, 4, 5]

    def test_small_overflow_tier_inlines_holders(self):
        # 1 distinct at rank 1, then 3 tied at rank 2 (would push to 4 with
        # max_n=3). Under INLINE_COLLAPSE_THRESHOLD=3 → holders preserved.
        group = [
            _row(1, 10, team_id=1, team_abbrev='A'),
            _row(2, 5,  team_id=2, team_abbrev='B'),
            _row(2, 5,  team_id=3, team_abbrev='C'),
            _row(2, 5,  team_id=4, team_abbrev='D'),
        ]
        result = records._collapse_one_group(group, max_n=3)
        # Rank 1 passes through; the 3-tied tier collapses (inline).
        assert len(result) == 2
        assert not result[0].get('is_collapsed')
        collapsed = result[1]
        assert collapsed['is_collapsed'] is True
        assert collapsed['tie_count'] == 3
        assert len(collapsed['holders']) == 3
        assert {h['team_abbrev'] for h in collapsed['holders']} == {'B', 'C', 'D'}

    def test_large_overflow_tier_count_only(self):
        # 4 tied at top (above INLINE_COLLAPSE_THRESHOLD=3) → collapsed
        # with empty holders, count-only summary.
        group = [
            _row(i, 5, team_id=i, team_abbrev=f'T{i}')
            for i in range(1, 5)
        ]
        result = records._collapse_one_group(group, max_n=3)
        assert len(result) == 1
        collapsed = result[0]
        assert collapsed['is_collapsed'] is True
        assert collapsed['tie_count'] == 4
        assert collapsed['holders'] == []

    def test_collapsed_row_clears_identity_fields(self):
        group = [_row(1, 5, team_id=1, team_abbrev='A')] * 4
        # Re-rank since the helper expects asc by rank
        for i, r in enumerate(group, 1):
            r['rank'] = i
        result = records._collapse_one_group(group, max_n=3)
        collapsed = result[0]
        assert collapsed['team_id'] is None
        assert collapsed['team_name'] is None
        assert collapsed['team_abbrev'] is None
        assert collapsed['owner_name'] is None
        assert collapsed['contributors'] == []

    def test_collapsed_row_keeps_recent_season_mp(self):
        # When tier members span multiple matchups, the collapsed row's
        # season/mp should be the most recent in the visible tier.
        group = [
            _row(1, 5, team_id=1, team_abbrev='A'),
            _row(2, 5, team_id=2, team_abbrev='B'),
            _row(3, 5, team_id=3, team_abbrev='C'),
            _row(4, 5, team_id=4, team_abbrev='D'),
        ]
        group[0]['season_year'], group[0]['matchup_period'] = 2024, 10
        group[1]['season_year'], group[1]['matchup_period'] = 2025, 3
        group[2]['season_year'], group[2]['matchup_period'] = 2025, 7
        group[3]['season_year'], group[3]['matchup_period'] = 2025, 5
        result = records._collapse_one_group(group, max_n=3)
        collapsed = result[0]
        assert collapsed['season_year'] == 2025
        assert collapsed['matchup_period'] == 7  # the latest, not just last in input


# ---------- _collapse_one_group (saturated cases, via DI count_fn) ----------

class TestCollapseOneGroupSaturated:
    """The saturated-tier branch backfills tie_count via the injected
    count_fn. v1.x DI cleanup made this testable without a warehouse;
    pre-DI the only way to exercise this path was the byte-diff golden
    BBCode regression."""

    def test_saturated_tier_uses_count_fn(self):
        # Build a 10-row tier all tied at the same value, ranks 1..10.
        # tier[-1]['rank'] == 10 triggers the saturated branch.
        group = [
            _row(i, 5, team_id=i, team_abbrev=f'T{i}')
            for i in range(1, 11)
        ]
        calls = []

        def mock_count(grain, stat_name, value):
            calls.append((grain, stat_name, value))
            return 247  # truth value beyond the visible 10

        result = records._collapse_one_group(group, max_n=3, count_fn=mock_count)
        assert len(result) == 1
        assert result[0]['is_collapsed'] is True
        assert result[0]['tie_count'] == 247  # from count_fn, not tier_size
        assert calls == [('team', 'HR', 5)]

    def test_saturated_tier_count_fn_none_falls_back_to_tier_size(self):
        # count_fn returning None (the no-fct-counterpart case) falls back
        # to visible tier_size -- still an undercount when saturated, but
        # honest about it.
        group = [
            _row(i, 5, team_id=i, team_abbrev=f'T{i}')
            for i in range(1, 11)
        ]

        def mock_count_returning_none(grain, stat_name, value):
            return None

        result = records._collapse_one_group(
            group, max_n=3, count_fn=mock_count_returning_none,
        )
        assert result[0]['tie_count'] == 10  # fell back to visible tier_size

    def test_saturated_tier_count_fn_absent_falls_back_to_tier_size(self):
        # count_fn omitted entirely (the default-None path) also falls
        # back. Matches the count_fn-returns-None case; lets non-warehouse
        # callers use collapse_ties without needing a counter.
        group = [
            _row(i, 5, team_id=i, team_abbrev=f'T{i}')
            for i in range(1, 11)
        ]
        result = records._collapse_one_group(group, max_n=3)
        assert result[0]['tie_count'] == 10

    def test_non_saturated_tier_skips_count_fn(self):
        # Tier sits at ranks 1..3 (well under 10) so the algorithm trusts
        # visible tier_size. count_fn should not be invoked.
        group = [
            _row(i, 5, team_id=i, team_abbrev=f'T{i}')
            for i in range(1, 5)  # 4 rows at rank 1..4, all tied at 5
        ]
        calls = []

        def spy_count(grain, stat_name, value):
            calls.append((grain, stat_name, value))
            return 999

        result = records._collapse_one_group(group, max_n=3, count_fn=spy_count)
        assert result[0]['is_collapsed'] is True
        assert result[0]['tie_count'] == 4  # visible tier_size, not 999
        assert calls == []  # count_fn never called -- not saturated


# ---------- collapse_ties (multi-group integration) ----------

class TestCollapseTies:
    def test_empty_input(self):
        assert records.collapse_ties([]) == []

    def test_groups_independent(self):
        # Two groups (different stat_name) collapse independently.
        rows = [
            _row(1, 10, team_id=1),
            _row(2, 8,  team_id=2),
            # Different stat_name → new group
            {**_row(1, 5, team_id=3), 'stat_name': 'K'},
            {**_row(2, 3, team_id=4), 'stat_name': 'K'},
        ]
        result = records.collapse_ties(rows, max_n=5)
        # All 4 rows pass through (no overflow tiers).
        assert len(result) == 4

    def test_groups_partition_by_direction(self):
        rows = [
            _row(1, 10, team_id=1),
            {**_row(1, 0, team_id=2), 'record_direction': 'fewest'},
        ]
        result = records.collapse_ties(rows, max_n=5)
        assert len(result) == 2


# Phase 7 G2: TestGetEffectivePolarity (5 tests) deleted along with the
# get_stat_polarity / _IMPLICIT_POLARITY / get_effective_polarity functions
# they tested. The polarity merge moved to the seed at B1 (regen-time);
# the runtime accessor is stat_catalog.get_polarity_map(). Polarity values
# are anchored at the seed level by tests/test_stat_catalog.py::
# TestPolarityMap::test_known_polarities (warehouse-marked).
