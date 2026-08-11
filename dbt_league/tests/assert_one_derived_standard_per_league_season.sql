-- The derived standard period length is a property of a LEAGUE-SEASON, not
-- of a matchup period (MLB-235 rung 4A).
--
-- int_matchup_season_standard reduces it with max(), which is only sound
-- because every row of a league-season carries the same value -- the
-- derivation computes one standard per season and denormalizes it. That
-- assumption used to live in a comment next to an unused
-- distinct_derived_standards column, which is a note, not a guard. This is
-- the guard: if two values ever coexist, max() would silently pick one and
-- the standings mart would normalize a whole season against it.
--
-- Returns offending league-seasons; any row fails the build.

select
    league_key,
    season_year,
    count(distinct standard_period_length) as distinct_standards
from {{ ref('dim_matchup_period') }}
where standard_period_length is not null
group by 1, 2
having count(distinct standard_period_length) > 1
