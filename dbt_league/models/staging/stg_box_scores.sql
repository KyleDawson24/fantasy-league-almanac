-- stg_box_scores.sql
-- Flatten raw JSON into one row per player per team per scoring period.
-- This is the foundational grain for all downstream models.

with raw as (
    select
        season_year,
        scoring_period,
        matchup_period,
        raw_json
    from {{ source('raw', 'box_scores') }}
),

matchups as (
    select
        season_year,
        scoring_period,
        matchup_period,
        m.value as matchup
    from raw,
        lateral flatten(input => raw_json) m
),

home_players as (
    select
        season_year,
        scoring_period,
        matchup_period,
        matchup:home_owner::string      as owner_name,
        matchup:home_team::string       as team_name,
        matchup:home_team_id::integer   as team_id,
        'home'                          as home_away,
        p.value:name::string            as player_name,
        p.value:playerId::integer       as player_id,
        p.value:position::string        as position,
        p.value:lineupSlot::string      as lineup_slot,
        p.value:proTeam::string         as pro_team,
        p.value:points::float           as points,
        p.value:breakdown               as breakdown
    from matchups,
        lateral flatten(input => matchup:home_lineup) p
),

away_players as (
    select
        season_year,
        scoring_period,
        matchup_period,
        matchup:away_owner::string      as owner_name,
        matchup:away_team::string       as team_name,
        matchup:away_team_id::integer   as team_id,
        'away'                          as home_away,
        p.value:name::string            as player_name,
        p.value:playerId::integer       as player_id,
        p.value:position::string        as position,
        p.value:lineupSlot::string      as lineup_slot,
        p.value:proTeam::string         as pro_team,
        p.value:points::float           as points,
        p.value:breakdown               as breakdown
    from matchups,
        lateral flatten(input => matchup:away_lineup) p
)

select * from home_players
union all
select * from away_players