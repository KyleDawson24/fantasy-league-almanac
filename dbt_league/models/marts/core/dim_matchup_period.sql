-- dim_matchup_period.sql
-- The consumer-facing matchup-period contract (MLB-235 rung 4A).
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, matchup_period).
-- ==========================================================================
--
-- WHAT CHANGED AND WHY. This was a thin view over the hand-maintained
-- matchup_schedule seed, and it carried no league at all -- so espn-main's
-- 2025/2026 calendar joined to ANY league sharing those season and period
-- numbers. Every consumer joined on (season_year, matchup_period) alone. The
-- seed has no league_key column to join on, which is the shape of the bug
-- rather than an oversight: it predates the registry.
--
-- Platform derivation now owns shape. ESPN serves matchup-period membership
-- (MLB-235), so scoring-period count and abnormality come from the payload
-- whenever it produces a verdict, and the seed is demoted to what only it can
-- still answer: the calendar.
--
-- RESOLUTION ORDER, and it is the whole contract:
--
--   explicit override  >  derived verdict  >  espn-main legacy seed  >  unknown
--
-- coalesce() implements it correctly because it keys on NULL rather than on
-- falsehood: an explicit override of FALSE beats a derived TRUE, which is the
-- case the seam exists for.
--
-- THE SEED'S ROWS BELONG TO espn-main. That is an ownership fact, not an
-- inference -- they were hand-maintained for that league before any other
-- existed. They are stamped with it here and reach no other league.
--
-- UNKNOWN IS NOT NORMAL. `is_abnormal` stays NULL when override, derivation
-- and legacy all decline to answer. Nine downstream filters were NULL-unsafe
-- `is_abnormal = false` tests, which silently DROP a NULL row -- so the
-- non-null gate `is_record_eligible` exists for them to read instead. It is
-- true only when abnormality is KNOWN and false.
--
-- is_record_eligible CONCERNS ABNORMALITY ONLY. Playoff status is a separate
-- semantic question and stays a separate predicate: a consumer that excludes
-- playoffs must keep saying so. Folding the two together would make it
-- impossible to ask for a normal playoff week.
--
-- DATES ARE NOW DERIVED TOO (rung 4B-2), and the seed is no longer their
-- only source. ESPN serves no ISO date, but its scoring-period ids are DAILY,
-- so one anchor turns membership into a calendar:
--
--     start_date = season_opener + (min scoring period - 1) days
--     end_date   = season_opener + (max scoring period - 1) days
--
-- The anchor is MLB's own published regular-season start
-- (stg_mlb__season_calendar), not a value anybody typed. Resolution mirrors
-- the abnormality contract -- derived first, legacy seed second, NULL third --
-- and dates stay nullable because "no anchor captured" is a real state that
-- must look like absence rather than a guess.
--
-- DERIVED AND LEGACY MUST NOT DISAGREE, and that is enforced rather than
-- hoped for: schema.yml carries a test that fails the build when a period has
-- both and they differ. On the pioneer league they agree on all 44 closed
-- periods of 2025 and 2026, so this coalesce moves no current byte; the test
-- is what makes a future divergence stop the build instead of silently
-- shifting the calendar.
--
-- ONLY CLOSED H2H PERIODS GET DERIVED DATES. The one narrow exception is an
-- ESPN type-5 season-points container: its purpose is to report the season to
-- date, so its end date deliberately advances with latestScoringPeriod. An
-- in-flight H2H period still keeps the seed's dates if it has them and none
-- otherwise; this exception does not admit it.

{{ config(materialized='view') }}

{% set legacy_league = var('legacy_matchup_schedule_league', 'espn-main') %}

with derived as (
    select
        league_key,
        season_year,
        matchup_period,
        scoring_period_count,
        is_abnormal_derived,
        standard_period_length,
        derivation_status
    from {{ ref('int_matchup_period_shape') }}
),

overrides as (
    -- Sparse by construction: a row here is a decision somebody made, and
    -- ordinary periods have none.
    select
        league_key,
        season_year,
        matchup_period,
        is_abnormal as is_abnormal_override,
        nullif(abnormal_reason, '') as override_reason
    from {{ ref('matchup_period_overrides') }}
),

