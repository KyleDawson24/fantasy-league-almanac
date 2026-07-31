-- int_cbs__team_owner_season.sql
-- Per-season CBS team ownership (MLB-64), ALL-TIME. Turns the curated
-- team_owner_by_year NAMES (the historian's Owner 1/2/3 where entered, else the
-- roster-page owner) into canonical owner IDS:
--
--   name -> owner_id : a CURRENT owner is matched by display name so it keeps
--       its seeded slug ('Julian D. Sherman' -> cbs-julian-sherman, NOT a fresh
--       cbs-jason-d-scott); everyone else is slugged from the name (matching
--       build_continuity_sheet._slug).
--   owner_id -> canonical : the owner_alias seed collapses the drift the
--       historian merged (Des/Desmond Foster, Rich/Rexford Landon, Sandy/Sanford).
--
-- CBS-specific because CBS serves owner NAMES, not ids; it feeds the shared,
-- platform-general owner chain (stg_cbs__team_owners -> dim_owner).
--
-- This is what grows the owner chain from current-era-only to full history:
-- stg_cbs__team_owners reads it, and dim_owner / dim_team_owner then serve
-- every season with no shape change.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, franchise_id, owner_id).
-- ==========================================================================
{{ config(materialized='view') }}

with owner_year as (
    select
        league_key,
        cast(season_year as varchar)   as season_year,
        cast(franchise_id as varchar)  as franchise_id,
        trim(owner_name)               as owner_name
    from {{ ref('team_owner_by_year') }}
    where owner_name is not null and trim(owner_name) <> ''
),

-- current owners: display name -> the seeded slug
current_display as (
    select
        o.owner_id,
        lower(coalesce(n.preferred_name,
                       nullif(trim(coalesce(n.first_name, '') || ' '
                              || coalesce(n.last_name, '')), ''))) as disp_key
    from (select distinct league_key, owner_id
          from {{ ref('cbs_team_owners') }}) o
    left join {{ ref('owner_nicknames') }} n on o.owner_id = n.owner_id
    where o.league_key = 'cbs-bsb'
),

alias as (
    select owner_id, canonical_owner_id from {{ ref('owner_alias') }}
),

resolved as (
    select
        oy.league_key,
        oy.season_year,
        oy.franchise_id,
        oy.owner_name,
        coalesce(
            cd.owner_id,
            'cbs-' || trim({{ regexp_replace('lower(oy.owner_name)',
                                             '[^a-z0-9]+', '-') }}, '-')
        ) as raw_owner_id
    from owner_year oy
    left join current_display cd
        on cd.disp_key is not null and lower(oy.owner_name) = cd.disp_key
)

select distinct
    r.league_key,
    r.season_year,
    r.franchise_id,
    coalesce(a.canonical_owner_id, r.raw_owner_id) as owner_id,
    r.owner_name
from resolved r
left join alias a on r.raw_owner_id = a.owner_id
