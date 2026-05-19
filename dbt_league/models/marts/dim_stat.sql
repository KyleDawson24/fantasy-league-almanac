-- dim_stat.sql
-- Consumer-facing contract layer for stat metadata. Thin view over the
-- stat_classification seed.
--
-- v1.1.0 DAG cleanup: introduces a public-facing dim so output scripts
-- and external readers depend on a "first-class" model rather than an
-- internal seed. The seed remains the source of truth for editors
-- (CSV edits + reseed); the dim is what consumers reference.
--
-- Adds vs. the seed:
--   - leaderboard_name -- seed_name post the project's documented
--     translation ('1B' -> 'SINGLES', '2B' -> 'DOUBLES', '3B' ->
--     'TRIPLES', '30' -> 'CYC', '64' -> 'SHO'; else pass-through). This
--     CASE is the SINGLE place the translation lives. v1.1.0 round 3
--     eliminated the Python-side mirror (output/stat_catalog.py used to
--     carry SEED_TO_LEADERBOARD + to_leaderboard_name) and the duplicate
--     CASE in mart_stat_leaderboard.sql's compile-time loop -- both now
--     read leaderboard_name from here.
--   - All other seed columns carried through unchanged.
--
-- Materialization: view. Seed is ~100 rows; no aggregation, no caching
-- value at this layer. Caching boundary for Python consumers is the
-- @lru_cache in output/stat_catalog.py::_load_catalog (one fetch per
-- script process; reshaped into per-helper dicts).
--
-- Runtime / scale notes: the dim adds zero runtime cost over reading
-- the seed directly. mart_stat_leaderboard's Jinja loop still iterates
-- the seed directly via run_query at parse time (bypasses dbt's ref()
-- chain), so the compiled SQL doesn't reference dim_stat or
-- stat_classification at run-time. The dim's value is architectural:
-- it gives the consumer layer a documented contract independent of the
-- seed's internal schema, which lets the seed evolve without breaking
-- public outputs.

{{ config(materialized='view') }}

select
    -- Primary key on the seed side -- matches the breakdown VARIANT key.
    stat_name,

    -- Consumer-facing leaderboard column name. Translation applied for
    -- the small set of seed names that differ from leaderboard names;
    -- otherwise passes through. Keep this CASE in sync with
    -- output/stat_catalog.py::SEED_TO_LEADERBOARD and the CASE block
    -- in mart_stat_leaderboard.sql until those are repointed at dim_stat.
    case stat_name
        when '1B' then 'SINGLES'
        when '2B' then 'DOUBLES'
        when '3B' then 'TRIPLES'
        when '30' then 'CYC'
        when '64' then 'SHO'
        else stat_name
    end as leaderboard_name,

    espn_stat_id,
    stat_category,
    espn_stat_label,
    display_name,
    abbrev,
    is_counting,
    is_derived,
    derivation_expr,
    auto_tracked,
    is_record_candidate,
    polarity,
    notes

from {{ ref('stat_classification') }}
