-- fct_weekly_player_stats.sql
-- Incremental player-weekly stat fact. Foundation fact for team stat rollups
-- and future player-level records/analysis.
--
-- Grain: one row per (season_year, matchup_period, team_id, player_id, stat_name).
--
-- Incremental strategy: process the latest-loaded matchup period and anything
-- newer. The latest period is always re-processed because in-progress weeks
-- get re-extracted as new scoring_periods arrive. The unique_key handles the
-- merge -- existing rows for that period are overwritten.
--
-- For historical corrections (e.g. fixing a past matchup_period), use:
--   dbt run --full-refresh --select fct_weekly_player_stats+

{{ config(
    materialized='incremental',
    unique_key=['season_year', 'matchup_period', 'team_id', 'player_id', 'stat_name'],
    on_schema_change='fail'
) }}

select
    season_year,
    matchup_period,
    team_id,
    team_name,
    owner_name,
    player_id,
    player_name,
    stat_name,
    stat_category,
    stat_value
from {{ ref('int_player_weekly_stats') }}

{% if is_incremental() %}
-- Composite scalar: season_year * 100 + matchup_period preserves (season, matchup)
-- ordering and makes the comparison a simple >= against {{ this }}'s max.
-- Matchup periods never exceed ~30 per season, so the * 100 offset is safe.
where (season_year * 100 + matchup_period) >= (
    select coalesce(max(season_year * 100 + matchup_period), 0) from {{ this }}
)
{% endif %}
