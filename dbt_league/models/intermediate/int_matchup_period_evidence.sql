-- int_matchup_period_evidence.sql
-- What ESPN's payload says about each matchup period, before any statistic
-- is computed over it (MLB-235).
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, matchup_period) whose id was
-- readable -- CLOSED and OPEN alike, distinguished by is_closed.
-- ==========================================================================
--
-- THE ONLY MODEL THAT FLATTENS. Membership, the season verdict and the
-- period shape all read this, so the rule for "which scoring periods does
-- this matchup period contain" is written once. It is the SQL twin of
-- extract/matchup_membership.py's `parse_matchup_membership`, and the two are
-- asserted against each other in tests/test_matchup_membership_models.py --
-- same synthetic payloads through both paths, same answers.
--
-- THE CLOSED-PERIOD POLICY is structural, not calendar-based: a period is
-- eligible only when its id is strictly below status.currentMatchupPeriod.
-- Periods at or after the current one are carried with is_closed = false and
-- are NOT shape-checked, exactly as the Python parser skips them -- an
-- unplayed period is shaped like an unplayed period, and refusing a season
-- over one would be a bug rather than a guard.
--
-- MEMBERSHIP IS THE KEY SET, NEVER THE VALUES. Nothing here reads a point
-- total, so a scoring period on which a team scored exactly zero stays in the
-- membership. Dropping zeroes would shorten real weeks into fake
-- abnormalities (195 of 3,120 values were 0.0 in the 2025+2026 evidence).
--
-- SIDES MUST AGREE. If these keys were "the days THIS TEAM scored" rather
-- than "the days in the period", two sides could differ. They never do in the
-- evidence, so a disagreement is the finding collapsing, not a shape to
-- accommodate: the period is marked not-well-formed and contributes nothing.
-- Picking the longer set would manufacture a normal week and the shorter one
-- an abnormal week, so neither is picked.
--
-- A BYE IS NOT A MISSING SIDE. An absent `home`/`away` key and an explicit
-- JSON null are both "no opponent" (the pure parser's `side is None`), and
-- both engines report exactly that through json_unwrap_text -- a JSON null
-- unwraps to SQL NULL while an object serializes to text. A side that IS
-- present but carries no usable pointsByScoringPeriod is the PARTIAL case and
-- fails the period: that is a period ESPN has not filled in, and inferring
-- its length from the other side would invent evidence.
--
-- KNOWN BOUNDARY, recorded rather than engineered around: a side that is a
-- JSON scalar instead of an object is out of contract on both sides of the
-- capture, and the engines part ways on it -- Snowflake's flatten yields zero
-- keys (so the period reads not-well-formed) while DuckDB's cast to map
-- raises and fails the build. Neither produces a confident result, which is
-- the property that matters; rung 2's capture already refuses a payload whose
-- schedule is not a list, and ESPN has never sent a scalar side.

{{ config(materialized='view') }}

{% set period_id = '[0-9]{1,9}' %}
{% set scoring_period_key = '[0-9]{1,6}' %}

with entries as (
    select
        s.league_key,
        s.season_year,
        s.current_matchup_period,

        -- NULL when the id is unreadable or not 1-based. Such an entry drops
        -- out of every CTE below, and the season model catches the loss by
        -- reconciling the entries it accounted for against
        -- scheduled_matchup_count -- so an unreadable id is malformed
        -- evidence rather than a silently shorter schedule.
        {% set raw_period = json_unwrap_text(json_get('m.value', 'matchupPeriodId')) %}
        case when {{ regexp_like(raw_period, period_id) }}
                  and {{ try_to_number(raw_period) }} >= 1
             then {{ try_to_number(raw_period) }} end::integer as matchup_period,

        {{ json_get('m.value', 'home') }} as home_side,
        {{ json_get('m.value', 'away') }} as away_side,
        -- Participation, per the bye rule above: non-NULL only for a side
        -- that is really there. Doubles as the side's identity for grouping,
        -- which is why it is the serialized document rather than a team id --
        -- no owner, member or franchise identity enters this model.
        {{ json_unwrap_text(json_get('m.value', 'home')) }} as home_text,
        {{ json_unwrap_text(json_get('m.value', 'away')) }} as away_text
    from {{ ref('stg_matchup_schedule') }} s,
        {{ flatten_array('s.schedule', 'm') }}
),

readable as (
    select * from entries
    where matchup_period is not null
      and current_matchup_period is not null
),

periods as (
    select
        league_key,
        season_year,
        current_matchup_period,
        matchup_period,
        matchup_period < current_matchup_period as is_closed,
        count(*) as matchup_count
    from readable
    group by 1, 2, 3, 4, 5
),

-- Only CLOSED periods are shape-checked, matching the pure parser.
sides as (
    select league_key, season_year, matchup_period,
           home_text as side_text, home_side as side
    from readable
    where matchup_period < current_matchup_period and home_text is not null
    union all
    select league_key, season_year, matchup_period,
           away_text as side_text, away_side as side
    from readable
    where matchup_period < current_matchup_period and away_text is not null
),

participating as (
    -- Two sides in one period cannot serialize identically in practice (they
    -- differ by team id), and if they ever did they would carry the same key
    -- set, so collapsing them changes no verdict.
    select league_key, season_year, matchup_period,
           count(distinct side_text) as participating_sides
    from sides
    group by 1, 2, 3
),

side_keys as (
    select
        sd.league_key,
        sd.season_year,
        sd.matchup_period,
        sd.side_text,
        -- JSON object keys are text on the wire and text on both engines, so
        -- this is the one place where SQL and the Python parser see exactly
        -- the same input. ASCII digits only, 1..6 of them: `isdigit()` alone
        -- is True for superscripts (which cannot be parsed) and for full-width
        -- digits (which parse SILENTLY to an ordinary number), and both
        -- engines' bare casts inherit the second bug.
        case when {{ regexp_like(to_varchar('k.key'), scoring_period_key) }}
                  and {{ try_to_number(to_varchar('k.key')) }} >= 1
             then {{ try_to_number(to_varchar('k.key')) }} end::integer
            as scoring_period
    from sides sd,
        {{ flatten_object(json_get('sd.side', 'pointsByScoringPeriod'), 'k') }}
),

side_membership as (
    select
        league_key, season_year, matchup_period, side_text,
        count(*) as key_count,
        count(scoring_period) as valid_key_count,
        {{ listagg_ordered('cast(scoring_period as varchar)', ',', 'scoring_period') }}
            as key_signature
    from side_keys
    group by 1, 2, 3, 4
),

period_membership as (
    select
        league_key, season_year, matchup_period,
        -- Sides that produced ANY key. Compared against participating_sides
        -- below: a side present with no membership never reaches this CTE,
        -- because a flatten over an absent, null or empty object yields zero
        -- rows on both engines. That difference IS the partial-period test.
        count(*) as sides_with_membership,
        count(distinct key_signature) as distinct_signatures,
        sum(key_count - valid_key_count) as invalid_key_count
    from side_membership
    group by 1, 2, 3
),

agreed_keys as (
    -- Taken across all sides at once, which is only sound because the verdict
    -- below requires a single signature; when it does not, the array is
    -- discarded rather than published.
    select league_key, season_year, matchup_period,
           {{ array_agg_ordered('scoring_period', 'scoring_period', distinct=True) }}
               as scoring_periods
    from side_keys
    where scoring_period is not null
    group by 1, 2, 3
),

verdict as (
    select
        p.league_key,
        p.season_year,
        p.current_matchup_period,
        p.matchup_period,
        p.is_closed,
        p.matchup_count,
        coalesce(pt.participating_sides, 0) as participating_sides,
        coalesce(pm.sides_with_membership, 0) as sides_with_membership,
        coalesce(pm.distinct_signatures, 0) as distinct_signatures,
        coalesce(pm.invalid_key_count, 0) as invalid_key_count,
        ak.scoring_periods,
        case when p.is_closed then
            coalesce(pt.participating_sides, 0) >= 1
            and coalesce(pm.sides_with_membership, 0) = coalesce(pt.participating_sides, 0)
            and coalesce(pm.distinct_signatures, 0) = 1
            and coalesce(pm.invalid_key_count, 0) = 0
        end as is_well_formed
    from periods p
    left join participating pt
        on p.league_key = pt.league_key
       and p.season_year = pt.season_year
       and p.matchup_period = pt.matchup_period
    left join period_membership pm
        on p.league_key = pm.league_key
       and p.season_year = pm.season_year
       and p.matchup_period = pm.matchup_period
    left join agreed_keys ak
        on p.league_key = ak.league_key
       and p.season_year = ak.season_year
       and p.matchup_period = ak.matchup_period
)

select
    league_key,
    season_year,
    current_matchup_period,
    matchup_period,
    is_closed,
    is_well_formed,
    matchup_count,
    participating_sides,
    sides_with_membership,
    distinct_signatures,
    invalid_key_count,
    -- Published only for a period whose sides agreed. A count over contested
    -- evidence is a number about the disagreement.
    case when is_well_formed then {{ array_length('scoring_periods') }} end::integer
        as scoring_period_count,
    case when is_well_formed then scoring_periods end as scoring_periods
from verdict
