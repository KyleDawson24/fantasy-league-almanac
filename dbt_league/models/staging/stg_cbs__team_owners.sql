-- stg_cbs__team_owners.sql
-- The CBS team -> owner bridge, mirroring stg_team_owners' grain so the
-- shared owner dimension chain (dim_owner -> dim_team_owner) serves both
-- platforms. CBS serves NO owner identity anywhere (the roster/standings
-- pages carry team names only; owner strings appear solely as free text
-- on the year-end report), so identity is SEED-CURATED: cbs_team_owners
-- maps franchise -> minted owner_id slugs ('cbs-abel-holbrook'), and the
-- shared owner_nicknames seed carries their names -- the same seed that
-- overrides ESPN display names, per Kyle's one-preferred-names-source
-- call (2026-07-13).
--
-- CURRENT-ERA ONLY, deliberately: rows are stamped with the latest
-- captured season because that's the era the seed attests. Historical
-- owner custody (who ran franchise 13 in 2012) is exactly MLB-64's
-- chain-of-custody work; when that lands, this staging grows per-season
-- rows and the downstream dims serve owner history with no shape change.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, team_id, owner_id).
--   Co-owned teams yield multiple rows per (league, season, team) --
--   identical to stg_team_owners' contract.
-- ==========================================================================

{{ config(materialized='view') }}

with current_season as (
    select league_key, max(season_year) as season_year
    from {{ ref('stg_cbs__rosters') }}
    group by 1
)

select
    s.league_key,
    cs.season_year,
    s.franchise_id      as team_id,
    s.owner_id
from {{ ref('cbs_team_owners') }} s
inner join current_season cs
    on s.league_key = cs.league_key
