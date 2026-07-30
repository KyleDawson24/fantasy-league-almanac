-- stg_box_scores.sql
-- Flatten raw JSON into one row per player per scoring period. Includes
-- both rostered players (with team_id/owner) and free agents (with NULLs).
-- Foundational grain for scoring, stats, and wasted-points chains.
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
--
-- Phase 4: raw JSON shape changed from a bare matchups array to a dict with
-- two keys: `matchups` (rostered lineups) and `free_agents` (anti-join rows
-- for unrostered MLB players who had stats). FA rows carry NULL team fields
-- and lineup_slot='FA'. Historical pre-Phase-4 rows still have the array
-- shape and only matchups; the unioned subqueries handle both shapes via
-- defensive raw_json:matchups extraction (returns NULL on the array shape,
-- which the lateral flatten then yields zero rows for).
--
-- lineup_slot_category derived here as a clean three-value bucket:
--   'pitching' = SP/RP/P (any pitcher slot)
--   'hitting'  = any other active slot (1B, 2B, ..., DH, UTIL, etc.)
--   'inactive' = BE/IL/FA
-- Used downstream at int_player_daily for the slot-stat-category
-- compatibility filter (a hitter's hitting stats only count when they're in
-- a hitting slot, etc.) -- toggleable via the strict_slot_validity dbt var.

with raw as (
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        raw_json
    from {{ source('raw', 'box_scores') }}
),

-- Phase 4 raw shape: {"matchups": [...], "free_agents": [...]}.
-- Pre-Phase-4 raw shape: bare array of matchup dicts. The lateral flatten
-- on raw_json:matchups returns zero rows for the array shape (because
-- raw_json:matchups is NULL on a JSON array), and the COALESCE-to-array
-- handles the legacy shape. Both shapes can coexist post-Phase-4 backfill
-- but in practice we --full-refresh, so this is defense-in-depth.
matchups as (
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        m.value as matchup
    from raw,
        {{ flatten_array('coalesce(' ~ json_get('raw_json', 'matchups') ~ ', raw_json)', 'm') }}
),

home_players as (
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        {{ json_text('matchup', 'home_owner') }}::string         as owner_name,
        {{ json_text('matchup', 'home_team') }}::string          as team_name,
        {{ json_text('matchup', 'home_team_id') }}::integer      as team_id,
        {{ json_text('matchup', 'home_team_abbrev') }}::string   as team_abbrev,
        'home'                             as home_away,
        {{ json_text('p.value', 'name') }}::string               as player_name,
        {{ json_text('p.value', 'playerId') }}::integer          as player_id,
        {{ json_text('p.value', 'position') }}::string           as position,
        {{ json_text('p.value', 'lineupSlot') }}::string         as lineup_slot,
        {{ json_text('p.value', 'proTeam') }}::string            as pro_team,
        {{ json_text('p.value', 'points') }}::double              as points,
        {{ json_get('p.value', 'breakdown') }}                  as breakdown,
        {{ json_get('p.value', 'eligibleSlots') }}              as eligible_slots,
        coalesce(
            {{ json_text('p.value', 'games_played') }}::integer,
            {{ iff(json_keys_count(json_get('p.value', 'breakdown')) ~ ' > 0', '1', '0') }}
        )                                  as games_played
    from matchups,
        {{ flatten_array(json_get('matchup', 'home_lineup'), 'p') }}
),

away_players as (
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        {{ json_text('matchup', 'away_owner') }}::string         as owner_name,
        {{ json_text('matchup', 'away_team') }}::string          as team_name,
        {{ json_text('matchup', 'away_team_id') }}::integer      as team_id,
        {{ json_text('matchup', 'away_team_abbrev') }}::string   as team_abbrev,
        'away'                             as home_away,
        {{ json_text('p.value', 'name') }}::string               as player_name,
        {{ json_text('p.value', 'playerId') }}::integer          as player_id,
        {{ json_text('p.value', 'position') }}::string           as position,
        {{ json_text('p.value', 'lineupSlot') }}::string         as lineup_slot,
        {{ json_text('p.value', 'proTeam') }}::string            as pro_team,
        {{ json_text('p.value', 'points') }}::double              as points,
        {{ json_get('p.value', 'breakdown') }}                  as breakdown,
        {{ json_get('p.value', 'eligibleSlots') }}              as eligible_slots,
        coalesce(
            {{ json_text('p.value', 'games_played') }}::integer,
            {{ iff(json_keys_count(json_get('p.value', 'breakdown')) ~ ' > 0', '1', '0') }}
        )                                  as games_played
    from matchups,
        {{ flatten_array(json_get('matchup', 'away_lineup'), 'p') }}
),

-- Phase 4 free agents: top-level array on the raw JSON dict, parallel to
-- matchups[]. NULL team fields by construction (FAs have no fantasy team).
-- home_away is also NULL — they're not on either side of any matchup.
free_agents as (
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        cast(null as string)               as owner_name,
        cast(null as string)               as team_name,
        cast(null as integer)              as team_id,
        cast(null as string)               as team_abbrev,
        cast(null as string)               as home_away,
        {{ json_text('f.value', 'name') }}::string               as player_name,
        {{ json_text('f.value', 'playerId') }}::integer          as player_id,
        {{ json_text('f.value', 'position') }}::string           as position,
        {{ json_text('f.value', 'lineupSlot') }}::string         as lineup_slot,
        {{ json_text('f.value', 'proTeam') }}::string            as pro_team,
        {{ json_text('f.value', 'points') }}::double              as points,
        {{ json_get('f.value', 'breakdown') }}                  as breakdown,
        {{ json_get('f.value', 'eligibleSlots') }}              as eligible_slots,
        coalesce(
            {{ json_text('f.value', 'games_played') }}::integer,
            {{ iff(json_keys_count(json_get('f.value', 'breakdown')) ~ ' > 0', '1', '0') }}
        )                                  as games_played
    from raw,
        {{ flatten_array(json_get('raw_json', 'free_agents'), 'f') }}
),

all_players as (
    select * from home_players
    union all
    select * from away_players
    union all
    select * from free_agents
)

select
    p.*,
    coalesce(n.nickname, p.player_name) as display_name,
    case
        when p.lineup_slot in ('SP', 'RP', 'P')   then 'pitching'
        when p.lineup_slot in ('BE', 'IL', 'FA')  then 'inactive'
        else 'hitting'
    end as lineup_slot_category
from all_players p
left join {{ ref('player_nicknames') }} n
    on p.player_id = n.player_id
