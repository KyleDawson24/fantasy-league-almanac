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

-- MEMORY SHAPE (MLB-10): each branch projects the sub-document it needs
-- BEFORE flattening, instead of carrying the whole raw_json into the
-- flatten. This is a pure projection change -- same values, same rows --
-- and on Snowflake it is invisible, because LATERAL FLATTEN streams and
-- the optimizer prunes the unread column either way.
--
-- On DuckDB it is the difference between building and not building.
-- `cast(x as json[])` MATERIALIZES the whole array where FLATTEN streams
-- it, and when the fat parent rides along it is retained per extracted
-- element: the free-agent flatten over the 236 KB average payload
-- exhausted 5.5 GB of a 6 GB cap on 319 rows, while the same flatten over
-- the projected sub-document finishes in 2.2s. Measured, not guessed --
-- 200 rows passed and 119 rows passed, but the two together did not,
-- which is what ruled out bad data and pointed at parent retention.
--
-- The 6 GB cap is deliberately NOT the thing that moves here: it is the
-- "runs on a stranger's laptop" promise (MLB-109/127), so raising it to
-- go green would delete the acceptance criterion rather than meet it.
-- This narrow-before-flatten shape is the house pattern for the same
-- class elsewhere.
--
-- Phase 4 raw shape: {"matchups": [...], "free_agents": [...]}.
-- Pre-Phase-4 raw shape: bare array of matchup dicts. raw_json:matchups
-- is NULL on the array shape, so the COALESCE falls through to raw_json
-- itself and the legacy rows still flatten. Both arms stay ARRAYS, which
-- DuckDB requires -- a JSON object there raises rather than yielding zero
-- rows. Both shapes can coexist post-Phase-4 backfill but in practice we
-- --full-refresh, so this is defense-in-depth.
with free_agent_source as (
    -- The sub-document is projected BEFORE the flatten so the 236 KB
    -- parent payload does not ride into it -- see the memory note in
    -- stg_box_scores__matchups for why that matters on DuckDB. On its own
    -- this took the free-agent branch from OOM to 2.2s.
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        {{ json_get('raw_json', 'free_agents') }} as free_agents_json
    from {{ source('raw', 'box_scores') }}
),

home_players as (
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        home_owner                         as owner_name,
        home_team                          as team_name,
        home_team_id                       as team_id,
        home_team_abbrev                   as team_abbrev,
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
    from {{ ref('stg_box_scores__matchups') }},
        {{ flatten_array('home_lineup', 'p') }}
),

away_players as (
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        away_owner                         as owner_name,
        away_team                          as team_name,
        away_team_id                       as team_id,
        away_team_abbrev                   as team_abbrev,
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
    from {{ ref('stg_box_scores__matchups') }},
        {{ flatten_array('away_lineup', 'p') }}
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
    from free_agent_source,
        {{ flatten_array('free_agents_json', 'f') }}
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
