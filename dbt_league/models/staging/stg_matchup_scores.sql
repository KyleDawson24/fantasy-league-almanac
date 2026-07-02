-- stg_matchup_scores.sql
-- One row per (season_year, matchup_period, team_id): the team's FINAL
-- ESPN platform score for the matchup, read from the wrapper's
-- home_score / away_score fields in the raw box-score JSON. ESPN's
-- per-team score is cumulative through the queried scoring_period, so
-- the last scoring period of each matchup_period carries the final value.
--
-- This is a matchup-grain sibling of stg_box_scores: the same raw table
-- carries two grains at once (per-player rows, per-matchup wrapper
-- scores), and each grain gets its own staging reshape. The wrapper
-- score is ESPN's authoritative team total -- slot-aware AND inclusive
-- of commissioner manual adjustments -- and is deliberately NOT
-- derivable from the player rows; the divergence is the point (see
-- platform_calculated_delta on fct_team_weekly_active_performance).
--
-- COALESCE handles both legacy raw shape (bare matchups array) and the
-- Phase-4 shape (dict with a matchups key).

{{ config(materialized='view') }}

with last_sp_per_matchup as (
    select
        season_year,
        matchup_period,
        max(scoring_period) as last_sp
    from {{ source('raw', 'box_scores') }}
    group by 1, 2
),

last_sp_matchups as (
    select
        b.season_year,
        b.matchup_period,
        coalesce(b.raw_json:matchups, b.raw_json) as matchups_json
    from {{ source('raw', 'box_scores') }} b
    inner join last_sp_per_matchup ls
        on b.season_year = ls.season_year
        and b.matchup_period = ls.matchup_period
        and b.scoring_period = ls.last_sp
)

select
    season_year, matchup_period,
    m.value:home_team_id::integer as team_id,
    m.value:home_score::float     as platform_points
from last_sp_matchups,
    lateral flatten(input => matchups_json) m
union all
select
    season_year, matchup_period,
    m.value:away_team_id::integer,
    m.value:away_score::float
from last_sp_matchups,
    lateral flatten(input => matchups_json) m
