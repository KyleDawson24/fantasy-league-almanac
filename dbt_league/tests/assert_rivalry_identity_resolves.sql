-- assert_rivalry_identity_resolves.sql
-- No countable result is lost to a team-season the identity dim cannot speak
-- about.
--
-- Both ledgers join their sources to dim_franchise_identity with INNER joins.
-- That is the right call -- a team-season with no identity should not be
-- guessed at -- but an inner join fails SILENTLY: a franchise missing from the
-- dim does not error, it just quietly stops having played. A rivalry that
-- shortens by three meetings looks exactly like a rivalry that was three
-- meetings long, and a season-points record that loses a year looks like a
-- year nobody played.
--
-- So this reconstructs both countable sets from their sources and asserts
-- every team-season in them resolves. The predicates below are the model's
-- own, restated -- if they drift apart this stops measuring the model, so a
-- change to the meetings or season_points CTEs belongs here too.
--
-- Emits one row per unresolved (league, season, franchise) with the ledger and
-- side it failed on, so the fix is a lineage-seed question rather than a hunt.

with period_evidence as (
    select league_key, season_year, matchup_period, is_closed
    from {{ ref('int_matchup_period_evidence') }}
),

captured_seasons as (
    select distinct league_key, season_year from period_evidence
),

meetings as (
    select
        m.league_key,
        m.season_year,
        m.team_id,
        m.opponent_id
    from {{ ref('mart_team_matchup') }} m
    left join captured_seasons cs
        on m.league_key = cs.league_key
        and m.season_year = cs.season_year
    left join period_evidence pe
        on m.league_key = pe.league_key
        and m.season_year = pe.season_year
        and m.matchup_period = pe.matchup_period
    where m.opponent_id is not null
      and m.team_id <> m.opponent_id
      and m.platform_points is not null
      and m.opponent_points is not null
      and m.result is not null
      and (cs.league_key is null or coalesce(pe.is_closed, false))
),

sides as (
    select league_key, season_year, cast(team_id as varchar) as franchise_id,
           'matchup' as ledger, 'row' as side
    from meetings
    union all
    select league_key, season_year, cast(opponent_id as varchar) as franchise_id,
           'matchup' as ledger, 'opponent' as side
    from meetings
    union all
    select league_key, season_year, franchise_id,
           'season_points' as ledger, 'row' as side
    from {{ ref('int_franchise_season_points') }}
    where is_season_complete
),

distinct_sides as (
    select distinct league_key, season_year, franchise_id, ledger, side
    from sides
)

select
    s.league_key,
    s.season_year,
    s.franchise_id,
    s.ledger,
    s.side
from distinct_sides s
left join {{ ref('dim_franchise_identity') }} d
    on s.league_key = d.league_key
    and s.franchise_id = d.franchise_id
    and s.season_year = d.season_year
where d.franchise_id is null
