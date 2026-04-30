-- stg_box_scores.sql
-- Flatten raw JSON into one row per player per team per scoring period.
-- Foundational grain for both scoring and stats chains.
--
-- Phase 3.1: player_nicknames join moved here (from individual marts) so
-- display_name = COALESCE(nickname, player_name) propagates through every
-- downstream model.
--
-- Phase 3.3: games_played surfaces here. New extractions write it per-player
-- (0 = didn't appear, 1 = single game, 2 = both halves of a doubleheader).
-- Historical raw rows predating Phase 3.3 don't have the field; we COALESCE
-- to 1 when the player has a non-empty breakdown, 0 otherwise — matching the
-- semantics the wrapper produced before we knew about the DH overwrite bug.

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
        p.value:breakdown               as breakdown,
        coalesce(
            p.value:games_played::integer,
            iff(array_size(object_keys(p.value:breakdown)) > 0, 1, 0)
        )                               as games_played
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
        p.value:breakdown               as breakdown,
        coalesce(
            p.value:games_played::integer,
            iff(array_size(object_keys(p.value:breakdown)) > 0, 1, 0)
        )                               as games_played
    from matchups,
        lateral flatten(input => matchup:away_lineup) p
),

all_players as (
    select * from home_players
    union all
    select * from away_players
)

select
    p.*,
    coalesce(n.nickname, p.player_name) as display_name
from all_players p
left join {{ ref('player_nicknames') }} n
    on p.player_id = n.player_id
