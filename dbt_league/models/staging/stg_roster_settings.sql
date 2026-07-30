-- stg_roster_settings.sql
-- Long-form reshape of the raw rosterSettings payload (latest snapshot
-- per league + season): one row per (league_key, season_year,
-- setting_type, espn_id).
--
--   setting_type = 'lineup_slot_count': lineupSlotCounts entries --
--     lineup slot id (0=C, 14=SP, 16=BE, ...) -> configured starters.
--   setting_type = 'position_limit': positionLimits entries -- default
--     MLB position id (1=SP, 2=C, 11=RP, ...) -> max rostered (-1 = ESPN
--     "No Limit").
--
-- Pure reshape; the slot-name map, No-Limit / N/A display shaping, and
-- the active-slot flag live in dim_roster_slot_counts.

{{ config(materialized='view') }}

with latest_extraction as (
    select
        league_key,
        season_year,
        raw_json
    from {{ source('raw', 'roster_settings') }}
    qualify row_number() over (
        partition by league_key, season_year
        -- MLB-134 -- total order. extracted_at alone ties whenever one
        -- extract stamps two payload versions of the same entity (a re-run
        -- or a double-capture). RAW carries no load sequence id, so the
        -- payload hash is the only discriminator available; it can only ever
        -- choose between byte-identical payloads, which makes the VALUE
        -- deterministic even though the row choice is arbitrary.
        order by extracted_at desc, hash(raw_json) desc
    ) = 1
)

select
    e.league_key,
    e.season_year,
    'lineup_slot_count' as setting_type,
    f.key::integer      as espn_id,
    f.value::integer    as setting_value
from latest_extraction e,
    {{ flatten_object('e.raw_json:lineupSlotCounts', 'f') }}

union all

select
    e.league_key,
    e.season_year,
    'position_limit',
    f.key::integer,
    f.value::integer
from latest_extraction e,
    {{ flatten_object('e.raw_json:positionLimits', 'f') }}
