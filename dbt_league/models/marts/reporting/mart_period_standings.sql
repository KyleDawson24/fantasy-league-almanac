-- mart_period_standings.sql
-- Period-grain standings arc for POINTS leagues (format-conditional; the
-- non-H2H counterpart to mart_team_season_standings, which is W-L-derived from
-- matchups). One row per (league_key, season_year, period, team_id): a team's
-- cumulative season-to-date points + rank at the close of each scoring period,
-- plus the derived movement (points earned that period, rank change, distance
-- behind the leader). This is the almanac home tab's "standings arc."
--
-- Source: stg_cbs__standings, consumed directly. There is no intermediate
-- convergence layer here on purpose -- a delivered-standings feed (F7) needs
-- no daily-performance rollup, no matchup pairs, and no scoring recompute; it
-- is authoritative as captured. Platform-neutral by name and shape: any future
-- points league (Fantrax points) reuses it, filtered by league_key.
--
-- Scope note: the CBS API serves current-season standings only, so today this
-- mart holds 2026 in-progress. Historical champions (2001-2025) arrive from
-- the parsed UI standings pages (MLB-53) into the same shape later; until then
-- is_latest_period marks the current leader, NOT a crowned champion (the 2026
-- season is unfinished).
--
-- Materialization: table -- cumulative points are float sums and the window
-- deltas below re-derive from them; a view's per-query re-evaluation could
-- flip rounding-boundary cells between reads (same rationale as the other
-- score-sum marts).

{{ config(materialized='table') }}

with standings as (
    select *
    from {{ ref('stg_cbs__standings') }}
),

max_period as (
    select
        league_key,
        season_year,
        max(period) as latest_period
    from standings
    group by 1, 2
),

with_movement as (
    select
        s.league_key,
        s.season_year,
        s.period,
        s.team_id,
        s.team_name,
        s.division_name,
        s.standings_rank,
        s.points,

        -- Points earned in this period alone = cumulative now minus
        -- cumulative at the previous period (the full cumulative total in
        -- period 1, where there is no prior period).
        s.points - coalesce(
            lag(s.points) over (
                partition by s.league_key, s.season_year, s.team_id
                order by s.period
            ), 0
        ) as period_points,

        -- Rank movement since the previous period (positive = climbed).
        -- NULL in period 1 (no prior rank to move from).
        lag(s.standings_rank) over (
            partition by s.league_key, s.season_year, s.team_id
            order by s.period
        ) - s.standings_rank as rank_change,

        -- Distance behind the period leader, on the cumulative-points scale.
        max(s.points) over (
            partition by s.league_key, s.season_year, s.period
        ) - s.points as points_behind_leader

    from standings s
)

select
    m.league_key,
    m.season_year,
    m.period,
    m.team_id,
    m.team_name,
    m.division_name,
    m.standings_rank,
    m.points,
    m.period_points,
    m.rank_change,
    m.points_behind_leader,
    (m.period = mp.latest_period) as is_latest_period
from with_movement m
inner join max_period mp
    on m.league_key = mp.league_key
    and m.season_year = mp.season_year
order by m.league_key, m.season_year, m.period, m.standings_rank