legacy as (
    select
        '{{ legacy_league }}' as league_key,
        season_year,
        matchup_period,
        start_date,
        end_date,
        is_abnormal as is_abnormal_legacy,
        nullif(abnormal_reason, '') as legacy_reason,
        is_playoff as is_playoff_legacy,
        nullif(playoff_round, '') as playoff_round_legacy
    from {{ ref('matchup_schedule') }}
),

settings as (
    -- The platform's own regular-season boundary (MLB-227). Absent until a
    -- league captures its settings, which is why the legacy flag survives.
    select league_key, season_year, regular_season_periods
    from {{ ref('stg_schedule_settings') }}
),

-- The calendar, derived (rung 4B-2). Joined on season_year ALONE: MLB's
-- regular season starts when it starts, so the anchor is not league-scoped
-- and a second ESPN league in the same warehouse reads the same one rather
-- than needing its own capture.
derived_dates as (
    select
        e.league_key,
        e.season_year,
        e.matchup_period,
        -- WRAPPED IN to_date_of BECAUSE THE ENGINES DISAGREE, and silently.
        -- Snowflake's DATEADD(day, n, <date>) returns a DATE; DuckDB's
        -- `<date> + to_days(n)` returns a TIMESTAMP. Left alone, the derived
        -- half of the coalesce below would be a timestamp on one engine and a
        -- date on the other, while the legacy half is a date on both -- so a
        -- consumer formatting start_date would render midnight on DuckDB and
        -- not on Snowflake. Measured, not theorised: the blank-seed build in
        -- tests/test_derived_calendar_build.py returned datetime objects
        -- before this cast.
        {{ to_date_of(date_add_unit('day', 'e.min_scoring_period - 1',
                                    'c.season_opener')) }}
            as derived_start_date,
        {{ to_date_of(date_add_unit('day', 'e.max_scoring_period - 1',
                                    'c.season_opener')) }}
            as derived_end_date
    from {{ ref('int_matchup_period_evidence') }} e
    join {{ ref('stg_mlb__season_calendar') }} c
        on e.season_year = c.season_year
    where e.min_scoring_period is not null
      and e.max_scoring_period is not null
      and c.season_opener is not null
),

-- Every period any source knows about. An override for a period nothing else
-- carries still produces a row: a decision that silently vanished would be
-- worse than one that looks odd.
universe as (
    select league_key, season_year, matchup_period from derived
    union
    select league_key, season_year, matchup_period from legacy
    union
    select league_key, season_year, matchup_period from overrides
),

resolved as (
    select
        u.league_key,
        u.season_year,
        u.matchup_period,

        -- Derived first, seed second, NULL third. coalesce keys on NULL
        -- rather than falsehood, so a period the platform has not closed
        -- keeps whatever the seed knew rather than being blanked.
        coalesce(dd.derived_start_date, l.start_date) as start_date,
        coalesce(dd.derived_end_date, l.end_date) as end_date,
        dd.derived_start_date,
        dd.derived_end_date,
        l.start_date as legacy_start_date,
        l.end_date as legacy_end_date,

        d.is_abnormal_derived,
        o.is_abnormal_override,
        l.is_abnormal_legacy,
        d.derivation_status,
        d.standard_period_length,

        coalesce(o.is_abnormal_override, d.is_abnormal_derived,
                 l.is_abnormal_legacy) as is_abnormal,

        case
            when o.is_abnormal_override is not null then 'override'
            when d.is_abnormal_derived is not null then 'derived'
            when l.is_abnormal_legacy is not null then 'legacy_seed'
            else 'unknown'
        end as effective_source,

        -- Platform-owned when available; the date arithmetic is the legacy
        -- fallback and nothing else. A league with no dates still gets a
        -- count.
        coalesce(d.scoring_period_count,
                 case when l.start_date is not null and l.end_date is not null
                      then datediff('day', l.start_date, l.end_date) + 1 end)
            as scoring_period_count,

        -- Playoffs: the captured boundary when the league has one, the
        -- legacy flag otherwise. NEVER inferred from an abnormal length --
        -- a long week is a long week, not a playoff.
        coalesce(
            case when s.regular_season_periods is not null
                 then u.matchup_period > s.regular_season_periods end,
            l.is_playoff_legacy) as is_playoff,

        -- An explicit label survives; a generic one is offered only where the
        -- platform boundary is all that exists.
        coalesce(
            l.playoff_round_legacy,
            case when s.regular_season_periods is not null
                      and u.matchup_period > s.regular_season_periods
                 then 'Round ' || {{ to_varchar('u.matchup_period - s.regular_season_periods') }}
            end) as playoff_round,

        o.override_reason,
        l.legacy_reason
    from universe u
    left join derived d
        on u.league_key = d.league_key
       and u.season_year = d.season_year
       and u.matchup_period = d.matchup_period
    left join derived_dates dd
        on u.league_key = dd.league_key
       and u.season_year = dd.season_year
       and u.matchup_period = dd.matchup_period
    left join overrides o
        on u.league_key = o.league_key
       and u.season_year = o.season_year
       and u.matchup_period = o.matchup_period
    left join legacy l
        on u.league_key = l.league_key
       and u.season_year = l.season_year
       and u.matchup_period = l.matchup_period
    left join settings s
        on u.league_key = s.league_key
       and u.season_year = s.season_year
)

