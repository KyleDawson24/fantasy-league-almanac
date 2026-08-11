-- assert_head_to_head_identity_resolves.sql
-- No countable meeting is lost to an unresolvable team-season.
--
-- The ledger joins BOTH sides of every meeting to dim_franchise_season with an
-- inner join. That is the right call -- a team-season the lineage spine cannot
-- speak about should not be guessed at -- but an inner join fails SILENTLY: a
-- franchise missing from the spine does not error, it just quietly stops
-- having played. A rivalry that shortens by three meetings looks exactly like
-- a rivalry that was three meetings long.
--
-- So this reconstructs the model's countable set from the matchup surface and
-- asserts every team-season in it resolves. The predicates below are the
-- model's own, restated -- if they drift apart, this test stops measuring the
-- model, so any change to the meetings CTE belongs here too.
--
-- Emits one row per unresolved (league, season, team), listing the side it
-- failed on, so the fix is a lineage-seed question rather than a hunt.

with meetings as (
    select
        league_key,
        season_year,
        team_id,
        opponent_id
    from {{ ref('mart_team_matchup') }}
    where opponent_id is not null
      and team_id <> opponent_id
      and platform_points is not null
      and opponent_points is not null
      and result is not null
),

sides as (
    select league_key, season_year, team_id as franchise_id, 'row' as side
    from meetings
    union all
    select league_key, season_year, opponent_id as franchise_id, 'opponent' as side
    from meetings
),

distinct_sides as (
    select distinct league_key, season_year, cast(franchise_id as varchar) as franchise_id, side
    from sides
)

select
    s.league_key,
    s.season_year,
    s.franchise_id,
    s.side
from distinct_sides s
left join {{ ref('dim_franchise_season') }} d
    on s.league_key = d.league_key
    and s.franchise_id = d.franchise_id
    and s.season_year = d.season_year
where d.franchise_id is null
