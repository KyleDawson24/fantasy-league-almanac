-- fct_player_season_performance.sql
-- v1.1.1: foundational brick of the player profile layer. Season-grain
-- counterpart to fct_weekly_player_performance: collapses matchup_period
-- while preserving the (season, team, player, slot) decomposition.
--
-- ==========================================================================
-- GRAIN:
--   One row per (season_year, team_id, player_id, lineup_slot).
--   team_id is NULLABLE -- NULL rows represent FA time. lineup_slot 'FA'
--   pairs with NULL team_id by construction (FA players have no team).
-- ==========================================================================
--
-- Per BRAINTHOUGHTS [ARCH] "The player profile is an assembly of bricks,
-- not a mega-model": this is brick #1. It stays pure baseball performance
-- (counting stats sliced by team/season/slot, no rates because rates
-- cannot be summed across grains). Future bricks join in independently:
-- dim_player for identity/bio/eligibility, a transactions model for
-- acquisition history (needs a new extract), and a callout-counts model.
-- Do NOT add nullable-until-pipeline-exists columns here.
--
-- Consumer rollups (no extra models needed -- just GROUP BY collapses):
--   - Player career             : GROUP BY player_id
--   - Player career-by-team     : GROUP BY player_id, team_id
--   - Player season             : GROUP BY player_id, season_year
--   - Player season-by-team     : GROUP BY player_id, season_year, team_id
--   - Player season-team-matchup: already served by fct_weekly_player_performance
--
-- Active vs inactive vs total framings -- all consumer-side filters:
--   - Total IRL production      : (no filter)
--   - Production while rostered : team_id IS NOT NULL
--   - Fantasy-credited          : performance_status = 'active'
--   - By fantasy team           : team_id = X (FA naturally absent)
--
-- Slot-bearing all-slots design (mirrors fct_weekly_player_performance,
-- not the active-only spinoff): rows for active slots carry stat
-- contributions credited to the fantasy team; rows for BE/IL/FA carry
-- player MLB production that was NOT credited to a fantasy team that day.
-- performance_status / wasted_bucket inherit the partition cleanly.
--
-- Display fields (team_name/team_abbrev/owner_name/player_name/
-- display_name) are NOT grain keys -- they're helpers, picked as the
-- value from the most recent matchup_period within the (season,
-- entity) partition. Owners and team names can shift mid-season; the
-- latest-in-season convention matches "what should the row label say
-- when displayed."
--
-- Materialization: view. Source is already a table; aggregation is small
-- (collapsing matchup_period out of ~30K-row source); no incremental
-- benefit. Mirrors mart_team_matchup and mart_league_weekly_benchmarks.

{{ config(materialized='view') }}

with weekly as (
    select * from {{ ref('fct_weekly_player_performance') }}
),

