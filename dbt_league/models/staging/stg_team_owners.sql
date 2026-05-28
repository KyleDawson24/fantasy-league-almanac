-- stg_team_owners.sql
-- v1.2: per-season team -> owner bridge, flattened from the append-only
-- RAW.TEAM_OWNERS snapshots (one VARIANT array of {team_id, owner_id,
-- first_name, last_name} per extract). Latest snapshot per season,
-- mirroring the stg pattern over scoring/roster settings.
--
-- ==========================================================================
-- GRAIN: one row per (season_year, team_id, owner_id).
--   Co-owned teams yield multiple rows per (season, team).
-- ==========================================================================
--
-- owner_id is the stable ESPN member GUID -- the join key dim_owner and
-- the owner_nicknames seed use. Names here come from the settings fetch
-- (proper-cased, e.g. "McAvery"), not the box-score owner string, which
-- the extract's format_owners() runs through .title() ("Mcginley").

{{ config(materialized='view') }}

with latest_extraction as (
    select
        season_year,
        raw_json
    from {{ source('raw', 'team_owners') }}
    qualify row_number() over (
        partition by season_year
        order by extracted_at desc
    ) = 1
)

select
    le.season_year,
    t.value:team_id::integer    as team_id,
    t.value:owner_id::string    as owner_id,
    t.value:first_name::string  as first_name,
    t.value:last_name::string   as last_name
from latest_extraction le,
    lateral flatten(input => le.raw_json) t
