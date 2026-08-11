-- int_matchup_period_shape.sql
-- How long each closed matchup period was, and whether that is abnormal for
-- the season it sits in (MLB-235).
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, matchup_period) that is
-- CLOSED, for every season whose payload could be read at all.
-- ==========================================================================
--
-- THE POINT OF THE TICKET, in one column: is_abnormal_derived is computed
-- from ESPN's own membership, with no reference to the matchup_schedule
-- seed's hand-maintained is_abnormal flag anywhere upstream of it.
--
-- NULL IS NOT FALSE, and the distinction is the whole fail-closed design. A
-- period whose season could not establish a standard length gets NULL --
-- "not known" -- rather than false. Collapsing the two would turn every
-- undetermined period into a normal one, which is precisely how a
-- fail-closed model becomes fail-open. Nine downstream `is_abnormal = false`
-- filter sites are NULL-unsafe today (MLB-222), so this column is not safe
-- to hand them until rung 4 decides the fallback; that is why nothing
-- consumes it yet.
--
-- A MALFORMED SEASON CONTRIBUTES NO ROWS AT ALL, matching the pure parser,
-- which returns an empty `periods` tuple rather than a list of periods it
-- could not vouch for. An INSUFFICIENT or AMBIGUOUS season DOES contribute
-- rows -- their lengths are known facts, only the norm is missing -- with
-- is_abnormal_derived NULL throughout. The status rides on every row so a
-- consumer reading one row never has to join back to learn whether to trust
-- it.

{{ config(materialized='view') }}

select
    e.league_key,
    e.season_year,
    e.matchup_period,
    e.scoring_period_count,
    d.standard_period_length,
    d.derivation_status,
    -- Only a season that established a norm can call a period abnormal.
    case when d.derivation_status = 'derived'
         then e.scoring_period_count <> d.standard_period_length
    end as is_abnormal_derived,
    -- Carried so a reader can tell "this period's own evidence was bad" from
    -- "this season never established a norm" without a second join.
    e.is_well_formed,
    e.participating_sides
from {{ ref('int_matchup_period_evidence') }} e
inner join {{ ref('int_matchup_season_derivation') }} d
    on e.league_key = d.league_key
   and e.season_year = d.season_year
where e.is_closed
  and d.derivation_status <> 'malformed'