season_rollup as (
    select
        season_year,
        team_id,
        player_id,
        lineup_slot,

        -- Partition columns. Both are functionally dependent on
        -- lineup_slot (already in the grouping key) and inherit from the
        -- source fact -- the same propagation pattern fct_weekly_player_
        -- performance itself uses from the daily layer.
        performance_status,
        wasted_bucket,

        -- Hitting counting stats
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
        sum(gdp)     as gdp,
        sum(b_ibb)   as b_ibb,
        sum(cyc)     as cyc,

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
        sum(gdp_pts)     as gdp_pts,
        sum(b_ibb_pts)   as b_ibb_pts,
        sum(cyc_pts)     as cyc_pts,

        -- Pitching counting stats
        sum(w)     as w,
        sum(l)     as l,
        sum(k)     as k,
        sum(er)    as er,
        sum(outs)  as outs,
        sum(qs)    as qs,
        sum(sv)    as sv,
        sum(hld)   as hld,
        sum(p_h)   as p_h,
        sum(p_bb)  as p_bb,
        sum(p_hr)  as p_hr,
        sum(p_r)   as p_r,
        sum(cg)    as cg,
        sum(blk)   as blk,
        sum(wp)    as wp,
        sum(hbp_p) as hbp_p,
        sum(blsv)  as blsv,
        sum(nh)    as nh,
        sum(pg)    as pg,
        sum(pk)    as pk,
        sum(sho)   as sho,

        -- Pitching point contributions
        sum(w_pts)     as w_pts,
        sum(l_pts)     as l_pts,
        sum(k_pts)     as k_pts,
        sum(er_pts)    as er_pts,
        sum(outs_pts)  as outs_pts,
        sum(qs_pts)    as qs_pts,
        sum(sv_pts)    as sv_pts,
        sum(hld_pts)   as hld_pts,
        sum(p_h_pts)   as p_h_pts,
        sum(p_bb_pts)  as p_bb_pts,
        sum(p_hr_pts)  as p_hr_pts,
        sum(p_r_pts)   as p_r_pts,
        sum(cg_pts)    as cg_pts,
        sum(blk_pts)   as blk_pts,
        sum(wp_pts)    as wp_pts,
        sum(hbp_p_pts) as hbp_p_pts,
        sum(blsv_pts)  as blsv_pts,
        sum(nh_pts)    as nh_pts,
        sum(pg_pts)    as pg_pts,
        sum(pk_pts)    as pk_pts,
        sum(sho_pts)   as sho_pts,

        -- Catch-all totals. Aliased to calculated_* to match the naming
        -- the active fact exposes (consumers across the project read
        -- calculated_hitting_pts / calculated_pitching_pts / calculated_
        -- points; this brick uses the same names so no consumer needs to
        -- learn a new vocabulary at season grain).
        sum(total_hitting_stat_pts)  as calculated_hitting_pts,
        sum(total_pitching_stat_pts) as calculated_pitching_pts,
        sum(total_stat_pts)          as calculated_points,

        -- Negative-production rollup. Magnitude sums cleanly across days
        -- and weeks; same semantics as upstream.
        sum(negative_points) as negative_points,

        -- Platform totals. Player-level slot-blind passthroughs of
        -- kona's appliedTotal -- sum-aggregates across the season for
        -- this (team, player, slot) row.
        sum(platform_points)       as platform_points,
        sum(platform_hitting_pts)  as platform_hitting_pts,
        sum(platform_pitching_pts) as platform_pitching_pts,

        -- MLB games played. DH days carry 2 upstream; SUM here gives
        -- the natural per-game denominator at season grain.
        sum(games_played) as games_played

    from weekly
    -- season + grain dims + functionally-dependent partition columns
    group by 1, 2, 3, 4, 5, 6
),

-- Display field convention: latest-in-season per (season, entity_key).
-- Owners + team names can shift mid-season; player names occasionally
-- change too (trades update pro_team; nickname-resolved display_name
-- can shift). "Latest in this season" reads naturally for downstream
-- display ("Team Hybrid as of the end of 2026").
latest_team_labels as (
    select
        season_year,
        team_id,
        team_name,
        team_abbrev,
        owner_name
    from {{ ref('fct_weekly_player_performance') }}
    where team_id is not null
    qualify row_number() over (
        partition by season_year, team_id
        order by matchup_period desc
    ) = 1
),

latest_player_labels as (
    select
        season_year,
        player_id,
        player_name,
        display_name
    from {{ ref('fct_weekly_player_performance') }}
    qualify row_number() over (
        partition by season_year, player_id
        order by matchup_period desc
    ) = 1
)

