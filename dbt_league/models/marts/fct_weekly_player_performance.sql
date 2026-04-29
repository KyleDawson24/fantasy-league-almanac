-- fct_weekly_player_performance.sql
-- Wide-format player-weekly convergence fact. The consumer-facing entity for
-- "what did this player do this week" — counting stats, rate stats,
-- per-stat point contributions, and fantasy scoring totals all in a single
-- row per player per matchup.
--
-- Phase 3.2 additions:
--   - Per-stat *_pts columns (e.g., hr_pts, k_pts) showing how many fantasy
--     points each counting stat generated under the current season's scoring
--     rules. These enable "top N stats by point contribution" callouts.
--   - calculated_points: SUM of all *_pts columns. Represents what this
--     player's stat line would score under current rules. For the current
--     season this should closely match total_points; for historical
--     seasons it normalizes to a common scale for cross-season comparison.
--   - calculated_hitting_pts / calculated_pitching_pts: category subtotals.
--
-- total_points / hitting_points / pitching_points retain their original
-- names (the platform-computed scores). They remain the official arbiters
-- for W/L. calculated_* sit alongside as the rules-normalized derivation.
--
-- Pipeline:
--   1. Read int_player_weekly_performance (slot-preserved counting + pts columns)
--   2. Filter to active slots (lineup_slot NOT IN ('BE', 'IL', 'FA'))
--   3. Aggregate counting and pts columns across slots (collapse slot dimension)
--   4. Compute rate stats via macros from the aggregated counting columns
--   5. Sum *_pts columns into calculated_points / hitting / pitching subtotals
--   6. Join fct_weekly_player_scores for platform total_points / hitting / pitching
--
-- Grain: one row per (season_year, matchup_period, team_id, player_id).
--
-- Incremental fact — merge by unique_key. Re-extracted matchup periods
-- overwrite existing rows. For historical corrections, use --full-refresh.

{{ config(
    materialized='incremental',
    unique_key=['season_year', 'matchup_period', 'team_id', 'player_id'],
    on_schema_change='fail'
) }}

with active as (
    select
        season_year,
        matchup_period,
        team_id,
        team_name,
        owner_name,
        player_id,
        player_name,

        -- Hitting counting
        sum(h)       as h,
        sum(ab)      as ab,
        sum(b_bb)    as b_bb,
        sum(b_so)    as b_so,
        sum(hbp)     as hbp,
        sum(sf)      as sf,
        sum(hr)      as hr,
        sum(r)       as r,
        sum(rbi)     as rbi,
        sum(sb)      as sb,
        sum(cs)      as cs,
        sum(tb)      as tb,
        sum(singles) as singles,
        sum(doubles) as doubles,
        sum(triples) as triples,
        sum(xbh)     as xbh,

        -- Hitting point contributions
        sum(h_pts)       as h_pts,
        sum(ab_pts)      as ab_pts,
        sum(b_bb_pts)    as b_bb_pts,
        sum(b_so_pts)    as b_so_pts,
        sum(hbp_pts)     as hbp_pts,
        sum(sf_pts)      as sf_pts,
        sum(hr_pts)      as hr_pts,
        sum(r_pts)       as r_pts,
        sum(rbi_pts)     as rbi_pts,
        sum(sb_pts)      as sb_pts,
        sum(cs_pts)      as cs_pts,
        sum(tb_pts)      as tb_pts,
        sum(singles_pts) as singles_pts,
        sum(doubles_pts) as doubles_pts,
        sum(triples_pts) as triples_pts,
        sum(xbh_pts)     as xbh_pts,

        -- Pitching counting
        sum(w)       as w,
        sum(l)       as l,
        sum(k)       as k,
        sum(er)      as er,
        sum(outs)    as outs,
        sum(qs)      as qs,
        sum(sv)      as sv,
        sum(hld)     as hld,
        sum(p_h)     as p_h,
        sum(p_bb)    as p_bb,
        sum(p_hr)    as p_hr,
        sum(p_r)     as p_r,
        sum(cg)      as cg,
        sum(blk)     as blk,
        sum(wp)      as wp,

        -- Pitching point contributions
        sum(w_pts)    as w_pts,
        sum(l_pts)    as l_pts,
        sum(k_pts)    as k_pts,
        sum(er_pts)   as er_pts,
        sum(outs_pts) as outs_pts,
        sum(qs_pts)   as qs_pts,
        sum(sv_pts)   as sv_pts,
        sum(hld_pts)  as hld_pts,
        sum(p_h_pts)  as p_h_pts,
        sum(p_bb_pts) as p_bb_pts,
        sum(p_hr_pts) as p_hr_pts,
        sum(p_r_pts)  as p_r_pts,
        sum(cg_pts)   as cg_pts,
        sum(blk_pts)  as blk_pts,
        sum(wp_pts)   as wp_pts,

        -- Catch-all totals (sum across ALL scored stats, even ones not
        -- represented in the wide *_pts columns). Used for calculated_points.
        sum(total_hitting_stat_pts)  as total_hitting_stat_pts,
        sum(total_pitching_stat_pts) as total_pitching_stat_pts,
        sum(total_stat_pts)          as total_stat_pts

    from {{ ref('int_player_weekly_performance') }}
    where lineup_slot not in ('BE', 'IL', 'FA')
    group by 1, 2, 3, 4, 5, 6, 7
),

