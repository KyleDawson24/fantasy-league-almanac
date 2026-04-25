-- int_player_weekly_stats.sql
-- Player-level weekly stat totals with business filters applied.
-- Filters:
--   1. Active lineup slots only (bench and IL excluded).
--   2. Counting stats only (rates excluded via stat_classification.is_counting).
-- Joins to stat_classification to carry stat_category through.
-- Grain: one row per (season_year, matchup_period, team_id, player_id, stat_name).

with daily as (
    select * from {{ ref('stg_player_stat_breakdowns') }}
),

classification as (
    select
        stat_name,
        stat_category,
        is_counting
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
        d.stat_name,
        c.stat_category,
        d.stat_value
    from daily d
    inner join classification c
        on d.stat_name = c.stat_name
    where d.lineup_slot not in ('BE', 'IL')
      and c.is_counting = true
),

weekly as (
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
        sum(stat_value) as stat_value
    from filtered
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9
)

select * from weekly
