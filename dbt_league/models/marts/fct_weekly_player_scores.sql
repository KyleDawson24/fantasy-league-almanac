-- fct_weekly_player_contributions.sql
-- Weekly player-level totals, one row per player per team per matchup period.
-- Used for top contributor callouts in the weekly summary.

with daily as (
    select * from {{ ref('int_player_daily_scores') }}
),

weekly as (
    select
        season_year,
        matchup_period,
        team_name,
        team_id,
        player_name,
        player_id,
        position,
        player_type,
        sum(points)                     as total_points,
        count(distinct scoring_period)  as days_active
    from daily
    group by 1, 2, 3, 4, 5, 6, 7, 8
)

select * from weekly

--adding a new mart in phase 4 that will allow for positional-level queries, likely named fct_weekly_positional_scores.
----a mart to be named later if you will