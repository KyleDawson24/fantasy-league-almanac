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

        -- Completion evidence. A finished season pins currentMatchupPeriod ON
        -- its final period rather than past it (2025 came back 26 of 26), so
        -- the strict rule alone would discard the last completed week of
        -- every finished season. These two are what prove the season is over,
        -- and STRICTLY so: equal does not prove it, because the final scoring
        -- day can be the day in progress. Measured 2025: 196 > 195. Measured
        -- 2026: 140 < 187.
        --
        -- isActive is deliberately NOT used and is the tempting one: it was
        -- True for BOTH the finished season and the live one, so a policy
        -- built on it would promote the in-flight period every week.
        --
        -- GATED ON THE JSON TYPE, not on a numeric regex over the text. The
        -- regex form is the obvious one and it is silently lax: unwrapping to
        -- text turns the JSON number 196 and the JSON STRING "196" into the
        -- same '196', so a regex accepts both -- while the pure parser's
        -- isinstance(value, int) rejects the string. That gap let malformed
        -- completion evidence promote a period in SQL that Python refused to
        -- promote. See json_is_integer for the measured spellings.
        {% set latest_json = json_get('raw_json', 'status', 'latestScoringPeriod') %}
        case when {{ json_is_integer(latest_json) }}
             then {{ try_to_number(json_unwrap_text(latest_json)) }}
             end::integer as latest_scoring_period_raw,

        {% set final_json = json_get('raw_json', 'status', 'finalScoringPeriod') %}
        case when {{ json_is_integer(final_json) }}
             then {{ try_to_number(json_unwrap_text(final_json)) }}
             end::integer as final_scoring_period_raw,

        {{ json_get('raw_json', 'schedule') }} as schedule,

        -- Entry count, NOT period count: one matchup period holds several
        -- matchups. int_matchup_season_derivation reconciles this against the
        -- entries it could actually read, which is how an unreadable
        -- matchupPeriodId is caught rather than silently dropped.
        {{ json_array_length(json_get('raw_json', 'schedule')) }}::integer
            as scheduled_matchup_count
    from latest_extraction
),

bounded as (
    select
        league_key,
        season_year,
        captured_at,
        declared_season_year,
        current_matchup_period,
        schedule,
        scheduled_matchup_count,
        -- The pure parser's `_optional_scoring_period` also refuses a
        -- non-positive id, so the two agree on 0 and negatives as well as on
        -- type. Applied here rather than inline so the type gate above reads
        -- as one idea.
        case when latest_scoring_period_raw >= 1
             then latest_scoring_period_raw end as latest_scoring_period,
        case when final_scoring_period_raw >= 1
             then final_scoring_period_raw end as final_scoring_period
    from typed
)

select
    *,
    -- Non-null on purpose: a missing or malformed completion field is not
    -- "unknown completion", it is "completion not proven", and every consumer
    -- of this flag treats those identically. Keeping it NULL would push a
    -- three-valued test onto each of them.
    coalesce(latest_scoring_period > final_scoring_period, false)
        as season_is_complete
from bounded
