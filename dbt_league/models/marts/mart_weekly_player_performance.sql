-- mart_weekly_player_performance.sql
-- Wide-format consumer mart at player-weekly grain. Pivots the long counting
-- stats from fct_weekly_player_stats into columns and adds derived rate stats
-- via shared rate macros.
--
-- Grain: one row per (season_year, matchup_period, team_id, player_id).
-- Materialized as a view -- always fresh, derived from the long fact, and
-- rate formulas defined in macros compile in at view-definition time.
--
-- Consumer usage:
--   SELECT display_name, hr, rbi, avg, obp, slg
--   FROM mart_weekly_player_performance
--   WHERE season_year = 2026 AND matchup_period = 3
--   ORDER BY ops DESC

{{ config(materialized='view') }}

with pivoted as (
    select
        season_year,
        matchup_period,
        team_id,
        team_name,
        owner_name,
        player_id,
        player_name,

        -- Hitting counting stats
        sum(case when stat_name = 'H'     then stat_value else 0 end) as h,
        sum(case when stat_name = 'AB'    then stat_value else 0 end) as ab,
        sum(case when stat_name = 'B_BB'  then stat_value else 0 end) as b_bb,
        sum(case when stat_name = 'B_SO'  then stat_value else 0 end) as b_so,
        sum(case when stat_name = 'HBP'   then stat_value else 0 end) as hbp,
        sum(case when stat_name = 'SF'    then stat_value else 0 end) as sf,
        sum(case when stat_name = 'HR'    then stat_value else 0 end) as hr,
        sum(case when stat_name = 'R'     then stat_value else 0 end) as r,
        sum(case when stat_name = 'RBI'   then stat_value else 0 end) as rbi,
        sum(case when stat_name = 'SB'    then stat_value else 0 end) as sb,
        sum(case when stat_name = 'CS'    then stat_value else 0 end) as cs,
        sum(case when stat_name = 'TB'    then stat_value else 0 end) as tb,
        sum(case when stat_name = '1B'    then stat_value else 0 end) as singles,
        sum(case when stat_name = '2B'    then stat_value else 0 end) as doubles,
        sum(case when stat_name = '3B'    then stat_value else 0 end) as triples,
        sum(case when stat_name = 'XBH'   then stat_value else 0 end) as xbh,

        -- Pitching counting stats
        sum(case when stat_name = 'W'     then stat_value else 0 end) as w,
        sum(case when stat_name = 'L'     then stat_value else 0 end) as l,
        sum(case when stat_name = 'K'     then stat_value else 0 end) as k,
        sum(case when stat_name = 'ER'    then stat_value else 0 end) as er,
        sum(case when stat_name = 'OUTS'  then stat_value else 0 end) as outs,
        sum(case when stat_name = 'QS'    then stat_value else 0 end) as qs,
        sum(case when stat_name = 'SV'    then stat_value else 0 end) as sv,
        sum(case when stat_name = 'HLD'   then stat_value else 0 end) as hld,
        sum(case when stat_name = 'P_H'   then stat_value else 0 end) as p_h,
        sum(case when stat_name = 'P_BB'  then stat_value else 0 end) as p_bb,
        sum(case when stat_name = 'P_HR'  then stat_value else 0 end) as p_hr,
        sum(case when stat_name = 'P_R'   then stat_value else 0 end) as p_r,
        sum(case when stat_name = 'CG'    then stat_value else 0 end) as cg,
        sum(case when stat_name = 'BLK'   then stat_value else 0 end) as blk,
        sum(case when stat_name = 'WP'    then stat_value else 0 end) as wp

    from {{ ref('fct_weekly_player_stats') }}
    group by 1, 2, 3, 4, 5, 6, 7
)

select
    p.season_year,
    p.matchup_period,
    p.team_id,
    p.team_name,
    p.owner_name,
    p.player_id,
    p.player_name,
    coalesce(n.nickname, p.player_name) as display_name,

    -- Hitting counting
    p.h, p.ab, p.b_bb, p.b_so, p.hbp, p.sf, p.hr, p.r, p.rbi,
    p.sb, p.cs, p.tb, p.singles, p.doubles, p.triples, p.xbh,

    -- Pitching counting
    p.w, p.l, p.k, p.er, p.outs, p.qs, p.sv, p.hld,
    p.p_h, p.p_bb, p.p_hr, p.p_r, p.cg, p.blk, p.wp,

    -- Hitting rates
    {{ batting_avg('p.h', 'p.ab') }}                                  as avg,
    {{ on_base_pct('p.h', 'p.b_bb', 'p.hbp', 'p.ab', 'p.sf') }}       as obp,
    {{ slugging_pct('p.tb', 'p.ab') }}                                as slg,
    {{ ops('p.h', 'p.b_bb', 'p.hbp', 'p.ab', 'p.sf', 'p.tb') }}       as ops,

    -- Pitching rates
    {{ era('p.er', 'p.outs') }}         as era,
    {{ whip('p.p_bb', 'p.p_h', 'p.outs') }}  as whip,
    {{ k_per_9('p.k', 'p.outs') }}      as k_per_9,
    {{ k_per_bb('p.k', 'p.p_bb') }}     as k_per_bb

from pivoted p
left join {{ ref('player_nicknames') }} n
    on p.player_id = n.player_id
