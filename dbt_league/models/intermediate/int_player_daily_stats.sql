-- int_player_daily_stats.sql
-- Slot-agnostic player-daily stat detail with stat_classification join applied.
-- Filters to is_counting=true (rate stats dropped here -- they can't be summed,
-- and rates get recomputed via macros at the fact layer where slot filter has
-- been applied).
--
-- Long format. lineup_slot preserved so downstream models can produce
-- active variants (fct_weekly_player_stats) and inactive variants
-- (future mart_wasted_points) from the same intermediate.
--
-- Grain: one row per (season, matchup, scoring_period, team, player, slot, stat_name).

with daily as (
    select * from {{ ref('stg_player_stat_breakdowns') }}
),

classification as (
    select stat_name, stat_category, is_counting
    from {{ ref('stat_classification') }}
),

filtered as (
    select
        d.season_year,
        d.matchup_period,
        d.scoring_period,
        d.team_id,
        d.team_name,
        d.owner_name,
        d.player_id,
        d.player_name,
        d.lineup_slot,
        d.stat_name,
        c.stat_category,
        d.stat_value
    from daily d
    inner join classification c
        on d.stat_name = c.stat_name
    where c.is_counting = true
)

select * from filtered
