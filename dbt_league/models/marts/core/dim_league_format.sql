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
-- fct_team_season_performance, and the renderer's own former format dispatch
-- -- deleted in MLB-263 once this model had replaced it):
-- format follows what the data DOES, not what the platform is called. A CBS
-- H2H league and an ESPN points league both exist and both would be misfiled
-- by a platform check. A platform's explicit FORMAT field is data, however,
-- and the first stranger rehearsal supplied the missing measured control:
-- ESPN currentLeagueType=5 is a season-long points league, while the H2H
-- points seasons on file read current=0 / created=2. That signal may settle
-- the format without pretending a platform name settles it.
--
-- SETTINGS FIRST, PRESENCE AS FALLBACK (MLB-263, ledger S-34). The contract
-- calls format a SETTING, not a shape, so where a platform states it we read
-- the statement: ESPN's currentLeagueType = 5 (above), and CBS's rules feed
-- carries a scoring_system label (stg_cbs__league_settings). Only when
-- neither speaks do we infer from the data, which is what this model did
-- exclusively before.
--
-- Both verdicts are published side by side so their agreement is TESTABLE
-- rather than assumed: assert_league_format_settings_match_presence fails the
-- build if a league's stated format and its data shape ever disagree. They
-- agree on both pioneer leagues today, which is what makes reading settings
-- first a no-op on current output rather than a re-verdict.
--
-- THE PRESENCE SIGNAL is the one the renderer already used: delivered period
-- standings exist only where the league has no matchups to be scored on
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

season_points_schedule as (
    select distinct league_key, true as has_season_points_schedule
    from {{ ref('stg_matchup_schedule') }}
    where current_league_type = 5
),

-- The platforms' own format statements, unioned into one shape.
settings_format as (
    -- CBS: the scoring-system label from the league's rules feed. Staging
    -- returns NULL for a label it does not recognise, and those are filtered
    -- out here so an unrecognised label falls through to presence rather
    -- than voting NULL over it.
    select league_key, settings_league_format
    from {{ ref('stg_cbs__league_settings') }}
    where settings_league_format is not null

    union

    -- ESPN: currentLeagueType is the platform's own format field, and 5 is
    -- the season-long points container. Other values are deliberately NOT
    -- translated: the H2H seasons on file read current = 0 / created = 2,
    -- and reading 'not 5' as 'h2h' would turn an unset field into a verdict.
    -- They fall through to presence, which has matchup evidence to work from.
    select distinct league_key, 'points' as settings_league_format
    from {{ ref('stg_matchup_schedule') }}
    where current_league_type = 5
),

leagues as (
    select league_key from period_standings
    union
    select league_key from matchups
    union
    select league_key from season_points_schedule
    union
    select league_key from settings_format
)

select
    l.league_key,
    coalesce(ps.has_period_standings, false) as has_period_standings,
    coalesce(m.has_matchups, false)          as has_matchups,
    coalesce(sp.has_season_points_schedule, false)
                                                as has_season_points_schedule,
    sf.settings_league_format,

    -- The data-shape inference, kept as its own published column so the
    -- agreement test has something to compare the statement against.
    case
        -- Delivered period standings settle it: that feed exists precisely
        -- because there are no matchups to read a result from.
        when coalesce(ps.has_period_standings, false) then 'points'
        when coalesce(sp.has_season_points_schedule, false) then 'points'
        when coalesce(m.has_matchups, false)          then 'h2h'
        else 'unknown'
    end as presence_league_format,

    -- THE VERDICT: the platform's own statement where it makes one, the
    -- inference otherwise. 'unknown' still means nobody could answer, which
    -- an empty install must be able to say.
    coalesce(
        sf.settings_league_format,
        case
            when coalesce(ps.has_period_standings, false) then 'points'
            when coalesce(sp.has_season_points_schedule, false) then 'points'
            when coalesce(m.has_matchups, false)          then 'h2h'
            else 'unknown'
        end
    ) as league_format
from leagues l
left join period_standings ps
    on l.league_key = ps.league_key
left join matchups m
    on l.league_key = m.league_key
left join season_points_schedule sp
    on l.league_key = sp.league_key
left join settings_format sf
    on l.league_key = sf.league_key
