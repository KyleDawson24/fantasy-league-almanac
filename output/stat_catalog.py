"""output/stat_catalog.py

Seed-driven stat metadata helpers. Single source of truth for stat display
labels, polarity, always-tracked semantics, and derivation expressions --
reads from the `stat_classification` table populated by the dbt seed at
`dbt_league/seeds/stat_classification.csv`.

Phase 7 B2: helpers built and verified; consumers (formatters.py,
records.py) still read from their hardcoded Python dicts. Sub-chunk G
rewires consumers to call here.

The helpers return dict[str, ...] / frozenset[str] keyed by LEADERBOARD
column names (uppercase, post seed -> leaderboard translation -- '1B' ->
'SINGLES', '64' -> 'SHO') so consumer lookups against
`mart_stat_leaderboard.stat_name` land directly without the caller
needing to know about the seed-side naming.

Caching: each helper is `@lru_cache(maxsize=1)`. The underlying catalog
fetch happens once per process. Test code can call
`stat_catalog._load_catalog.cache_clear()` to force a reload (e.g. after
a `dbt seed` between phases of a test).
"""

from functools import lru_cache

from db import query_snowflake


# Seed stat_name -> leaderboard column name translation. Used for stats
# where the seed-side name (matching ESPN's breakdown VARIANT key or an
# espn-stat-id placeholder) differs from the leaderboard column name.
#
# Phase 7 B1 retired the K/9 -> K_PER_9 and K/BB -> K_PER_BB entries by
# renaming the seed rows directly; the rate-stat divergence is now
# represented at the seed level. Remaining entries handle stat-name
# shapes that can't be safely renamed because they appear in the
# breakdown VARIANT and the stg FK test pins them. v1.x added '30' ->
# 'CYC' (Hit for the Cycle) when stat 30 was promoted to a tracked
# stat with a wide column.
SEED_TO_LEADERBOARD = {
    '1B': 'SINGLES',
    '2B': 'DOUBLES',
    '3B': 'TRIPLES',
    '30': 'CYC',
    '64': 'SHO',
}


def to_leaderboard_name(seed_stat: str) -> str:
    """Translate seed stat_name to leaderboard column name. Falls through
    to the input for stats with matching names on both sides."""
    return SEED_TO_LEADERBOARD.get(seed_stat, seed_stat)


@lru_cache(maxsize=1)
def _load_catalog() -> tuple:
    """Fetch the stat catalog once per process. Reads from dim_stat
    (the consumer-facing contract layer over the stat_classification
    seed) so public outputs depend on a mart-layer model rather than
    an internal seed. Returns a tuple of dicts (tuple, not list, for
    hash-stability)."""
    rows = query_snowflake("""
        SELECT
            stat_name, leaderboard_name, espn_stat_id, stat_category,
            espn_stat_label, display_name, abbrev,
            is_counting, is_derived, derivation_expr,
            auto_tracked, is_record_candidate, polarity,
            notes
        FROM dim_stat
    """)
    return tuple(rows)


@lru_cache(maxsize=1)
def get_display_map() -> dict:
    """leaderboard_name -> human-readable display label. e.g.
    'HR' -> 'Home Runs', 'B_BB' -> 'Walks (Batter)'. Rows with empty/null
    display_name are skipped. Replaces formatters.STAT_DISPLAY."""
    return {
        to_leaderboard_name(r['stat_name']): r['display_name']
        for r in _load_catalog()
        if r['display_name']
    }


@lru_cache(maxsize=1)
def get_abbrev_map() -> dict:
    """leaderboard_name -> compact abbreviation. e.g. 'B_BB' -> 'BB',
    'HBP_P' -> 'HBP'. Replaces formatters.STAT_ABBREV."""
    return {
        to_leaderboard_name(r['stat_name']): r['abbrev']
        for r in _load_catalog()
        if r['abbrev']
    }


@lru_cache(maxsize=1)
def get_polarity_map() -> dict:
    """leaderboard_name -> 'positive' | 'negative' | 'neutral'. The seed
    bakes in what records.py used to compute at runtime by merging
    stg_scoring_settings (sign of points_per_unit) with _IMPLICIT_POLARITY.
    Replaces records._IMPLICIT_POLARITY + get_stat_polarity +
    get_effective_polarity."""
    return {
        to_leaderboard_name(r['stat_name']): r['polarity']
        for r in _load_catalog()
        if r['polarity']
    }


@lru_cache(maxsize=1)
def get_auto_tracked() -> frozenset:
    """Leaderboard stat_names where auto_tracked=true. These stats surface
    as records regardless of the league's scoring settings (they have no
    points_per_unit-derived polarity to drive should_track_record).
    Implementation short-circuits should_track_record to True at team
    grain so both directions surface."""
    return frozenset(
        to_leaderboard_name(r['stat_name'])
        for r in _load_catalog()
        if r['auto_tracked']
    )


@lru_cache(maxsize=1)
def get_record_candidates() -> frozenset:
    """Leaderboard stat_names where is_record_candidate=true. These are
    the stats that surface in records output -- the polarity-aware filter
    rule baked into the seed during B1. Equivalent to running
    should_track_record(grain='team', direction='most', ...) for every
    leaderboard column."""
    return frozenset(
        to_leaderboard_name(r['stat_name'])
        for r in _load_catalog()
        if r['is_record_candidate']
    )


@lru_cache(maxsize=1)
def get_derived_exprs() -> dict:
    """leaderboard_name -> SQL expression for stats computed inline at the
    mart (the fct doesn't carry a column for them). Used by sub-chunk F's
    Jinja-loop UNPIVOT rewrite. e.g. 'PA' -> 'ab + b_bb + hbp + sf'.
    Stats without derivation_expr are not included."""
    return {
        to_leaderboard_name(r['stat_name']): r['derivation_expr']
        for r in _load_catalog()
        if r['derivation_expr']
    }
