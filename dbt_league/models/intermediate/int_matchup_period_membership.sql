-- int_matchup_period_membership.sql
-- Which scoring periods belong to which matchup period, according to ESPN
-- (MLB-235).
--
-- ==========================================================================
-- GRAIN: one row per
-- (league_key, season_year, matchup_period, scoring_period).
-- ==========================================================================
--
-- THE NON-CIRCULAR ANSWER to the question the matchup_schedule seed answers
-- today. Every value here came out of the KEYS of
-- schedule[].home/away.pointsByScoringPeriod in a payload this project did
-- not shape, and no step between RAW and this model reads a seed, a date, or
-- a matchup_period stamped by the extract.
--
-- WHAT IS NOT HERE, and cannot be: dates. ESPN serves scoring-period IDS and
-- no calendar, so dim_matchup_period's start_date/end_date and the
-- days_in_period arithmetic downstream of it still need the seed. This
-- retires the LENGTH question, not the calendar.
--
-- WHAT IS EXCLUDED, and why each exclusion is a refusal rather than a filter:
--
--   the current matchup period   still filling in; it reads short, which is
--                                indistinguishable from a real abnormality
--   any period whose sides       contested membership is not membership;
--   disagreed                    picking a side would manufacture a verdict
--   every period of a            the payload could not be read as membership
--   malformed season             at all, so none of it is evidence
--
-- An INSUFFICIENT_EVIDENCE or AMBIGUOUS_STANDARD_LENGTH season keeps its
-- rows. Those statuses mean "no norm was established", not "the membership is
-- unknown" -- the same distinction the pure parser draws by returning
-- populated `rows` alongside a refused standard length.
--
-- NO IDENTITY. The grain carries no team, owner, member or franchise: which
-- days a matchup period contains is a property of the SCHEDULE, and the sides
-- are only ever read to prove they agree about it.

{{ config(materialized='view') }}

with eligible as (
    select
        e.league_key,
        e.season_year,
        e.matchup_period,
        e.scoring_periods
    from {{ ref('int_matchup_period_evidence') }} e
    inner join {{ ref('int_matchup_season_derivation') }} d
        on e.league_key = d.league_key
       and e.season_year = d.season_year
    where e.is_closed
      and e.is_well_formed
      and d.derivation_status <> 'malformed'
)

select
    el.league_key,
    el.season_year,
    el.matchup_period,
    sp.value::integer as scoring_period
from eligible el,
    {{ flatten_native_array('el.scoring_periods', 'sp') }}
