-- int_franchise_seasons.sql
-- Platform-general OBSERVED franchise seasons (MLB-115): one row per
-- (league_key, franchise_id, season_year) a franchise was actually seen
-- playing. The season-grain twin of int_franchise_registry, which answers
-- "who are this league's franchises" where this answers "when did each of
-- them play."
--
-- Every league contributes whatever season-grain source it has, which is what
-- lets a CURATED league order its eras without a maintained column in its flat
-- seed. Add a league's season source to the union when it joins. Overlap
-- between sources is harmless -- the group by is the dedupe.
--
-- Two consumers, and they want opposite ends of it: int_franchise_registry
-- rolls it up to each franchise's LATEST season (the display-recency signal),
-- while dim_franchise_season uses it as the spine of every franchise-season
-- that can be spoken about at all.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, franchise_id, season_year).
-- ==========================================================================
{{ config(materialized='view') }}

select
    league_key,
    franchise_id,
    season_year
from (
    select
        league_key,
        cast(team_id as varchar) as franchise_id,
        season_year
    from {{ ref('stg_box_scores') }}
    where team_id is not null

    union all

    -- CBS history (2001-2025), parsed from the UI standings pages.
    select
        league_key,
        cast(franchise_id as varchar) as franchise_id,
        season_year
    from {{ ref('stg_cbs__ui_standings') }}

    union all

    -- CBS's CURRENT season, which the UI history does not reach -- the API
    -- serves the live standings and the parsed pages stop at the last completed
    -- year. A league whose history and present arrive by different routes needs
    -- both, or its active franchises look like they never played this year.
    select
        league_key,
        cast(team_id as varchar) as franchise_id,
        season_year
    from {{ ref('stg_cbs__standings') }}
)
group by league_key, franchise_id, season_year
