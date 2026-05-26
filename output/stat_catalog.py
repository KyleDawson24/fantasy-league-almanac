"""output/stat_catalog.py

Stat metadata helpers. Single source of truth at the Python layer for
stat display labels, polarity, auto-tracked semantics, and derivation
expressions -- reads from the `dim_stat` mart (the consumer-facing
contract over the stat_classification seed).

The helpers return dict[str, ...] / frozenset[str] keyed by LEADERBOARD
column names (uppercase, post seed -> leaderboard translation -- '1B' ->
'SINGLES', '30' -> 'CYC', '64' -> 'SHO') so consumer lookups against
`mart_stat_leaderboard.stat_name` land directly without the caller
needing to know about the seed-side naming. v1.1.0: the translation now
lives canonically in dim_stat's `leaderboard_name` column; helpers read
that column directly instead of re-applying a Python-side mapping.

Caching: each helper is `@lru_cache(maxsize=1)`. The underlying catalog
fetch happens once per process. Test code can call
`stat_catalog._load_catalog.cache_clear()` to force a reload (e.g. after
a `dbt seed` between phases of a test).
"""

from functools import lru_cache

from db import query_snowflake


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
            qualifier_stat, qualifier_min,
            notes
        FROM dim_stat
    """)
    return tuple(rows)


@lru_cache(maxsize=1)
def get_display_map() -> dict:
    """leaderboard_name -> human-readable display label. e.g.
    'HR' -> 'Home Runs', 'B_BB' -> 'Walks (Batter)'. Rows with empty/null
    display_name are skipped."""
    return {
        r['leaderboard_name']: r['display_name']
        for r in _load_catalog()
        if r['display_name']
    }


@lru_cache(maxsize=1)
def get_abbrev_map() -> dict:
    """leaderboard_name -> compact abbreviation. e.g. 'B_BB' -> 'BB',
    'HBP_P' -> 'HBP'."""
    return {
        r['leaderboard_name']: r['abbrev']
        for r in _load_catalog()
        if r['abbrev']
    }


@lru_cache(maxsize=1)
def get_polarity_map() -> dict:
    """leaderboard_name -> 'positive' | 'negative' | 'neutral'. The seed
    bakes in what records.py used to compute at runtime by merging
    stg_scoring_settings (sign of points_per_unit) with project-defined
    overrides for stats not in scoring settings."""
    return {
        r['leaderboard_name']: r['polarity']
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
        r['leaderboard_name']
        for r in _load_catalog()
        if r['auto_tracked']
    )


@lru_cache(maxsize=1)
def get_record_candidates() -> frozenset:
    """Leaderboard stat_names where is_record_candidate=true. These are
    the stats that surface in records output -- the polarity-aware filter
    rule baked into the seed."""
    return frozenset(
        r['leaderboard_name']
        for r in _load_catalog()
        if r['is_record_candidate']
    )


@lru_cache(maxsize=1)
def get_rate_qualifiers() -> dict:
    """leaderboard_name -> (qualifier_stat, qualifier_min) for rate stats
    that have a min-volume gate (v1.1.1). Driven by the qualifier_stat /
    qualifier_min columns on dim_stat (seed-side). Non-rate stats are
    not included. Consumers reading rate records from mart_stat_leaderboard
    use this to label the threshold and to project the qualifier value
    (AB count for hitting rates, OUTS count for pitching rates) for
    display alongside each record."""
    return {
        r['leaderboard_name']: (r['qualifier_stat'], int(r['qualifier_min']))
        for r in _load_catalog()
        if r.get('qualifier_min') is not None
    }


@lru_cache(maxsize=1)
def get_derived_exprs() -> dict:
    """leaderboard_name -> SQL expression for stats computed inline at the
    mart (the fct doesn't carry a column for them). Used by the mart's
    Jinja-loop UNPIVOT. e.g. 'PA' -> 'ab + b_bb + hbp + sf'. Stats
    without derivation_expr are not included."""
    return {
        r['leaderboard_name']: r['derivation_expr']
        for r in _load_catalog()
        if r['derivation_expr']
    }