select
    sr.season_year,
    sr.team_id,
    sr.player_id,
    sr.lineup_slot,
    sr.performance_status,
    sr.wasted_bucket,

    -- Display helpers (not grain keys; latest-in-season per entity).
    tl.team_name,
    tl.team_abbrev,
    tl.owner_name,
    pl.player_name,
    pl.display_name,

    -- Hitting counting stats
    sr.h, sr.ab, sr.b_bb, sr.b_so, sr.hbp, sr.sf, sr.hr, sr.r, sr.rbi,
    sr.sb, sr.cs, sr.tb, sr.singles, sr.doubles, sr.triples, sr.xbh,
    sr.gdp, sr.b_ibb, sr.cyc,

    -- Hitting point contributions. Rounded at the fact layer to kill
    -- cosmetic float-summation-order drift (per v1.0.1 fct_weekly_player_
    -- active_performance precedent): consumers re-summing across slot or
    -- across season would otherwise produce values like 65.94999 vs
    -- 65.95001 that land on opposite sides of the next ROUND boundary.
    round(sr.h_pts,       1) as h_pts,
    round(sr.ab_pts,      1) as ab_pts,
    round(sr.b_bb_pts,    1) as b_bb_pts,
    round(sr.b_so_pts,    1) as b_so_pts,
    round(sr.hbp_pts,     1) as hbp_pts,
    round(sr.sf_pts,      1) as sf_pts,
    round(sr.hr_pts,      1) as hr_pts,
    round(sr.r_pts,       1) as r_pts,
    round(sr.rbi_pts,     1) as rbi_pts,
    round(sr.sb_pts,      1) as sb_pts,
    round(sr.cs_pts,      1) as cs_pts,
    round(sr.tb_pts,      1) as tb_pts,
    round(sr.singles_pts, 1) as singles_pts,
    round(sr.doubles_pts, 1) as doubles_pts,
    round(sr.triples_pts, 1) as triples_pts,
    round(sr.xbh_pts,     1) as xbh_pts,
    round(sr.gdp_pts,     1) as gdp_pts,
    round(sr.b_ibb_pts,   1) as b_ibb_pts,
    round(sr.cyc_pts,     1) as cyc_pts,

    -- Pitching counting stats
    sr.w, sr.l, sr.k, sr.er, sr.outs, sr.qs, sr.sv, sr.hld, sr.p_h,
    sr.p_bb, sr.p_hr, sr.p_r, sr.cg, sr.blk, sr.wp, sr.hbp_p, sr.blsv,
    sr.nh, sr.pg, sr.pk, sr.sho,

    -- Pitching point contributions
    round(sr.w_pts,     1) as w_pts,
    round(sr.l_pts,     1) as l_pts,
    round(sr.k_pts,     1) as k_pts,
    round(sr.er_pts,    1) as er_pts,
    round(sr.outs_pts,  1) as outs_pts,
    round(sr.qs_pts,    1) as qs_pts,
    round(sr.sv_pts,    1) as sv_pts,
    round(sr.hld_pts,   1) as hld_pts,
    round(sr.p_h_pts,   1) as p_h_pts,
    round(sr.p_bb_pts,  1) as p_bb_pts,
    round(sr.p_hr_pts,  1) as p_hr_pts,
    round(sr.p_r_pts,   1) as p_r_pts,
    round(sr.cg_pts,    1) as cg_pts,
    round(sr.blk_pts,   1) as blk_pts,
    round(sr.wp_pts,    1) as wp_pts,
    round(sr.hbp_p_pts, 1) as hbp_p_pts,
    round(sr.blsv_pts,  1) as blsv_pts,
    round(sr.nh_pts,    1) as nh_pts,
    round(sr.pg_pts,    1) as pg_pts,
    round(sr.pk_pts,    1) as pk_pts,
    round(sr.sho_pts,   1) as sho_pts,

    -- Catch-all totals + platform totals + negatives. All rounded for
    -- the same reason as the per-stat _pts above.
    round(sr.calculated_hitting_pts,  1) as calculated_hitting_pts,
    round(sr.calculated_pitching_pts, 1) as calculated_pitching_pts,
    round(sr.calculated_points,       1) as calculated_points,
    round(sr.negative_points,         1) as negative_points,
    round(sr.platform_points,         1) as platform_points,
    round(sr.platform_hitting_pts,    1) as platform_hitting_pts,
    round(sr.platform_pitching_pts,   1) as platform_pitching_pts,
    sr.games_played

from season_rollup sr
left join latest_team_labels tl
    on sr.season_year = tl.season_year
    and sr.team_id    = tl.team_id
left join latest_player_labels pl
    on sr.season_year = pl.season_year
    and sr.player_id  = pl.player_id
