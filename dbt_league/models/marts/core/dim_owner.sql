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
        last_name,
        cast(null as varchar) as seen_name
    from {{ ref('stg_team_owners') }}
    where owner_id is not null
    qualify row_number() over (
        partition by league_key, owner_id
        order by season_year desc
    ) = 1

    union all

    -- CBS owners (MLB-64): the spine is now every owner ever seen, per season
    -- (stg_cbs__team_owners grew to full history). Current owners still get
    -- their names from the owner_nicknames seed (the coalesces below); the
    -- HISTORICAL owners the seed doesn't cover carry seen_name -- the latest
    -- roster-page display of that person -- as the display fallback, and its
    -- first token as first_name so co-owner comma-joins still work. Distinct
    -- owner_ids by construction ('cbs-' slugs vs ESPN member GUIDs).
    select
        league_key,
        owner_id,
        split_part(max_by(owner_name, season_year), ' ', 1) as first_name,
        cast(null as varchar)                                as last_name,
        max_by(owner_name, season_year)                      as seen_name
    from {{ ref('stg_cbs__team_owners') }}
    group by league_key, owner_id
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
    --
    -- PORTABILITY (MLB-134, hand off to MLB-10): INITCAP is a dialect surface
    -- and its output is RENDERED data -- owner names on every team page and
    -- the Home grid. Two exposures on a port: whether the target engine has
    -- INITCAP at all, and whether it calls the same characters word
    -- boundaries (apostrophes and hyphens are the classic divergence --
    -- O'Neal vs O'neal, McDonald vs Mcdonald). This is the title_case()
    -- macro candidate; the per-adapter dispatch belongs with MLB-10 phase 2,
    -- NOT here. Named now so that if a golden moves during the port it has a
    -- documented cause instead of reading as a data bug.
    coalesce(
        n.preferred_name,
        nullif(
            initcap(trim(coalesce(o.first_name, n.first_name)))
                || ' ' || initcap(trim(coalesce(o.last_name, n.last_name))),
            ''),
        -- CBS historical owners have no nickname row and no last_name, so the
        -- concat above is NULL -- fall back to their roster-page display.
        o.seen_name
    ) as owner_display
from owners o
left join nicknames n
    on o.owner_id = n.owner_id
