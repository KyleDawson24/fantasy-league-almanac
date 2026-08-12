-- mart_franchise_rivalry_axes.sql
-- The rows and columns of the Rivalry Matrix (MLB-229): one row per ACTIVE
-- team identity in a league, with the label it displays and the order it
-- draws in.
--
-- WHY ACTIVITY IS A SEPARATE MODEL. Activity applies to the AXES of the
-- rendered matrix, never to the facts underneath it. An active team keeps
-- every result its former platform ids and former names ever earned, and a
-- folded team's games still happened -- they just have no row on a grid about
-- who is playing now. Folding an activity filter into mart_franchise_rivalry
-- would have deleted history to answer a display question; keeping it here
-- means the same fact rows serve a current-teams matrix, an all-time one, and
-- anything between, by changing which axes are asked for.
--
-- CURRENT IDS, THEN THE IDENTITY RULE, THEN DEDUPE. int_franchise_current_teams
-- gives the platform ids the league is fielding now. Each resolves through
-- dim_franchise_identity for that season, and the resulting identities are
-- deduplicated -- two live platform ids that share one configured canonical
-- name are ONE axis, not two, which is the same collapse the ledger applies to
-- their history. (When those two ids are genuinely different teams,
-- assert_configured_name_has_no_active_collision is what says so.)
--
-- ORDERING is by display name, so a matrix reads alphabetically and two
-- rebuilds draw the same grid. A consumer wanting standings order joins its
-- own; sort_order is here so the default is stable rather than
-- engine-dependent.
--
-- THE HOLDING PEN GETS NO AXIS. It is not a team, and it is not playing.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, identity_key).
-- ==========================================================================
{{ config(materialized='table') }}

with current_identities as (
    select distinct
        c.league_key,
        i.identity_key,
        i.identity_source,
        i.identity_name,
        i.identity_abbrev,
        c.current_season
    from {{ ref('int_franchise_current_teams') }} c
    join {{ ref('dim_franchise_identity') }} i
        on c.league_key = i.league_key
        and c.franchise_id = i.franchise_id
        and c.current_season = i.season_year
    where i.canonical_franchise_id <> '{{ var("holding_pen_franchise_id") }}'
),

-- How many live platform ids resolved onto this axis. Normally one; more than
-- one is the configured-name collapse doing its job, and a number the renderer
-- can surface if a league ever wants to see it.
counted as (
    select
        ci.league_key,
        ci.identity_key,
        ci.identity_source,
        ci.identity_name,
        ci.identity_abbrev,
        ci.current_season,
        (
            select count(*)
            from {{ ref('int_franchise_current_teams') }} c2
            join {{ ref('dim_franchise_identity') }} i2
                on c2.league_key = i2.league_key
                and c2.franchise_id = i2.franchise_id
                and c2.current_season = i2.season_year
            where c2.league_key = ci.league_key
              and i2.identity_key = ci.identity_key
        ) as active_platform_teams
    from current_identities ci
)

select
    c.league_key,
    c.identity_key,
    c.identity_source,
    c.identity_name,
    c.identity_abbrev,
    c.current_season,
    c.active_platform_teams,
    -- The league's format, carried so a renderer needs ONE query to draw the
    -- matrix: which axes, and which of the two ledgers means anything here.
    -- LEFT join and coalesced -- a league whose format cannot yet be read
    -- still gets axes, and says 'unknown' rather than being filed as H2H.
    coalesce(f.league_format, 'unknown') as league_format,
    row_number() over (
        partition by c.league_key
        -- identity_key breaks a name tie so the order is total. Two axes
        -- cannot share a name AND a key -- that is one axis -- so this only
        -- decides between two differently-keyed teams whose labels match,
        -- which is exactly the unconfigured-fallback case.
        order by c.identity_name, c.identity_key
    ) as sort_order
from counted c
left join {{ ref('dim_league_format') }} f
    on c.league_key = f.league_key
