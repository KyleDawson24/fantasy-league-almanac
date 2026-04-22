-- int_player_daily_scores.sql
-- Player-level daily scores, active lineup slots only.
-- Classifies each player as hitting or pitching.
----can change mid game to account for the ohtani edge case
-- Thin model — primary purpose is filtering and classification
-- so downstream marts don't repeat that logic.

with players as (
    select * from {{ ref('stg_box_scores') }}
),

active as (
    select
        season_year,
        matchup_period,
        scoring_period,
        team_name,
        owner_name
        team_id,
        player_name,
        player_id,
        position,
        lineup_slot,
        pro_team,
        points as total_points_typed,
        case
            when lineup_slot in ('SP', 'RP') then 'pitching'
            else 'hitting'
        end as player_type
    from players
    where lineup_slot not in ('BE', 'IL')
)

select * from active