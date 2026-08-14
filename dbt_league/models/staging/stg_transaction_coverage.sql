-- stg_transaction_coverage.sql
-- Was this league-season's transaction board actually read? (MLB-243)
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year).
-- ==========================================================================
--
-- WHY THIS EXISTS. `fct_roster_stints` used to gate itself on the presence of
-- exploded `stg_transactions` rows. That test cannot distinguish the two
-- states that matter most:
--
--   * a league whose board was READ and genuinely held nothing -- a new or
--     quiet league, which is completely normal and whose stints should build
--     (every stint opens on the opening-day roster, and with no trade edges
--     any team-to-team handoff is a drop/add, not a trade);
--   * a league whose board was never successfully read -- refused, or not
--     served for that season -- where classifying acquisitions at all would
--     be inventing them.
--
-- Both produce zero staged transaction rows. So the gate refused the first
-- along with the second, and a perfectly ordinary league got zero roster
-- stints, no acquisition channels, and no explanation.
--
-- PRESERVING A GOOD CAPTURE. The feed measurably flaps -- the same
-- well-formed request returned 401 and 200 minutes apart (measured
-- 2026-08-14) -- so a later unavailable attempt must not un-prove an earlier
-- success. The resolution below prefers the most recent SERVED row and only
-- falls back to the latest row overall when nothing has ever succeeded.
--
-- ADDITIVE FOR EXISTING LEAGUES. Coverage rows only exist for extracts run
-- after this model shipped, so a league captured earlier has none. Rather
-- than dropping those leagues out of the gate, staged transaction rows are
-- themselves treated as proof the board was read: if rows exist, it was.
-- Every league that builds stints today keeps building them; only a
-- proven-empty board newly qualifies.
{{ config(materialized='view') }}

with recorded as (
    select
        league_key,
        season_year,
        {{ json_text('raw_json', 'outcome') }}::string        as outcome,
        {{ json_text('raw_json', 'topic_count') }}::integer   as topic_count,
        {{ json_text('raw_json', 'http_status') }}::integer   as http_status,
        {{ json_text('raw_json', 'attempts') }}::integer      as attempts,
        extracted_at
    from {{ source('raw', 'transaction_coverage') }}
),

-- The most recent SERVED verdict wins; failing that, the most recent verdict
-- of any kind. `is_served` sorts first so one flap cannot bury a success.
resolved as (
    select
        league_key,
        season_year,
        outcome,
        topic_count,
        http_status,
        attempts,
        extracted_at as coverage_captured_at
    from (
        select
            recorded.*,
            row_number() over (
                partition by league_key, season_year
                order by
                    case when outcome in ('SERVED_NONEMPTY', 'SERVED_EMPTY')
                         then 0 else 1 end,
                    extracted_at desc,
                    -- MLB-134 total order; see stg_schedule_settings.
                    hash(outcome) desc
            ) as pick
        from recorded
    )
    where pick = 1
),

-- Legacy captures: rows in the transaction log are themselves proof the
-- board was read, for seasons extracted before coverage was recorded.
legacy as (
    select distinct
        league_key,
        season_year,
        'SERVED_NONEMPTY' as outcome,
        cast(null as {{ dbt.type_int() }})    as topic_count,
        cast(null as {{ dbt.type_int() }})    as http_status,
        cast(null as {{ dbt.type_int() }})    as attempts,
        cast(null as {{ dbt.type_timestamp() }}) as coverage_captured_at
    from {{ ref('stg_transactions') }}
    where (league_key, season_year) not in (
        select league_key, season_year from resolved
    )
),

unioned as (
    select * from resolved
    union all
    select * from legacy
)

select
    league_key,
    season_year,
    outcome,
    topic_count,
    http_status,
    attempts,
    coverage_captured_at,
    -- THE GATE. True when the board was actually read, whatever it held.
    outcome in ('SERVED_NONEMPTY', 'SERVED_EMPTY') as has_transaction_log,
    -- Proven zero, as opposed to merely absent. Consumers that want to say
    -- "no transactions occurred" need THIS, not an empty row count.
    outcome = 'SERVED_EMPTY'                       as is_proven_empty
from unioned
