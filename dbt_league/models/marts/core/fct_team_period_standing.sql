-- fct_team_period_standing.sql
-- "Where did each team stand after period N" -- one fact for both platforms
-- (MLB-263, ledger S-29 / S-35).
--
-- TWO SOURCES, ONE SHAPE. CBS serves standings snapshots per period and
-- mart_period_standings already carries them (source = served). ESPN keeps
-- no intra-season standings snapshot, so the season-points book
-- reconstructed one in Python (espn_points_data.dense_rank_arc): a DENSE
-- team x scoring-day spine, cumulative active production carried forward
-- across quiet days, and a rank taken from the cumulative total on that
-- day. That derivation moves here unchanged (source = derived), so the
-- presenter can read one model whichever platform served the league.
--
-- period_key is the platform's own period ordinal: the scoring_period for
-- ESPN (a day), the served period for CBS. It is NOT a matchup_period and is
-- not comparable across platforms -- it orders a league's own arc.
--
-- The ESPN branch derives for EVERY ESPN league, format regardless: the
-- cumulative-points standing is meaningful under any format, and the reader
-- decides which arc a book shows (the H2H book ranks by wins, elsewhere).
--
-- cume_pts is rounded to one decimal, as the Python query returned it, so
-- the reader re-point is byte-for-byte. Ties rank by team_id, the same
-- deterministic last resort the Python used.
--
-- Materialization: table (window functions over the dense spine).

{{ config(materialized='table') }}

with espn_days as (
    select distinct league_key, season_year, scoring_period
    from {{ ref('fct_player_daily_performance') }}
    where league_key like 'espn%'
),

espn_latest_day as (
    select league_key, season_year, max(scoring_period) as latest_period
    from espn_days
    group by 1, 2
),

espn_teams as (
    select league_key, season_year, cast(team_id as integer) as team_id, team_name
    from {{ ref('fct_team_season_performance') }}
    where league_key like 'espn%'
),

-- 1. THE DENSE SPINE: every team, every scoring day the league captured.
espn_spine as (
    select t.league_key, t.season_year, t.team_id, t.team_name, d.scoring_period
    from espn_teams t
    inner join espn_days d
        on d.league_key = t.league_key
        and d.season_year = t.season_year
),

espn_scored as (
    select
        league_key, season_year, team_id, scoring_period,
        sum(cast(coalesce(total_stat_pts, 0) as decimal(18, 6))) as pts
    from {{ ref('fct_player_daily_performance') }}
    where league_key like 'espn%'
      and team_id is not null
      and is_active_slot
    group by 1, 2, 3, 4
),

-- 2. CARRY FORWARD: the running sum walks the dense spine, so a
--    zero-production day inherits the previous total.
espn_cume as (
    select
        s.league_key,
        s.season_year,
        s.team_id,
        s.team_name,
        s.scoring_period,
        sum(coalesce(x.pts, 0)) over (
            partition by s.league_key, s.season_year, s.team_id
            order by s.scoring_period
            rows between unbounded preceding and current row
        ) as cume_pts
    from espn_spine s
    left join espn_scored x
        on x.league_key = s.league_key
        and x.season_year = s.season_year
        and cast(x.team_id as integer) = s.team_id
        and x.scoring_period = s.scoring_period
),

-- 3. RANK FROM THE CUMULATIVE TOTAL on each day.
espn_derived as (
    select
        c.league_key,
        c.season_year,
        cast(c.scoring_period as integer)               as period_key,
        c.team_id,
        c.team_name,
        round(cast(c.cume_pts as double), 1)            as cume_pts,
        row_number() over (
            partition by c.league_key, c.season_year, c.scoring_period
            order by c.cume_pts desc, c.team_id)        as standings_rank,
        (c.scoring_period = l.latest_period)            as is_latest_period,
        'derived'                                       as source
    from espn_cume c
    inner join espn_latest_day l
        on l.league_key = c.league_key
        and l.season_year = c.season_year
),

cbs_served as (
    select
        league_key,
        season_year,
        cast(period as integer)                          as period_key,
        cast(team_id as integer)                         as team_id,
        team_name,
        round(cast(points as double), 1)                 as cume_pts,
        cast(standings_rank as integer)                  as standings_rank,
        is_latest_period,
        'served'                                         as source
    from {{ ref('mart_period_standings') }}
)

select * from espn_derived
union all
select * from cbs_served