select
    league_key,
    season_year,
    matchup_period,

    -- The calendar. Derived from ESPN's daily scoring-period ids anchored to
    -- MLB's published regular-season start, falling back to the seed, and
    -- NULL when neither exists -- never invented.
    start_date,
    end_date,

    -- Both inputs, kept so the standing agreement test can compare them and
    -- so a reader can tell which answered without re-deriving anything.
    derived_start_date,
    derived_end_date,
    legacy_start_date,
    legacy_end_date,
    case
        when derived_start_date is not null then 'derived'
        when legacy_start_date is not null then 'legacy_seed'
        else 'unresolved'
    end as calendar_source,

    is_abnormal,
    -- THE NON-NULL GATE. True only when abnormality is KNOWN and false, so an
    -- unknown period is excluded from records rather than quietly counted as
    -- ordinary. Abnormality only -- playoffs stay a separate predicate.
    coalesce(is_abnormal = false, false) as is_record_eligible,

    -- A reason is carried ONLY when the effective verdict is abnormal -- a
    -- normal row has nothing to explain, and a legacy note is never attached
    -- to a period the platform has since called ordinary.
    --
    -- WITHIN an abnormal verdict the ordering is deliberately NOT the same as
    -- the verdict's own precedence, and that is a decision rather than an
    -- oversight: an override's reason wins, but otherwise the LEGACY note is
    -- preferred over the generated one whenever the legacy seed agrees the
    -- period is abnormal. The two are describing the same fact, and the seed's
    -- text says WHY ("All-Star break (14 days)", "Extended opening week")
    -- where the derivation can only say how long it was. Dropping a
    -- human-written explanation to satisfy a tidier rule would lose the more
    -- informative half of the answer -- and would change rendered output,
    -- which is not something to do for a comment's sake.
    --
    -- The generated fallback speaks only where nothing else can.
    case
        -- `is distinct from true` rather than `is not true`: Snowflake does
        -- not support the IS [NOT] TRUE predicate and rejects it as a SYNTAX
        -- error, so the spelling is a portability question, not a style one.
        -- The truth table is identical -- false and NULL both match, TRUE
        -- does not -- which is what keeps an unknown period unexplained
        -- rather than quietly labelled.
        when is_abnormal is distinct from true then null
        when effective_source = 'override' then override_reason
        when is_abnormal_legacy then legacy_reason
        when standard_period_length is not null then
            'Derived: ' || {{ to_varchar('scoring_period_count') }}
            || ' scoring periods vs standard '
            || {{ to_varchar('standard_period_length') }}
    end as abnormal_reason,

    is_playoff,
    playoff_round,

    -- Provenance, so a reader can tell WHY an answer is what it is without
    -- re-deriving it.
    effective_source,
    derivation_status,
    scoring_period_count,
    -- The season's platform-derived norm, constant across every row of a
    -- league-season and NULL where nothing was derived. Published so
    -- consumers stop recomputing it: mart_team_season_standings used to take
    -- its own mode over periods already filtered by the abnormality flag,
    -- which made the flag an input to the norm meant to explain it. An
    -- override moves eligibility and deliberately does NOT move this -- a
    -- commissioner's ruling about one week is not evidence about the league's
    -- standard week.
    standard_period_length,
    is_abnormal_derived,
    is_abnormal_override
from resolved
