-- stg_cbs__team_owners.sql
-- The CBS team -> owner bridge. GROWN to full history (MLB-64): it now sources
-- the per-season resolved ownership (int_cbs__team_owner_season), which turns
-- the curated owner-by-year NAMES into canonical owner ids. It was current-era
-- only (the seed attested just today's owners); the continuity-sheet harvest
-- lands the rest, so this serves every season the historian has filled --
-- and the shared owner chain (dim_owner -> dim_team_owner) serves owner
-- history downstream with NO shape change, exactly as this model's prior
-- header promised.
--
-- Same grain + owner_id contract as stg_team_owners so the ESPN branch of the
-- shared dims is untouched; owner_name rides along for dim_owner to display
-- historical owners the nickname seed doesn't cover.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, team_id, owner_id).
--   Co-owned teams yield multiple rows per (league, season, team).
-- ==========================================================================

{{ config(materialized='view') }}

select
    league_key,
    season_year,
    franchise_id as team_id,
    owner_id,
    owner_name
from {{ ref('int_cbs__team_owner_season') }}
qualify row_number() over (
    partition by league_key, season_year, franchise_id, owner_id
    order by owner_name
) = 1
