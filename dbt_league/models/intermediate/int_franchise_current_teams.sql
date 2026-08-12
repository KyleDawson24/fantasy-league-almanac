-- int_franchise_current_teams.sql
-- The platform teams a league is fielding RIGHT NOW (MLB-229): one row per
-- (league_key, franchise_id) taken from each league's latest team capture,
-- with the season that capture describes.
--
-- WHAT THIS IS FOR. Activity belongs to the AXES of a rendered matrix, never
-- to the facts underneath it: a team that changed platform ids keeps every
-- result its former ids earned, and a franchise that folded keeps the games it
-- played. So this model answers exactly one question -- which ids are live --
-- and hands it to the axes model to resolve into identities. Nothing
-- aggregating a fact reads it.
--
-- THE CAPTURE, NOT THE HISTORY. "Currently active" is a statement about the
-- platform's present, so it comes from what the platform is serving now rather
-- than from who appears in the oldest box score. Both arms are the same shape
-- -- the team list of the league's latest captured season -- which is why a
-- new platform joins by adding its own team capture and nothing else moves.
--
-- WHY THE STANDINGS FEEDS AND NOT THE ROSTER SNAPSHOTS. A roster capture
-- answers "who is on this team", and reaching it means depending on the
-- player chain for a question about team existence -- heavier, and empty for a
-- league whose players have not been attributed yet. The standings feeds are
-- the platform's own team list, which is the thing being asked for.
--
-- LATEST SEASON PER LEAGUE, and only that season. A franchise that played last
-- year and not this one is correctly absent: that is what "no longer active"
-- means. Every league resolves its own latest season, so two leagues at
-- different points in their calendars never contaminate each other.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, franchise_id).
-- ==========================================================================
{{ config(materialized='view') }}

with delivered as (
    select
        league_key,
        season_year,
        cast(team_id as varchar) as franchise_id
    from {{ ref('stg_team_standings') }}
    where team_id is not null
),

period_standings as (
    select
        league_key,
        season_year,
        cast(team_id as varchar) as franchise_id
    from {{ ref('stg_cbs__standings') }}
    where team_id is not null
),

captured as (
    select * from delivered
    union all
    select * from period_standings
),

horizon as (
    select
        league_key,
        max(season_year) as current_season
    from captured
    group by league_key
)

select distinct
    c.league_key,
    c.franchise_id,
    c.season_year as current_season
from captured c
join horizon h
    on c.league_key = h.league_key
    and c.season_year = h.current_season
