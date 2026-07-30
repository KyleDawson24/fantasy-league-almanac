-- stg_team_owners.sql
-- v1.2: per-season team -> owner bridge, flattened from the append-only
-- RAW.TEAM_OWNERS snapshots (one VARIANT array of {team_id, owner_id,
-- first_name, last_name} per extract). Latest snapshot per season,
-- mirroring the stg pattern over scoring/roster settings.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, team_id, owner_id).
--   Co-owned teams yield multiple rows per (league, season, team).
-- ==========================================================================
--
-- owner_id is the stable ESPN member GUID -- the join key dim_owner and
-- the owner_nicknames seed use. Names here come from the settings fetch
-- (proper-cased, e.g. "McAvery"), not the box-score owner string, which
-- the extract's format_owners() runs through .title() ("Mcginley").

{{ config(materialized='view') }}

with latest_extraction as (
    select
        league_key,
        season_year,
        raw_json
    from {{ source('raw', 'team_owners') }}
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
    t.value:team_id::integer    as team_id,
    t.value:owner_id::string    as owner_id,
    t.value:first_name::string  as first_name,
    t.value:last_name::string   as last_name
from latest_extraction le,
    {{ flatten_array('le.raw_json', 't') }}
