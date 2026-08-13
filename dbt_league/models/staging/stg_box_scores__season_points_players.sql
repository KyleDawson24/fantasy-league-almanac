-- One row per rostered player per scoring day for ESPN season-long points.
--
-- This materialized boundary is load-bearing on DuckDB. The parent model
-- stg_box_scores__team_rosters has already discarded the full RAW payload;
-- this model then flattens each team's lineup and discards the lineup JSON
-- before stg_box_scores unions rostered players with free agents. Keeping
-- both the flatten and the final union in one statement retained enough JSON
-- state to exhaust a 6 GB DuckDB cap on a 142-day, one-season public run.
-- Snowflake's lateral flatten streams this shape, but the boundary preserves
-- identical rows on both engines and keeps the consumer-laptop contract true.

{{ config(materialized='table') }}

select
    league_key,
    season_year,
    scoring_period,
    matchup_period,
    owner_name,
    team_name,
    team_id,
    team_abbrev,
    cast(null as string)                                      as home_away,
    {{ json_text('p.value', 'name') }}::string                as player_name,
    {{ json_text('p.value', 'playerId') }}::integer           as player_id,
    {{ json_text('p.value', 'position') }}::string            as position,
    {{ json_text('p.value', 'lineupSlot') }}::string          as lineup_slot,
    {{ json_text('p.value', 'clubOfGame') }}::string          as pro_team,
    {{ json_text('p.value', 'points') }}::double              as points,
    {{ json_get('p.value', 'breakdown') }}                    as breakdown,
    {{ json_get('p.value', 'eligibleSlots') }}                as eligible_slots,
    coalesce(
        {{ json_text('p.value', 'games_played') }}::integer,
        {{ iff(json_keys_count(json_get('p.value', 'breakdown')) ~ ' > 0', '1', '0') }}
    )                                                         as games_played
from {{ ref('stg_box_scores__team_rosters') }},
    {{ flatten_array('lineup', 'p') }}
