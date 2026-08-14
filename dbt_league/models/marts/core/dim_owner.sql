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
        nullif(trim(display_name), '') as seen_name
    from {{ ref('stg_team_owners') }}
    where owner_id is not null
    qualify row_number() over (
        partition by league_key, owner_id
        -- MLB-134 -- stg_team_owners' grain includes team_id, so an
        -- owner holding two teams in their latest season would be two
        -- tied rows with independently-sourced name fields, and this
        -- lands in RENDERED data (owner_display -> every team page and
        -- the Home grid). No such owner exists today; pinned so the
        -- first one doesn't silently pick a name.
        order by season_year desc nulls last, team_id
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
    -- lowercase ("lance barrett") or with stray whitespace ("Jared ");
    -- INITCAP + TRIM normalizes to "Lance Barrett" / "Jared Ellery", same
    -- as format_owners()'s .title(). preferred_name (when set) wins
    -- verbatim, so nicknames + intentional casing (McAvery) come through.
    --
    -- PORTABILITY (MLB-134, resolved by MLB-10 phase 2): INITCAP is a dialect
    -- surface and its output is RENDERED data -- owner names on every team
    -- page and the Home grid. Both exposures the pre-port review named are
    -- now closed. DuckDB has no INITCAP at all, so title_case() dispatches to
    -- a character-wise shim rather than a rename; and the word-boundary
    -- question (apostrophes and hyphens -- O'Neal vs O'neal, McDonald vs
    -- Mcdonald) was settled by A/B rather than by argument: the shim was run
    -- against Snowflake's native INITCAP over all 98 non-empty owner name
    -- parts in the seed, 0 mismatches. No golden can move here.
    coalesce(
        n.preferred_name,
        nullif(
            {{ title_case('trim(coalesce(o.first_name, n.first_name))') }}
                || ' ' || {{ title_case('trim(coalesce(o.last_name, n.last_name))') }},
            ''),
        -- CBS historical owners have no nickname row and no last_name, so the
        -- concat above is NULL -- fall back to their roster-page display.
        -- ESPN can likewise expose only displayName for a privacy-limited
        -- member. If the platform withholds every name field, retain a clear
        -- non-identity label; the team name still distinguishes the row.
        o.seen_name,
        -- WITHHELD, NOT MISSING (MLB-243). A public ESPN league serves the
        -- stable owner GUID and NULLs every first/last/display name -- a
        -- supported privacy shape, not a gap in our extract. "Unknown owner"
        -- described it as our failure to identify somebody and invited a
        -- reader to think the data is broken; this says what actually
        -- happened. The identity itself is intact and keyed on the GUID --
        -- only the label is unavailable, and no name is ever invented to
        -- fill it.
        '{{ var("owner_unavailable_label") }}'
    ) as owner_display
from owners o
left join nicknames n
    on o.owner_id = n.owner_id
