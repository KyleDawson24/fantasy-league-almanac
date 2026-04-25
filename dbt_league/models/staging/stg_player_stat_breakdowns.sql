-- stg_player_stat_breakdowns.sql
-- Flatten the breakdown VARIANT from stg_box_scores into one row per
-- (season_year, scoring_period, team_id, player_id, stat_name).
-- Mechanical reshape only -- business filters (active slots, counting stats)
-- are applied in intermediate.

with players as (
    select * from {{ ref('stg_box_scores') }}
),

flattened as (
    select
        season_year,
        scoring_period,
        matchup_period,
        team_id,
        team_name,
        owner_name,
        player_id,
        player_name,
        position,
        lineup_slot,
        b.key::string   as stat_name,
        b.value::float  as stat_value
    from players,
        lateral flatten(input => breakdown) b
    where breakdown is not null
)

select * from flattened
