-- stg_draft_settings.sql
-- The season's draft configuration, flattened from the append-only
-- RAW.DRAFT_SETTINGS snapshots (MLB-243).
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year).
-- ==========================================================================
--
-- WHY IT EXISTS NOW. The first stranger rehearsal drafted on July 31 and
-- began counting on August 1, but the type-5 extraction processed scoring
-- days 1..142 -- the whole MLB season. Production from March through July,
-- before the league existed, is therefore attributed according to the first
-- lineup we ever observed. Every accumulating surface inherits that: season
-- totals, the record book, the boards, the draft-value deltas.
--
-- FIXING IT IS NOT THIS MODEL'S JOB. Clamping the scoring window touches
-- extraction, the daily facts, the record book and every aggregate at once,
-- and the correct treatment of pre-league production is a third category --
-- neither active nor inactive -- rather than a filter. That is deliberately
-- deferred.
--
-- What this model does is make the limitation DETECTABLE, so a workbook can
-- say so. `date` is the draft's completion stamp in epoch milliseconds; put
-- beside stg_mlb__season_calendar's opener it answers "did this league start
-- counting long after the season did?" for the price of one small view, and
-- it changes no number anywhere.
{{ config(materialized='view') }}

with latest_extraction as (
    select
        league_key,
        season_year,
        raw_json
    from {{ source('raw', 'draft_settings') }}
    qualify row_number() over (
        partition by league_key, season_year
        -- MLB-134 -- total order; see stg_schedule_settings for the reason.
        order by extracted_at desc, hash(raw_json) desc
    ) = 1
)

select
    league_key,
    season_year,
    -- Epoch millis. NULL for a season whose draft has not happened, which
    -- is a real answer and must not become a date.
    {{ epoch_ms_to_timestamp(json_text('raw_json', 'date')) }}
        as drafted_at,
    {{ json_text('raw_json', 'type') }}::string           as draft_type,
    {{ json_text('raw_json', 'keeperCount') }}::integer   as keeper_count
from latest_extraction
