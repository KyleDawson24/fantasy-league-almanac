-- dim_owner.sql
-- v1.2: owner dimension. The spine is every owner_id ever seen in
-- stg_team_owners (so owners who have since left the league still resolve
-- to a display name); the owner_nicknames seed contributes an optional
-- preferred_name override.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, owner_id).
--   Owner ids are platform-issued (ESPN member GUIDs, CBS owner ids) and
--   only meaningful within their league; the same human in two leagues is
--   two rows until the cross-league identity work (MLB-8) stitches them.
-- ==========================================================================
--
-- owner_display = preferred_name when the seed sets one, else the
-- "First Last" name from the latest settings snapshot. Consumers reach
-- this via (season, team_id) -> stg_team_owners -> owner_id -> here
-- (collapsed for co-owners in dim_team_owner).
--
-- Why spine off stg_team_owners and not the seed: the seed only lists
-- *current* owners. A 2025 team owned by someone who has since left the
-- league has an owner_id absent from the seed; sourcing the spine from
-- the settings bridge keeps that owner's name resolvable (no preferred
-- override, just their real name).
--
-- Materialization: view. Tiny; changes only when settings are
-- re-extracted or the seed is edited.

{{ config(materialized='view') }}

with owners as (
    select
        league_key,
        owner_id,
        first_name,
        last_name
    from {{ ref('stg_team_owners') }}
    where owner_id is not null
    qualify row_number() over (
        partition by league_key, owner_id
        order by season_year desc
    ) = 1

    union all

    -- CBS owners (MLB-72 follow-on): the platform serves no owner
    -- identity, so the spine rows come from the curated bridge and the
    -- names ride entirely on the owner_nicknames seed (the coalesces
    -- below). Distinct owner_ids by construction ('cbs-' slugs vs ESPN
    -- member GUIDs), so the union can't collide.
    select distinct
        league_key,
        owner_id,
        cast(null as varchar) as first_name,
        cast(null as varchar) as last_name
    from {{ ref('stg_cbs__team_owners') }}
),

nicknames as (
    select
        owner_id,
        nullif(trim(first_name), '')     as first_name,
        nullif(trim(last_name), '')      as last_name,
        nullif(trim(preferred_name), '') as preferred_name
    from {{ ref('owner_nicknames') }}
)

select
    o.league_key,
    o.owner_id,
    -- Seed names backfill platforms that serve none (CBS); a platform-served
    -- name always wins, so ESPN rows are untouched.
    coalesce(o.first_name, n.first_name) as first_name,
    coalesce(o.last_name, n.last_name)   as last_name,
    n.preferred_name,
    -- Fallback (no preferred_name set) matches the extract's title-cased
    -- owner_name so unset owners don't regress: ESPN stores some names
    -- lowercase ("luke baker") or with stray whitespace ("Jonathan ");
    -- INITCAP + TRIM normalizes to "Lance Barrett" / "Jonathan Evans", same
    -- as format_owners()'s .title(). preferred_name (when set) wins
    -- verbatim, so nicknames + intentional casing (McAvery) come through.
    coalesce(
        n.preferred_name,
        initcap(trim(coalesce(o.first_name, n.first_name)))
            || ' ' || initcap(trim(coalesce(o.last_name, n.last_name)))
    ) as owner_display
from owners o
left join nicknames n
    on o.owner_id = n.owner_id
