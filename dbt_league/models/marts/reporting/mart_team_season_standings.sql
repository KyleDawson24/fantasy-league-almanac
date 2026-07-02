-- mart_team_season_standings.sql
-- v2.0: season-grain standings surface. One row per (season_year, team_id)
-- with the official W-L-T record, season sums of every scored-stat counting
-- column, the calculated score lenses (offense / pitching / total), points
-- conceded, and the gameplay-day denominators consumers need to normalize
-- totals into per-matchup averages.
--
-- Lifted from the inline query that previously lived in
-- output/almanac_data.py:get_team_standings (the same move that created
-- mart_team_matchup in v1.1.1): standings-shaped aggregation belongs in a
-- dbt contract, not in consumer Python.
--
-- Source: mart_team_matchup, consumed grain-wholesale. The matchup view
-- already carries the opponent's calculated line and the platform-derived
-- result; re-deriving either here would duplicate its self-join logic.
--
-- Regular season only (is_playoff = false): a standings is a regular-season
-- object -- an 11-2 team that loses in Round 1 shouldn't read 11-3, and
-- ESPN's own standings freeze at the end of the regular season. Abnormal
-- weeks (opening week, All-Star break) stay IN: they are real matchups with
-- real W/L outcomes, and the scoring-day denominators below let consumers
-- normalize away their odd lengths.
--
-- Denominators, per the v2.0 Advanced Standings design:
--   * scoring_days_played -- gameplay days, NOT calendar days. Counted as
--     COUNT(DISTINCT scoring_period) per matchup over the daily fact, so a
--     14-calendar-day All-Star week contributes only its ~11 days with MLB
--     games. ESPN emits scoring periods only for days with games, and the
--     daily fact only has rows where stats exist -- either way the break
--     days never enter the count.
--   * standard_matchup_days -- the season's typical matchup length in
--     gameplay days: the modal scoring-day count across normal
--     regular-season weeks (7 here; a league playing 2-week matchups would
--     derive 14 with no code change). "Per-week" normalization is
--     value * standard_matchup_days / scoring_days_played.
--
-- Grain: one row per (season_year, team_id).
--
-- Materialization: table -- score sums are float arithmetic and this mart
-- feeds the almanac byte-diff goldens; a view's per-query re-summation
-- could flip rounding-boundary cells between reads (same rationale as
-- mart_team_matchup).

{{ config(materialized='table') }}

with matchups as (
    select *
    from {{ ref('mart_team_matchup') }}
    where not is_playoff
),

-- Gameplay days per matchup period: days on which any player produced
-- stats. League-wide by construction (no team partition) so every team in
-- a period shares the same denominator.
matchup_scoring_days as (
    select
        season_year,
        matchup_period,
        count(distinct scoring_period) as scoring_days
    from {{ ref('fct_player_daily_performance') }}
    group by 1, 2
),

-- The season's standard matchup length in gameplay days, derived from the
-- normal weeks only (abnormal weeks would skew the mode's neighborhood;
-- playoff weeks are out of scope for a standings entirely).
season_standard_days as (
    select
        sd.season_year,
        mode(sd.scoring_days) as standard_matchup_days
    from matchup_scoring_days sd
    inner join {{ ref('dim_matchup_period') }} mp
        on sd.season_year = mp.season_year
        and sd.matchup_period = mp.matchup_period
    where not mp.is_abnormal
      and not mp.is_playoff
    group by 1
),

team_season as (
    select
        m.season_year,
        m.team_id,

        -- Latest-week labels so a mid-season rename can't surface stale.
        max_by(m.team_name,   m.matchup_period) as team_name,
        max_by(m.team_abbrev, m.matchup_period) as team_abbrev,

        count(*)             as matchup_periods_played,
        sum(sd.scoring_days) as scoring_days_played,

        -- Official platform record (result is W/L/T from ESPN's
        -- authoritative team scores, commissioner adjustments included).
        sum(case when m.result = 'W' then 1 else 0 end) as wins,
        sum(case when m.result = 'L' then 1 else 0 end) as losses,
        sum(case when m.result = 'T' then 1 else 0 end) as ties,

        -- Hitting counting sums
        sum(m.h)       as h,
        sum(m.ab)      as ab,
        sum(m.b_bb)    as b_bb,
        sum(m.b_so)    as b_so,
        sum(m.hbp)     as hbp,
        sum(m.sf)      as sf,
        sum(m.hr)      as hr,
        sum(m.r)       as r,
        sum(m.rbi)     as rbi,
        sum(m.sb)      as sb,
        sum(m.cs)      as cs,
        sum(m.tb)      as tb,
        sum(m.singles) as singles,
        sum(m.doubles) as doubles,
        sum(m.triples) as triples,
        sum(m.xbh)     as xbh,
        sum(m.gdp)     as gdp,
        sum(m.b_ibb)   as b_ibb,
        sum(m.cyc)     as cyc,

        -- Pitching counting sums
        sum(m.w)     as w,
        sum(m.l)     as l,
        sum(m.k)     as k,
        sum(m.er)    as er,
        sum(m.outs)  as outs,
        sum(m.qs)    as qs,
        sum(m.sv)    as sv,
        sum(m.hld)   as hld,
        sum(m.p_h)   as p_h,
        sum(m.p_bb)  as p_bb,
        sum(m.p_hr)  as p_hr,
        sum(m.p_r)   as p_r,
        sum(m.cg)    as cg,
        sum(m.blk)   as blk,
        sum(m.wp)    as wp,
        sum(m.hbp_p) as hbp_p,
        sum(m.blsv)  as blsv,
        sum(m.nh)    as nh,
        sum(m.pg)    as pg,
        sum(m.pk)    as pk,
        sum(m.sho)   as sho,

        -- Fielding counting sums would slot here for leagues that score
        -- fielding categories; this league doesn't, and the wide facts
        -- carry no fielding columns yet.

        -- Calculated score lenses, rounded once at season grain from the
        -- team-week values.
        round(sum(m.calculated_hitting_pts),  1) as calculated_hitting_pts,
        round(sum(m.calculated_pitching_pts), 1) as calculated_pitching_pts,
        round(sum(m.calculated_points),       1) as calculated_points,

        -- Points conceded on the same lens (the opponent's calculated
        -- totals, summed).
        round(sum(m.opponent_calculated_points), 1) as against_calculated_points

    from matchups m
    left join matchup_scoring_days sd
        on m.season_year = sd.season_year
        and m.matchup_period = sd.matchup_period
    group by 1, 2
)

select
    ts.*,
    ssd.standard_matchup_days,
    tod.owner_display
from team_season ts
left join season_standard_days ssd
    on ts.season_year = ssd.season_year
left join {{ ref('dim_team_owner') }} tod
    on ts.season_year = tod.season_year
    and ts.team_id = tod.team_id
