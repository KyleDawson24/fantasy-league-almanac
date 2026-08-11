-- int_matchup_season_derivation.sql
-- Can this season's matchup lengths be derived, and if so what is normal?
-- (MLB-235)
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year) PRESENT IN RAW -- never one
-- more. A league-season nobody captured has no row here.
-- ==========================================================================
--
-- THE DEPENDENCY THIS INVERTS. mart_team_season_standings derives the
-- league's standard period length as mode(scoring_days) over periods ALREADY
-- FILTERED to `not is_abnormal` -- so today the hand-maintained flag is an
-- input to the norm that is supposed to explain it. The mode here is taken
-- over EVERY eligible closed period, with no reference to the seed at all,
-- which derives the flag instead. Same statistic, dependency reversed.
--
-- WHY THERE IS NO ROW FOR AN UNCAPTURED SEASON. `unavailable` is a statement
-- about the LEAGUE -- ESPN answered with an empty schedule -- not about this
-- installation. Manufacturing a row to say "unavailable" for a season nobody
-- asked ESPN about would put a verdict where there is no evidence, and a
-- consumer could not tell the two apart. An installation that has never run
-- the opt-in capture gets zero rows, which is the honest shape.
--
-- STATUS VOCABULARY, matching extract/matchup_membership.py term for term:
--
--   derived                    a standard length was established
--   insufficient_evidence      fewer than {{ var('matchup_min_closed_periods', 3) }} closed periods
--   ambiguous_standard_length  the modal length ties
--   unavailable                ESPN served an empty schedule
--   malformed                  the payload cannot be read as membership
--
-- Everything except `derived` means: classify nothing from this season.
--
-- THE TWO FAIL-CLOSED RULES ARE NOT TUNING KNOBS. The floor exists for a
-- specific failure: the only closed period early in a season is the long
-- opening week, and a mode over one observation would bless the anomaly as
-- the norm and then flag every ordinary week after it. The tie rule refuses
-- rather than picking the lower length, which would invent a norm the season
-- does not have.
--
-- THE CONTIGUITY TEST IS ARITHMETIC, DELIBERATELY. Closed ids are distinct,
-- >= 1 and < current_matchup_period by construction, so `closed_period_count
-- = current_matchup_period - 1` is a complete proof that the run is unbroken
-- -- by pigeonhole, no set enumeration. That matters because
-- current_matchup_period is a number the PAYLOAD chose: comparing against a
-- materialised range would let a six-byte edit to a JSON document decide how
-- much memory this model asks for. A malformed payload has to come back
-- malformed, and exhausting the engine on the way to that verdict is not a
-- slower version of returning it. (Same defect, same fix, as the pure
-- parser's `_require_no_gaps`.)
--
-- ONE DELIBERATE DIFFERENCE FROM THE PURE PARSER: a missing seasonId is
-- malformed here, where the parser tolerates it and simply skips the
-- cross-check. The parser reads arbitrary payloads; this model reads STORED
-- ones, and rung 2's capture requires seasonId on every row it writes. A
-- stored row without it did not come from that contract, so the stricter
-- reading is the right one -- and it fails closed either way.

{{ config(materialized='view') }}

{% set min_closed_periods = var('matchup_min_closed_periods', 3) %}

with seasons as (
    select * from {{ ref('stg_matchup_schedule') }}
),

evidence as (
    select * from {{ ref('int_matchup_period_evidence') }}
),

per_season as (
    select
        league_key,
        season_year,
        -- Reconciles against stg's scheduled_matchup_count. An entry whose
        -- matchupPeriodId could not be read never reaches the evidence model,
        -- so a shortfall here is exactly "the payload holds entries this
        -- chain cannot interpret".
        sum(matchup_count) as entries_accounted,
        sum(case when is_closed then 1 else 0 end) as closed_period_count,
        sum(case when is_closed and not is_well_formed then 1 else 0 end)
            as malformed_period_count
    from evidence
    group by 1, 2
),

length_tally as (
    select
        league_key,
        season_year,
        scoring_period_count as period_length,
        count(*) as periods_at_length
    from evidence
    where is_closed
      and is_well_formed
      and scoring_period_count is not null
    group by 1, 2, 3
),

ranked as (
    select
        league_key, season_year, period_length, periods_at_length,
        max(periods_at_length) over (partition by league_key, season_year)
            as top_count
    from length_tally
),

modal as (
    select
        league_key,
        season_year,
        count(*) as tied_lengths,
        min(period_length) as modal_length,
        max(top_count) as modal_period_count
    from ranked
    where periods_at_length = top_count
    group by 1, 2
),

assessed as (
    select
        s.league_key,
        s.season_year,
        s.captured_at,
        s.declared_season_year,
        s.current_matchup_period,
        s.scheduled_matchup_count,
        coalesce(ps.closed_period_count, 0) as closed_period_count,
        coalesce(ps.malformed_period_count, 0) as malformed_period_count,
        m.tied_lengths,
        m.modal_length,
        m.modal_period_count,

        (
            -- the loader's season_year has no independent witness but this
            s.declared_season_year is null
            or s.declared_season_year <> s.season_year
            -- without it nothing can be shown to be closed
            or s.current_matchup_period is null
            -- entries this chain could not interpret
            or coalesce(ps.entries_accounted, 0) <> s.scheduled_matchup_count
            -- a closed period whose sides disagreed, or that had no usable
            -- membership at all
            or coalesce(ps.malformed_period_count, 0) > 0
            -- a hole in the closed run: see the contiguity note above
            or coalesce(ps.closed_period_count, 0) <> s.current_matchup_period - 1
        ) as is_malformed
    from seasons s
    left join per_season ps
        on s.league_key = ps.league_key
       and s.season_year = ps.season_year
    left join modal m
        on s.league_key = m.league_key
       and s.season_year = m.season_year
),

classified as (
    select
        *,
        case
            when is_malformed then 'malformed'
            -- ESPN answered, with nothing in it. Distinct from a season this
            -- installation never captured, which has no row at all.
            when scheduled_matchup_count = 0 then 'unavailable'
            when closed_period_count < {{ min_closed_periods }} then 'insufficient_evidence'
            when tied_lengths > 1 then 'ambiguous_standard_length'
            else 'derived'
        end as derivation_status
    from assessed
)

select
    league_key,
    season_year,
    captured_at,
    declared_season_year,
    current_matchup_period,
    scheduled_matchup_count,
    closed_period_count,
    malformed_period_count,
    derivation_status,
    -- Published ONLY when the season earned it. NULL is not "we think it is
    -- seven": it is the absence of a norm, and every consumer has to treat it
    -- that way.
    case when derivation_status = 'derived' then modal_length end::integer
        as standard_period_length,
    case when derivation_status = 'derived' then modal_period_count end::integer
        as periods_at_standard_length,
    {{ min_closed_periods }}::integer as min_closed_periods_required
from classified
