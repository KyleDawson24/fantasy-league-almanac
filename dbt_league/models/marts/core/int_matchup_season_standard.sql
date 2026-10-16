-- int_matchup_season_standard.sql
-- One season's standard matchup length, and where it came from (MLB-235 4A).
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year) that ANY source can answer
-- for. Never a synthetic row: a league-season with neither a derived
-- standard nor recomputable gameplay days simply has none.
-- ==========================================================================
--
-- WHY THIS IS A MODEL RATHER THAN A CTE. It was a CTE inside
-- mart_team_season_standings, and it was ANCHORED ON THE FALLBACK -- the
-- legacy recomputation was the FROM and the derived answer a left join. That
-- reads as "derived wins" and is not: a league-season with a valid
-- platform-derived standard but no recomputable fallback row vanished
-- entirely. An ordinary installation happens to produce both, so no real data
-- exposed it; the ownership contract must not depend on the fallback
-- existing. Lifting it out makes the resolution reachable by a fixture that
-- cannot build the whole box-score chain the standings mart sits on.
--
-- A KEY UNIVERSE, then two left joins, so neither input is a prerequisite for
-- the other. Four cases, all intended:
--
--   derived + fallback  ->  derived wins
--   derived only        ->  derived survives
--   fallback only       ->  fallback survives
--   neither             ->  no row at all
--
-- DERIVED IS NOT MOVED BY AN OVERRIDE. It is read straight off
-- dim_matchup_period.standard_period_length, which comes from the platform
-- derivation: a commissioner's ruling about one week changes that week's
-- eligibility and is not evidence about the league's standard week.
--
-- The single-value assumption behind max() is not left to a comment --
-- tests/assert_one_derived_standard_per_league_season.sql fails the build if
-- a league-season ever publishes two.

{{ config(materialized='view') }}

with derived_standard as (
    select
        league_key,
        season_year,
        max(standard_period_length) as derived_standard_days,
        count(distinct standard_period_length) as distinct_derived_standards
    from {{ ref('dim_matchup_period') }}
    where standard_period_length is not null
    group by 1, 2
),

-- Gameplay days per matchup period: days on which any player produced stats.
-- League-wide by construction (no team partition) so every team in a period
-- shares the same denominator.
matchup_scoring_days as (
    select
        league_key,
        season_year,
        matchup_period,
        count(distinct scoring_period) as scoring_days
    from {{ ref('fct_player_daily_performance') }}
    group by 1, 2, 3
),

-- The pre-MLB-235 recomputation, kept as the compatibility fallback for an
-- installation that has only the seed and the dates. It is a mode over
-- ELIGIBLE periods, so it still carries the inverted dependency this ticket
-- exists to remove -- which is exactly why it is now second choice rather
-- than the only choice.
legacy_standard as (
    select
        sd.league_key,
        sd.season_year,
        mode(sd.scoring_days) as legacy_standard_days
    from matchup_scoring_days sd
    inner join {{ ref('dim_matchup_period') }} mp
        on sd.league_key = mp.league_key
        and sd.season_year = mp.season_year
        and sd.matchup_period = mp.matchup_period
    -- MLB-235: is_record_eligible rather than `not is_abnormal`. The old test
    -- silently DROPPED a period whose abnormality is unknown; the gate is
    -- non-null and answers false there, which is the same exclusion said out
    -- loud. The playoff predicate stays separate and explicit -- eligibility
    -- concerns abnormality only, and `is_playoff = false` fails closed on its
    -- own when the flag is unknown.
    where mp.is_record_eligible
      and mp.is_playoff = false
    group by 1, 2
),

-- Neither source is the anchor.
season_keys as (
    select league_key, season_year from derived_standard
    union
    select league_key, season_year from legacy_standard
)

select
    k.league_key,
    k.season_year,
    coalesce(ds.derived_standard_days, ls.legacy_standard_days)
        as standard_matchup_days,
    case when ds.derived_standard_days is not null then 'derived'
         else 'legacy_recomputation' end as standard_source,
    ds.derived_standard_days,
    ls.legacy_standard_days
from season_keys k
left join derived_standard ds
    on k.league_key = ds.league_key
   and k.season_year = ds.season_year
left join legacy_standard ls
    on k.league_key = ls.league_key
   and k.season_year = ls.season_year
