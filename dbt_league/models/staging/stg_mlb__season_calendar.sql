-- stg_mlb__season_calendar.sql
-- The season's first ESPN scoring date, from MLB's own public season record
-- (MLB-235 rung 4B-2).
--
-- ==========================================================================
-- GRAIN: one row per (season_year). NOT per league.
-- ==========================================================================
--
-- WHY THE GRAIN DROPS league_key, when every other staging model keeps it.
-- MLB's regular season starts when it starts; it is a fact about baseball,
-- not about a fantasy league. RAW.MLB_SEASON_CALENDAR carries a league_key
-- only because the snapshot table shape is uniform and both sinks already
-- write it -- it records which league's run happened to fetch the calendar,
-- which is provenance, not a key. Keying on it here would mean a second ESPN
-- league in the same warehouse could not see an anchor the first one already
-- measured, and could disagree with it.
--
-- WHAT IT IS FOR. ESPN serves matchup-period membership as daily
-- scoring-period IDS and no ISO dates. One anchor turns the whole thing into
-- a calendar:
--
--     scoring period N  ==  season_opener + (N - 1) days
--
-- and a matchup period's start/end are the min and max of its membership.
-- Before this model that anchor came from the earliest start_date in the
-- hand-maintained matchup_schedule seed, which is exactly the input MLB-235
-- exists to stop requiring.
--
-- IT IS THE REGULAR-SEASON START, and the distinction is load-bearing rather
-- than pedantic. MLB opens with a special or standalone game days before the
-- conventional full-slate Opening Day, and not the same way twice: 2025-03-18
-- was the Tokyo Series, 2026-03-25 a single Yankees-Giants Opening Night in
-- San Francisco. Anchoring to "the day everyone plays" would therefore have
-- been wrong in both seasons on file, in two different ways, shifting every
-- date in the season. The endpoint's `seasonStartDate` is spring training
-- (2026-02-20) and is 33 days earlier still. The stored snapshot records
-- which field it used in `anchor_field` for that reason.
--
-- MEASURED AGAINST THE SEED, not assumed to match it: restricted to CLOSED
-- periods, this anchor reproduces the hand-maintained calendar on all 44
-- periods of 2025 and 2026, zero mismatches, long opening weeks and both
-- 14-day All-Star periods included. dim_matchup_period carries a standing
-- test that fails if a derived date and a legacy date ever disagree, so a
-- future divergence stops the build instead of quietly moving the calendar.
--
-- PRESENT-AND-EMPTY IS SUPPORTED. A warehouse whose extract has never
-- reached MLB's API holds this table with zero rows; every consumer left-joins
-- and leaves dates NULL. Unresolved is a real state and it is visible: no
-- dates, and points-since-trade renders unavailable rather than guessing.

{{ config(materialized='view') }}

with latest_extraction as (
    select
        season_year,
        raw_json,
        extracted_at
    from {{ source('raw', 'mlb_season_calendar') }}
    qualify row_number() over (
        -- Season alone, per the grain note above. hash() breaks the tie the
        -- same way stg_matchup_schedule does: two runs in one second can
        -- stamp the same extracted_at, and RAW carries no load sequence, so
        -- the payload hash is the only discriminator available. It can only
        -- ever choose between byte-identical payloads.
        partition by season_year
        order by extracted_at desc, hash(raw_json) desc
    ) = 1
)

select
    season_year,
    extracted_at as captured_at,

    -- MLB's own answer to which season this describes. Stored as an integer
    -- by the capture (the API sends the string '2026'), and checked here for
    -- the same reason stg_matchup_schedule checks ESPN's seasonId: the row's
    -- season_year was stamped by the LOADER, so it agrees with itself no
    -- matter which season the document described.
    {{ try_to_number(json_unwrap_text(json_get('raw_json', 'seasonId'))) }}::integer
        as declared_season_year,

    -- THE ANCHOR. try_parse_date rather than a cast so a payload that stored
    -- something unparseable lands NULL and every date downstream stays NULL,
    -- instead of raising mid-build or -- far worse -- coercing to a date that
    -- looks fine and is wrong.
    {{ try_parse_date(json_unwrap_text(json_get('raw_json', 'regularSeasonStartDate')),
                      'YYYY-MM-DD', '%Y-%m-%d') }} as season_opener,

    {{ try_parse_date(json_unwrap_text(json_get('raw_json', 'regularSeasonEndDate')),
                      'YYYY-MM-DD', '%Y-%m-%d') }} as regular_season_end,

    -- Provenance, carried so a reader can tell WHERE a date came from without
    -- leaving the row: which URL answered, and which of the endpoint's
    -- several date fields was taken as scoring period 1.
    {{ json_unwrap_text(json_get('raw_json', 'source')) }} as source_url,
    {{ json_unwrap_text(json_get('raw_json', 'anchor_field')) }} as anchor_field
from latest_extraction
