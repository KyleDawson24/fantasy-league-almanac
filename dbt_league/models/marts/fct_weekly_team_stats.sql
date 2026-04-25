-- fct_weekly_team_stats.sql
-- Incremental team-weekly stat fact. Team totals are defined by construction as
-- SUM of player contributions, so this rolls up from fct_weekly_player_stats
-- rather than re-aggregating from intermediate. This guarantees the team total
-- always equals the sum of its player rows.
--
-- Grain: one row per (season_year, matchup_period, team_id, stat_name).
--
-- Incremental strategy mirrors fct_weekly_player_stats: latest-loaded period
-- plus anything newer. Use --full-refresh for historical corrections.

{{ config(
    materialized='incremental',
    unique_key=['season_year', 'matchup_period', 'team_id', 'stat_name'],
    on_schema_change='fail'
) }}

with players as (
    select * from {{ ref('fct_weekly_player_stats') }}

    {% if is_incremental() %}
    where (season_year * 100 + matchup_period) >= (
        select coalesce(max(season_year * 100 + matchup_period), 0) from {{ this }}
    )
    {% endif %}
)

select
    season_year,
    matchup_period,
    team_id,
    team_name,
    owner_name,
    stat_name,
    stat_category,
    sum(stat_value) as stat_value
from players
group by 1, 2, 3, 4, 5, 6, 7
