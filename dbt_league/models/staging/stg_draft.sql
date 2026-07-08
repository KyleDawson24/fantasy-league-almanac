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
        order by extracted_at desc
    ) = 1
)

select
    le.league_key,
    le.season_year,
    p.value:overall_pick::integer as overall_pick,
    p.value:round_num::integer    as round_num,
    p.value:round_pick::integer   as round_pick,
    p.value:player_id::integer    as player_id,
    p.value:player_name::string   as player_name,
    p.value:team_id::integer      as team_id,
    p.value:keeper::boolean       as keeper
from latest_extraction le,
    lateral flatten(input => le.raw_json) p
