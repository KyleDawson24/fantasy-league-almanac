-- int_franchise_season_points.sql
-- Each franchise's AUTHORITATIVE season points total, as the platform itself
-- reported it, with the evidence for whether the season is finished
-- (MLB-229).
--
-- WHY NOT fct_team_season_performance. That fact's totals are OURS -- summed
-- from the player chain under a chosen lens, and its platform_points overlay
-- is NULL for a points league by design. A season-points rivalry asks who the
-- platform said outscored whom, so it has to read the platform's own delivered
-- standings. Kyle's ruling, and it also makes the comparison available for
-- seasons the player chain never covered.
--
-- Two source arms, joined by DATA PRESENCE and not by a platform check --
-- the same rule int_franchise_registry uses. A league contributes through
-- whichever arm its history actually arrives on.
--
--   1. DELIVERED SEASON STANDINGS (stg_team_standings). The platform serves a
--      per-team season total; completion comes from the schedule capture.
--   2. PARSED FINAL STANDINGS (stg_cbs__ui_standings). Reconstructed once from
--      the UI history and frozen. These ARE final standings -- that is what
--      the source is -- so every row is complete by construction.
--
-- stg_cbs__standings is deliberately NOT a third arm. It carries the LIVE
-- season only (the UI history stops at the last completed year), and there is
-- no completion evidence for it, so every row it could contribute would be
-- filtered out again by the completeness gate. Adding it would be a branch
-- that can only ever produce zero rows. It becomes the right seam the day CBS
-- gains a season-completion signal.
--
-- ==========================================================================
-- WHAT "COMPLETE" MEANS, AND WHY IT IS NOT ONE RULE
-- ==========================================================================
-- A season-points verdict reads as settled history in a way a weekly result
-- does not, so a season in flight must not produce one. The proof available
-- depends on the arm:
--
--   * Where the schedule capture reached the season, season_is_complete is a
--     measured fact (latestScoringPeriod past finalScoringPeriod).
--   * Where it did not -- the capture is opt-in and no league has it for its
--     whole history -- a season STRICTLY EARLIER than the league's latest
--     standings season is complete: the platform has moved on from it. Only
--     the latest season is unproven, which is exactly the one that might be
--     in flight.
--   * Parsed final standings are complete by construction.
--
-- The fallback is a statement about the league's own timeline rather than a
-- guess about the calendar, which is what makes it safe for a historical
-- season and still refuses the live one.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, franchise_id).
-- ==========================================================================
{{ config(materialized='view') }}

with delivered as (
    select
        league_key,
        season_year,
        cast(team_id as varchar) as franchise_id,
        platform_points          as season_points
    from {{ ref('stg_team_standings') }}
    where team_id is not null
      and platform_points is not null
),

-- The league's own horizon on the delivered arm. Anything behind it has been
-- superseded; the season sitting ON it is the one that may still be running.
delivered_horizon as (
    select
        league_key,
        max(season_year) as latest_season
    from delivered
    group by league_key
),

schedule_completion as (
    select
        league_key,
        season_year,
        {{ boolor_agg('season_is_complete') }} as season_is_complete
    from {{ ref('stg_matchup_schedule') }}
    group by league_key, season_year
),

delivered_scored as (
    select
        d.league_key,
        d.season_year,
        d.franchise_id,
        d.season_points,
        'delivered_standings' as points_source,
        coalesce(
            sc.season_is_complete,
            d.season_year < h.latest_season
        ) as is_season_complete,
        case when sc.league_key is not null
             then 'schedule_capture' else 'superseded_season' end
            as completion_evidence
    from delivered d
    join delivered_horizon h
        on d.league_key = h.league_key
    left join schedule_completion sc
        on d.league_key = sc.league_key
        and d.season_year = sc.season_year
),

parsed_final as (
    select
        league_key,
        season_year,
        cast(franchise_id as varchar) as franchise_id,
        total_points                  as season_points,
        'parsed_final_standings'      as points_source,
        true                          as is_season_complete,
        'final_standings_source'      as completion_evidence
    from {{ ref('stg_cbs__ui_standings') }}
    where franchise_id is not null
      and total_points is not null
),

unioned as (
    select * from delivered_scored
    union all
    select * from parsed_final
)

-- One row per franchise-season. Where both arms somehow reach one league
-- season, the FINAL standings win -- a parsed final total is settled where a
-- delivered one may still be moving. Ordering by is_season_complete then
-- source name makes that a rule rather than whichever row the engine scanned
-- first.
select
    league_key,
    season_year,
    franchise_id,
    season_points,
    points_source,
    is_season_complete,
    completion_evidence
from unioned
qualify row_number() over (
    partition by league_key, season_year, franchise_id
    order by case when points_source = 'parsed_final_standings' then 0 else 1 end,
             points_source
) = 1
