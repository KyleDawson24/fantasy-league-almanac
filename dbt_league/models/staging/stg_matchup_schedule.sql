-- stg_matchup_schedule.sql
-- The latest captured mMatchupScore snapshot per league-season (MLB-235).
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year).
-- ==========================================================================
--
-- WHAT THIS IS FOR. The extract's schedule chain is circular: load_schedule()
-- reads the hand-maintained matchup_schedule seed, get_scoring_periods()
-- turns its dates into scoring-period ids, and the extract stamps that answer
-- onto every RAW.BOX_SCORES row -- so the warehouse cannot use BOX_SCORES to
-- prove the mapping it was handed. RAW.MATCHUP_SCHEDULE can: the KEYS of
-- schedule[].home/away.pointsByScoringPeriod are ESPN's own membership, and
-- nothing in this chain reads a seed.
--
-- This model does the snapshot selection and NOTHING ELSE. The schedule array
-- passes through as JSON; int_matchup_period_evidence interprets it. Extract
-- captures, staging selects, intermediate interprets.
--
-- PRESENT-AND-EMPTY IS A SUPPORTED STATE, not a degraded one. The capture is
-- opt-in (extract.py --include-matchup-schedule), so a warehouse that has
-- never run it holds this table with zero rows, and every model downstream
-- resolves to zero rows without erroring. No league-season is invented to
-- carry a status: a season nobody captured has no row here, and a row that
-- does not exist cannot be said to be "unavailable" -- that word is reserved
-- for a season ESPN answered with an empty schedule, which is a statement
-- about the league rather than about this installation.
--
-- READ AS TEXT, PARSED DEFENSIVELY. `try_to_number` rather than a bare cast
-- so a payload that stored something non-numeric lands NULL and fails closed
-- downstream, instead of raising mid-build. The regex bound mirrors the pure
-- parser's `_require_positive_int`: ASCII digits only (so a full-width digit
-- is refused rather than silently accepted as an ordinary number, which is
-- what a bare cast does on both engines).

{{ config(materialized='view') }}

{% set readable_int = '[0-9]{1,9}' %}

with latest_extraction as (
    select
        league_key,
        season_year,
        raw_json,
        extracted_at
    from {{ source('raw', 'matchup_schedule') }}
    qualify row_number() over (
        partition by league_key, season_year
        -- MLB-134 -- total order. extracted_at alone ties whenever one
        -- extract stamps two payload versions of the same entity (a re-run
        -- or a double-capture). RAW carries no load sequence id, so the
        -- payload hash is the only discriminator available; it can only ever
        -- choose between byte-identical payloads, which makes the VALUE
        -- deterministic even though the row choice is arbitrary.
        --
        -- It matters more here than for its neighbours: membership is
        -- RETROSPECTIVE, so two snapshots of one season genuinely differ --
        -- the later one has closed a period the earlier had in flight. The
        -- newest is the only one that describes the season as it now stands.
        order by extracted_at desc, hash(raw_json) desc
    ) = 1
),

typed as (
    select
        league_key,
        season_year,
        extracted_at as captured_at,

        -- ESPN's own season label. The row's season_year was stamped by the
        -- LOADER, so it agrees with itself no matter which season the
        -- document described; this is the only independent answer, and
        -- int_matchup_season_derivation refuses a season where the two
        -- disagree.
        {% set declared = json_unwrap_text(json_get('raw_json', 'seasonId')) %}
        case when {{ regexp_like(declared, readable_int) }}
             then {{ try_to_number(declared) }} end::integer
            as declared_season_year,

        -- The closed-period policy in one number. Membership is
        -- retrospective: the period in flight is still filling in and reads
        -- short, which is indistinguishable from a real abnormality.
        {% set current = json_unwrap_text(json_get('raw_json', 'status', 'currentMatchupPeriod')) %}
        case when {{ regexp_like(current, readable_int) }}
             then {{ try_to_number(current) }} end::integer
            as current_matchup_period,

        {{ json_get('raw_json', 'schedule') }} as schedule,

        -- Entry count, NOT period count: one matchup period holds several
        -- matchups. int_matchup_season_derivation reconciles this against the
        -- entries it could actually read, which is how an unreadable
        -- matchupPeriodId is caught rather than silently dropped.
        {{ json_array_length(json_get('raw_json', 'schedule')) }}::integer
            as scheduled_matchup_count
    from latest_extraction
)

select * from typed
