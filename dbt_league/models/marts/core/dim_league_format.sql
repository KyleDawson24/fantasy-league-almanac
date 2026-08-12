-- dim_league_format.sql
-- What KIND of league is this? One row per league_key, decided by DATA
-- PRESENCE (MLB-229).
--
-- WHY IT EXISTS. The Rivalry Matrix means different things in different
-- formats: in a head-to-head league a rivalry is a record of matchups, and in
-- a points league there are no matchups at all -- the rivalry is which team
-- outscored which, season by season. Rendering both grids everywhere would
-- show every H2H league a season-points table nobody asked for and every
-- points league a matchup grid that is structurally empty. So the renderer has
-- to know the format, and this is where it asks.
--
-- NEVER BY PLATFORM NAME. The house rule (int_franchise_registry,
-- fct_team_season_performance, output/cbs_almanac_sheets.is_points_league):
-- format follows what the data DOES, not what the platform is called. A CBS
-- H2H league and an ESPN points league both exist and both would be misfiled
-- by a platform check, and a new platform joins by delivering data rather than
-- by being added to a list.
--
-- THE SIGNAL is the one the renderer already used: delivered period standings
-- exist only where the league has no matchups to be scored on
-- (mart_period_standings, the F7 seam). Presence of matchup pairings is
-- carried alongside as the positive evidence for the other side, so a league
-- with neither reads `unknown` rather than being quietly filed as H2H --
-- an empty install should say "I do not know yet", not pick.
--
-- ==========================================================================
-- GRAIN: one row per league_key.
-- ==========================================================================
{{ config(materialized='view') }}

with period_standings as (
    select distinct league_key, true as has_period_standings
    from {{ ref('mart_period_standings') }}
),

matchups as (
    select distinct league_key, true as has_matchups
    from {{ ref('stg_matchup_pairs') }}
    where home_team_id is not null
      and away_team_id is not null
),

leagues as (
    select league_key from period_standings
    union
    select league_key from matchups
)

select
    l.league_key,
    coalesce(ps.has_period_standings, false) as has_period_standings,
    coalesce(m.has_matchups, false)          as has_matchups,
    case
        -- Delivered period standings settle it: that feed exists precisely
        -- because there are no matchups to read a result from.
        when coalesce(ps.has_period_standings, false) then 'points'
        when coalesce(m.has_matchups, false)          then 'h2h'
        else 'unknown'
    end as league_format
from leagues l
left join period_standings ps
    on l.league_key = ps.league_key
left join matchups m
    on l.league_key = m.league_key
