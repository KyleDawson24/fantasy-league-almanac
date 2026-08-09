-- stg_divisions.sql
-- The league's divisions per season, flattened from the divisions array
-- inside the append-only RAW.SCHEDULE_SETTINGS snapshots (MLB-227).
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, division_id).
-- ==========================================================================
--
-- TWO DATA-SHAPE TRAPS live in this array, both measured on the real payload
-- rather than anticipated:
--
--   1. DIVISION IDS ARE NOT CONTIGUOUS. A season that has dropped divisions
--      keeps the SURVIVING ids rather than renumbering, so a two-division
--      season can present ids 0 and 3 with no 1 or 2 -- which is exactly the
--      shape one season here has. Anything that indexes divisions
--      positionally, or assumes id = row number, silently mislabels every
--      team in the second division. The id is carried through as ESPN's own
--      value and joined on; the schema test asserting every team's
--      division_id resolves is what would catch a regression here.
--
--   2. NAMES CARRY TRAILING WHITESPACE. At least one division name is stored
--      with a trailing space, in every season it appears. Untrimmed it
--      renders as a distinct label from its own trimmed spelling anywhere
--      the two meet, and groups separately. Trimmed here, at the staging
--      boundary, so no consumer has to remember; RAW still holds the
--      verbatim payload for anyone who needs the original.
--
-- division_size is ESPN's declared size, not a count of teams observed in
-- stg_team_standings. They agree today; keeping the declared value means a
-- disagreement stays visible instead of being defined away.

{{ config(materialized='view') }}

with latest_extraction as (
    select
        league_key,
        season_year,
        raw_json
    from {{ source('raw', 'schedule_settings') }}
    qualify row_number() over (
        partition by league_key, season_year
        -- MLB-134 -- total order; see stg_schedule_settings for the reason.
        order by extracted_at desc, hash(raw_json) desc
    ) = 1
)

select
    le.league_key,
    le.season_year,
    {{ json_text('d.value', 'id') }}::integer            as division_id,
    trim({{ json_text('d.value', 'name') }}::string)     as division_name,
    {{ json_text('d.value', 'size') }}::integer          as division_size
from latest_extraction le,
    {{ flatten_array(json_get('le.raw_json', 'divisions'), 'd') }}
