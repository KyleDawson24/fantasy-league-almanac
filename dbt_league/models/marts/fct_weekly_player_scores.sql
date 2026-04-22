-- fct_weekly_player_scores.sql
-- One row per player per team per matchup period.
-- Splits points into hitting_points, pitching_points, and total_points.
-- Two-way players (e.g. Ohtani) get a single row with both hitting
-- and pitching contributions reflected in separate columns.
-- Nickname resolution via player_nicknames seed.

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
        sum(case when player_type = 'hitting'  then total_points_typed else 0 end) as hitting_points,
        sum(case when player_type = 'pitching' then total_points_typed else 0 end) as pitching_points,
        sum(total_points_typed)    as total_points,
        count(distinct scoring_period) as days_active
    from daily
    group by 1, 2, 3, 4, 5, 6, 7
)

select
    w.*,
    coalesce(n.nickname, w.player_name) as display_name
from weekly w
left join {{ ref('player_nicknames') }} n
    on w.player_id = n.player_id

--adding a new mart in phase 4 that will allow for positional-level queries, likely named fct_weekly_positional_scores.
----a mart to be named later if you will