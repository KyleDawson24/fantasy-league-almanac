-- int_franchise_registry.sql
-- Platform-general OBSERVED franchise registry (MLB-64): one row per
-- (league_key, franchise_id) with the franchise's observed display name +
-- abbrev, unioned across platforms. This is the "what your league's history
-- makes available to us" layer -- dim_franchise resolves the human's continuity
-- overrides (the franchise_lineage seed) onto THIS. Keeping the observed input
-- behind a general seam is what makes the whole continuity pipeline
-- platform-agnostic: a new platform (ESPN's "Baseball Buns In The Sun", etc.)
-- adds ONE branch here in the same shape, and optionally rows to the
-- franchise_lineage seed -- no league needs an override to flow through.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, franchise_id).
-- ==========================================================================
{{ config(materialized='view') }}

-- CBS: the curated registry (display abbrevs + latest name) built from the
-- parsed UI standings history.
select
    league_key,
    cast(franchise_id as varchar) as franchise_id,
    franchise_name                as observed_name,
    abbrev                        as observed_abbrev
from {{ ref('cbs_franchises') }}

-- Future platforms plug in here with the SAME shape, e.g. ESPN:
--   union all
--   select league_key, cast(team_id as varchar), team_name, team_abbrev
--   from <espn per-franchise registry>
-- (ESPN carries per-season names in mart_team_season_standings today; a thin
--  franchise-grain staging model would be its registry. No override needed to
--  appear here -- unlinked franchises just resolve to themselves.)
