-- int_franchise_registry.sql
-- Platform-general OBSERVED franchise registry (MLB-64): one row per
-- (league_key, franchise_id) with the franchise's observed display name +
-- abbrev, unioned across platforms. This is the "what your league's history
-- makes available to us" layer -- dim_franchise resolves the human's continuity
-- overrides (the franchise_lineage seed) onto THIS. Keeping the observed input
-- behind a general seam is what makes the whole continuity pipeline
-- platform-agnostic: every league flows through the same shape, and no league
-- needs an override just to appear.
--
-- Two observed sources, in precedence order. A league is served by exactly one:
--
--   1. CURATED -- a franchises seed, for leagues whose names are historical and
--      cannot be re-derived. CBS's 26 seasons live here because the platform
--      serves no franchise API; the names were reconstructed once from the
--      parsed UI standings history and are frozen data now.
--
--   2. DERIVED -- per-season team identity observed in the box scores, taking
--      the franchise's LATEST season as its display. Leagues whose platform
--      reports team names live belong here: a mid-season rename propagates on
--      the next extract with no seed maintenance, which a curated seed would
--      silently defeat.
--
-- Precedence is by league, not by row: the derived branch excludes any league
-- the curated seed already covers, so the two can never fight over a franchise
-- and the grain stays one row per (league_key, franchise_id).
--
-- Neither branch names a platform. A new league joins whichever branch matches
-- how its data actually behaves -- historical-and-frozen, or live-and-observed.
--
-- Both branches also carry last_observed_season, the recency signal
-- dim_franchise anchors DISPLAY on (MLB-113). The derived branch has one
-- natively; a curated league's seed is flat, so recency comes from the
-- season-grain union below rather than from a column the human must maintain.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, franchise_id).
-- ==========================================================================
{{ config(materialized='view') }}

with curated as (
    select
        league_key,
        cast(franchise_id as varchar) as franchise_id,
        franchise_name                as observed_name,
        abbrev                        as observed_abbrev
    from {{ ref('cbs_franchises') }}
),

-- Distinct team identity per season, carrying the last period each name was
-- seen under. stg_box_scores is player-grain, so the same (season, team)
-- repeats once per rostered player per matchup.
--
-- A team can rename MID-season, which puts two names in one season_year and
-- makes season alone an insufficient sort: the two rows tie, and row_number()
-- then picks between them arbitrarily -- a franchise's display name could flip
-- between rebuilds. The period each name was last seen under is the within-
-- season recency signal that breaks it.
observed_seasons as (
    select
        league_key,
        season_year,
        cast(team_id as varchar) as franchise_id,
        team_name,
        team_abbrev,
        max(matchup_period) as last_matchup_period
    from {{ ref('stg_box_scores') }}
    where team_id is not null
      and league_key not in (select league_key from curated)
    group by
        league_key,
        season_year,
        cast(team_id as varchar),
        team_name,
        team_abbrev
),

-- The franchise's latest observed name wins its display, so a rename shows
-- up everywhere the moment it lands in RAW -- mid-season ones included.
derived as (
    select
        league_key,
        franchise_id,
        team_name   as observed_name,
        team_abbrev as observed_abbrev
    from (
        select
            observed_seasons.*,
            row_number() over (
                partition by league_key, franchise_id
                order by season_year desc,
                         last_matchup_period desc nulls last
            ) as recency_rank
        from observed_seasons
    )
    where recency_rank = 1
),

-- When was each franchise last seen playing? DISPLAY recency only -- identity
-- anchoring stays the lineage seed's job (dim_franchise). Platform-general by
-- union: every league contributes whatever season-grain source it has, which is
-- what gives a CURATED league a recency signal its flat seed cannot carry. Add
-- a league's season source here when it joins. Overlap between sources is safe
-- -- this is a max(), not a count().
franchise_recency as (
    select
        league_key,
        franchise_id,
        max(season_year) as last_observed_season
    from (
        select
            league_key,
            cast(team_id as varchar) as franchise_id,
            season_year
        from {{ ref('stg_box_scores') }}
        where team_id is not null

        union all

        select
            league_key,
            cast(franchise_id as varchar) as franchise_id,
            season_year
        from {{ ref('stg_cbs__ui_standings') }}
    )
    group by league_key, franchise_id
),

observed as (
    select league_key, franchise_id, observed_name, observed_abbrev
    from curated

    union all

    select league_key, franchise_id, observed_name, observed_abbrev
    from derived
)

select
    o.league_key,
    o.franchise_id,
    o.observed_name,
    o.observed_abbrev,
    fr.last_observed_season
from observed o
left join franchise_recency fr
    on o.league_key = fr.league_key
    and o.franchise_id = fr.franchise_id
