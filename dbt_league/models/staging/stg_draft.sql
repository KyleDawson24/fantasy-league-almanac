-- stg_draft.sql
-- v1.2: per-season draft board, flattened from the append-only
-- RAW.DRAFT_PICKS snapshots (one VARIANT array of pick dicts per extract).
-- Latest snapshot per season, mirroring stg_team_owners over RAW.TEAM_OWNERS.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, overall_pick).
-- ==========================================================================
--
-- Sourced from the espn-api wrapper's league.draft (player names + the
-- drafting team resolved at extract time). overall_pick is the true overall
-- selection number, snake order included. keeper flags picks retained from
-- the prior season (this is a keeper league) -- they occupy real draft slots
-- but weren't competitively drafted, so consumers can label them.

{{ config(materialized='view') }}

with latest_extraction as (
    select
        league_key,
        season_year,
        raw_json
    from {{ source('raw', 'draft_picks') }}
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
    le.league_key,
    le.season_year,
    {{ json_text('p.value', 'overall_pick') }}::integer as overall_pick,
    {{ json_text('p.value', 'round_num') }}::integer    as round_num,
    {{ json_text('p.value', 'round_pick') }}::integer   as round_pick,
    {{ json_text('p.value', 'player_id') }}::integer    as player_id,
    {{ json_text('p.value', 'player_name') }}::string   as player_name,
    {{ json_text('p.value', 'team_id') }}::integer      as team_id,
    {{ json_text('p.value', 'keeper') }}::boolean       as keeper
from latest_extraction le,
    {{ flatten_array('le.raw_json', 'p') }}