scores as (
    select
        season_year,
        matchup_period,
        team_id,
        player_id,
        display_name,
        total_points,
        hitting_points,
        pitching_points
    from {{ ref('fct_weekly_player_scores') }}
)

select
    a.season_year,
    a.matchup_period,
    a.team_id,
    a.team_name,
    a.owner_name,
    a.player_id,
    a.player_name,
    coalesce(s.display_name, a.player_name) as display_name,

    -- Hitting counting
    a.h, a.ab, a.b_bb, a.b_so, a.hbp, a.sf, a.hr, a.r, a.rbi,
    a.sb, a.cs, a.tb, a.singles, a.doubles, a.triples, a.xbh,

    -- Hitting point contributions
    a.h_pts, a.ab_pts, a.b_bb_pts, a.b_so_pts, a.hbp_pts, a.sf_pts,
    a.hr_pts, a.r_pts, a.rbi_pts, a.sb_pts, a.cs_pts, a.tb_pts,
    a.singles_pts, a.doubles_pts, a.triples_pts, a.xbh_pts,

    -- Pitching counting
    a.w, a.l, a.k, a.er, a.outs, a.qs, a.sv, a.hld,
    a.p_h, a.p_bb, a.p_hr, a.p_r, a.cg, a.blk, a.wp,

    -- Pitching point contributions
    a.w_pts, a.l_pts, a.k_pts, a.er_pts, a.outs_pts, a.qs_pts,
    a.sv_pts, a.hld_pts, a.p_h_pts, a.p_bb_pts, a.p_hr_pts, a.p_r_pts,
    a.cg_pts, a.blk_pts, a.wp_pts,

    -- Hitting rates
    {{ batting_avg('a.h', 'a.ab') }}                                  as avg,
    {{ on_base_pct('a.h', 'a.b_bb', 'a.hbp', 'a.ab', 'a.sf') }}       as obp,
    {{ slugging_pct('a.tb', 'a.ab') }}                                as slg,
    {{ ops('a.h', 'a.b_bb', 'a.hbp', 'a.ab', 'a.sf', 'a.tb') }}       as ops,

    -- Pitching rates
    {{ era('a.er', 'a.outs') }}              as era,
    {{ whip('a.p_bb', 'a.p_h', 'a.outs') }}  as whip,
    {{ k_per_9('a.k', 'a.outs') }}           as k_per_9,
    {{ k_per_bb('a.k', 'a.p_bb') }}          as k_per_bb,

    -- Calculated scoring (current season's weights applied to stat breakdowns).
    -- Uses the catch-all totals from int_player_weekly_performance, which sum
    -- stat_points across ALL scored stats. This is correct even for stats that
    -- aren't pivoted into dedicated *_pts columns above (e.g. GDP, B_IBB,
    -- HBP_P, PK, BLSV, NH, PG). The *_pts columns remain available for per-stat
    -- consumer callouts; calculated_* uses the comprehensive totals.
    a.total_hitting_stat_pts  as calculated_hitting_pts,
    a.total_pitching_stat_pts as calculated_pitching_pts,
    a.total_stat_pts          as calculated_points,

    -- Platform scoring (ESPN's pre-computed values, the official arbiter for W/L).
    -- Renamed from total_points/hitting_points/pitching_points in Phase 3.2 to
    -- explicitly distinguish from calculated_* (the rules-normalized derivation).
    s.total_points      as platform_points,
    s.hitting_points    as platform_hitting_pts,
    s.pitching_points   as platform_pitching_pts

from active a
left join scores s
    on a.season_year = s.season_year
    and a.matchup_period = s.matchup_period
    and a.team_id = s.team_id
    and a.player_id = s.player_id

{% if is_incremental() %}
where (a.season_year * 100 + a.matchup_period) >= (
    select coalesce(max(season_year * 100 + matchup_period), 0) from {{ this }}
)
{% endif %}
