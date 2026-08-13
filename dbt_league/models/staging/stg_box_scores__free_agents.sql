-- One row per unrostered player per scoring day from ESPN's RAW box scores.
--
-- Project the free_agents sub-document, stream its elements into a CTE, and
-- materialize the typed player rows before the final stg_box_scores union.
-- The streamed-array spelling is load-bearing: DuckDB's ordinary lateral
-- cast(json as json[]) retains the parent once per element and exhausted the
-- public 6 GB cap on a measured 142-day season-points league. The same data
-- completes in under a second when the SELECT-list unnest lands here first.

{{ config(materialized='table') }}

with free_agent_source as (
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        {{ json_get('raw_json', 'free_agents') }} as free_agents_json
    from {{ source('raw', 'box_scores') }}
),

free_agents as (
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        {{ streamed_array_value('free_agents_json', 'f') }} as player_json
    from free_agent_source
        {{- streamed_array_join('free_agents_json', 'f') }}
)

select
    league_key,
    season_year,
    scoring_period,
    matchup_period,
    cast(null as string)                                      as owner_name,
    cast(null as string)                                      as team_name,
    cast(null as integer)                                     as team_id,
    cast(null as string)                                      as team_abbrev,
    cast(null as string)                                      as home_away,
    {{ json_text('player_json', 'name') }}::string            as player_name,
    {{ json_text('player_json', 'playerId') }}::integer       as player_id,
    {{ json_text('player_json', 'position') }}::string        as position,
    {{ json_text('player_json', 'lineupSlot') }}::string      as lineup_slot,
    {{ json_text('player_json', 'clubOfGame') }}::string      as pro_team,
    {{ json_text('player_json', 'points') }}::double          as points,
    {{ json_get('player_json', 'breakdown') }}                as breakdown,
    {{ json_get('player_json', 'eligibleSlots') }}            as eligible_slots,
    coalesce(
        {{ json_text('player_json', 'games_played') }}::integer,
        {{ iff(json_keys_count(json_get('player_json', 'breakdown')) ~ ' > 0', '1', '0') }}
    )                                                         as games_played
from free_agents
