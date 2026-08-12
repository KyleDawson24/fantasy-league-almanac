-- int_franchise_season_points.sql
-- Each franchise's AUTHORITATIVE season points total, as the platform itself
-- reported it, carrying the league-season's completion verdict (MLB-229).
--
-- WHY NOT fct_team_season_performance. That fact's totals are OURS -- summed
-- from the player chain under a chosen lens, and its platform_points overlay
-- is NULL for a points league by design. A season-points rivalry asks who the
-- PLATFORM said outscored whom, so it has to read the platform's own delivered
-- standings. Kyle's ruling, and it also makes the comparison available for
-- seasons the player chain never covered.
--
-- Two source arms, joined by DATA PRESENCE and not by a platform check --
-- the same rule int_franchise_registry uses. A league contributes through
-- whichever arm its history actually arrives on.
--
--   1. DELIVERED SEASON STANDINGS (stg_team_standings). The platform serves a
--      per-team season total.
--   2. PARSED FINAL STANDINGS (stg_cbs__ui_standings). Reconstructed once from
--      the UI history and frozen.
--
-- stg_cbs__standings is deliberately NOT a third arm. It carries the LIVE
-- season only (the UI history stops at the last completed year), so every row
-- it could contribute would be filtered out again by the completeness gate.
-- Adding it would be a branch that can only ever produce zero rows. It becomes
-- the right seam the day CBS gains a season-completion signal.
--
-- COMPLETENESS IS NOT DECIDED HERE. int_league_season_closure owns it, for
-- both this model and the matchup ledger -- two answers to one question drift,
-- and they drifted in the same direction the first time. In particular a
-- delivered final RANK now closes a season on its own, so the latest loaded
-- season is no longer withheld just because the opt-in schedule capture has
-- not run: ESPN publishing final ranks is the platform saying the season is
-- over, and supersession is a fallback rather than the only route.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, franchise_id).
-- ==========================================================================
{{ config(materialized='view') }}

with closure as (
    select
        league_key,
        season_year,
        is_season_complete,
        completion_evidence
    from {{ ref('int_league_season_closure') }}
),

delivered as (
    select
        league_key,
        season_year,
        cast(team_id as varchar) as franchise_id,
        platform_points          as season_points,
        'delivered_standings'    as points_source
    from {{ ref('stg_team_standings') }}
    where team_id is not null
      and platform_points is not null
),

parsed_final as (
    select
        league_key,
        season_year,
        cast(franchise_id as varchar)  as franchise_id,
        total_points                   as season_points,
        'parsed_final_standings'       as points_source
    from {{ ref('stg_cbs__ui_standings') }}
    where franchise_id is not null
      and total_points is not null
),

unioned as (
    select * from delivered
    union all
    select * from parsed_final
),

-- One row per franchise-season. Where both arms reach one league season the
-- FINAL standings win -- a parsed final total is settled where a delivered one
-- may still be moving. Ordering by source makes that a rule rather than
-- whichever row the engine scanned first.
deduped as (
    select *
    from unioned
    qualify row_number() over (
        partition by league_key, season_year, franchise_id
        order by case when points_source = 'parsed_final_standings' then 0
                      else 1 end,
                 points_source
    ) = 1
)

select
    d.league_key,
    d.season_year,
    d.franchise_id,
    d.season_points,
    d.points_source,
    -- INNER join to closure: a franchise-season whose league-season the
    -- closure model cannot speak about at all has no verdict to inherit, and
    -- inventing one here is exactly the fail-open this ticket removed.
    c.is_season_complete,
    c.completion_evidence
from deduped d
join closure c
    on d.league_key = c.league_key
    and d.season_year = c.season_year
