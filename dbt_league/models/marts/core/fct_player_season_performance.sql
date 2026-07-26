-- fct_player_season_performance.sql
-- v1.1.1: foundational brick of the player profile layer. Season-grain
-- counterpart to fct_player_weekly_slot_performance: collapses matchup_period
-- while preserving the (season, team, player, slot) decomposition.
--
-- ==========================================================================
-- GRAIN:
--   One row per (league_key, season_year, team_id, player_id, lineup_slot).
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
--   - Player season-team-matchup: already served by fct_player_weekly_slot_performance
--
-- Active vs inactive vs total framings -- all consumer-side filters:
--   - Total IRL production      : (no filter)
--   - Production while rostered : team_id IS NOT NULL
--   - Fantasy-credited          : performance_status = 'active'
--   - By fantasy team           : team_id = X (FA naturally absent)
--
-- Slot-bearing all-slots design (mirrors fct_player_weekly_slot_performance,
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
-- Materialization: table. Was a view; every consumer query re-ran the
-- GROUP BY re-summing float points in a nondeterministic order, so a
-- value sitting on a .x5 rounding boundary could flip between two reads
-- minutes apart (observed: an almanac team-tab ERA cell flip-flopping
-- 0.82 -> 0.81 -> 0.82 across consecutive runs with no data change).
-- Materializing freezes each build's values so the almanac byte-diff
-- only moves on real changes -- the same reasoning as
-- fct_player_position_pts. Small table (~30K rows); rebuild cost is
-- negligible.

{{ config(materialized='table') }}

with weekly as (
    select * from {{ ref('fct_player_weekly_slot_performance') }}
),

season_rollup as (
    select
        league_key,
        season_year,
        team_id,
        player_id,
        lineup_slot,

        -- Partition columns. Both are functionally dependent on
        -- lineup_slot (already in the grouping key) and inherit from the
        -- source fact -- the same propagation pattern
        -- fct_player_weekly_slot_performance itself uses from the daily
        -- layer.
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
        {{ stable_sum("h_pts", none) }}       as h_pts,
        {{ stable_sum("ab_pts", none) }}      as ab_pts,
        {{ stable_sum("b_bb_pts", none) }}    as b_bb_pts,
        {{ stable_sum("b_so_pts", none) }}    as b_so_pts,
        {{ stable_sum("hbp_pts", none) }}     as hbp_pts,
        {{ stable_sum("sf_pts", none) }}      as sf_pts,
        {{ stable_sum("hr_pts", none) }}      as hr_pts,
        {{ stable_sum("r_pts", none) }}       as r_pts,
        {{ stable_sum("rbi_pts", none) }}     as rbi_pts,
        {{ stable_sum("sb_pts", none) }}      as sb_pts,
        {{ stable_sum("cs_pts", none) }}      as cs_pts,
        {{ stable_sum("tb_pts", none) }}      as tb_pts,
        {{ stable_sum("singles_pts", none) }} as singles_pts,
        {{ stable_sum("doubles_pts", none) }} as doubles_pts,
        {{ stable_sum("triples_pts", none) }} as triples_pts,
        {{ stable_sum("xbh_pts", none) }}     as xbh_pts,
        {{ stable_sum("gdp_pts", none) }}     as gdp_pts,
        {{ stable_sum("b_ibb_pts", none) }}   as b_ibb_pts,
        {{ stable_sum("cyc_pts", none) }}     as cyc_pts,

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
        {{ stable_sum("w_pts", none) }}     as w_pts,
        {{ stable_sum("l_pts", none) }}     as l_pts,
        {{ stable_sum("k_pts", none) }}     as k_pts,
        {{ stable_sum("er_pts", none) }}    as er_pts,
        {{ stable_sum("outs_pts", none) }}  as outs_pts,
        {{ stable_sum("qs_pts", none) }}    as qs_pts,
        {{ stable_sum("sv_pts", none) }}    as sv_pts,
        {{ stable_sum("hld_pts", none) }}   as hld_pts,
        {{ stable_sum("p_h_pts", none) }}   as p_h_pts,
        {{ stable_sum("p_bb_pts", none) }}  as p_bb_pts,
        {{ stable_sum("p_hr_pts", none) }}  as p_hr_pts,
        {{ stable_sum("p_r_pts", none) }}   as p_r_pts,
        {{ stable_sum("cg_pts", none) }}    as cg_pts,
        {{ stable_sum("blk_pts", none) }}   as blk_pts,
        {{ stable_sum("wp_pts", none) }}    as wp_pts,
        {{ stable_sum("hbp_p_pts", none) }} as hbp_p_pts,
        {{ stable_sum("blsv_pts", none) }}  as blsv_pts,
        {{ stable_sum("nh_pts", none) }}    as nh_pts,
        {{ stable_sum("pg_pts", none) }}    as pg_pts,
        {{ stable_sum("pk_pts", none) }}    as pk_pts,
        {{ stable_sum("sho_pts", none) }}   as sho_pts,

        -- Catch-all totals. Aliased to calculated_* to match the naming
        -- the active fact exposes (consumers across the project read
        -- calculated_hitting_pts / calculated_pitching_pts / calculated_
        -- points; this brick uses the same names so no consumer needs to
        -- learn a new vocabulary at season grain).
        {{ stable_sum("total_hitting_stat_pts", none) }}  as calculated_hitting_pts,
        {{ stable_sum("total_pitching_stat_pts", none) }} as calculated_pitching_pts,
        {{ stable_sum("total_stat_pts", none) }}          as calculated_points,

        -- Negative-production rollup. Magnitude sums cleanly across days
        -- and weeks; same semantics as upstream.
        {{ stable_sum("negative_points", none) }} as negative_points,

        -- Platform totals. Player-level slot-blind passthroughs of
        -- kona's appliedTotal -- sum-aggregates across the season for
        -- this (team, player, slot) row.
        {{ stable_sum("platform_points", none) }}       as platform_points,
        {{ stable_sum("platform_hitting_pts", none) }}  as platform_hitting_pts,
        {{ stable_sum("platform_pitching_pts", none) }} as platform_pitching_pts,

        -- MLB games played. DH days carry 2 upstream; SUM here gives
        -- the natural per-game denominator at season grain.
        sum(games_played) as games_played

    from weekly
    -- league + season + grain dims + functionally-dependent partition columns
    group by 1, 2, 3, 4, 5, 6, 7
),

-- Display field convention: latest-in-season per (season, entity_key).
-- Owners + team names can shift mid-season; player names occasionally
-- change too (trades update pro_team; nickname-resolved display_name
-- can shift). "Latest in this season" reads naturally for downstream
-- display ("Team Hybrid as of the end of 2026").
latest_team_labels as (
    select
        league_key,
        season_year,
        team_id,
        team_name,
        team_abbrev,
        owner_name
    from {{ ref('fct_player_weekly_slot_performance') }}
    where team_id is not null
    qualify row_number() over (
        partition by league_key, season_year, team_id
        order by matchup_period desc
    ) = 1
),

latest_player_labels as (
    select
        league_key,
        season_year,
        player_id,
        player_name,
        display_name
    from {{ ref('fct_player_weekly_slot_performance') }}
    qualify row_number() over (
        partition by league_key, season_year, player_id
        order by matchup_period desc
    ) = 1
)

select
    sr.league_key,
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
    -- cosmetic float-summation-order drift (per the v1.0.1
    -- fct_player_weekly_active_performance precedent): consumers re-summing across slot or
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
    on sr.league_key  = tl.league_key
    and sr.season_year = tl.season_year
    and sr.team_id    = tl.team_id
left join latest_player_labels pl
    on sr.league_key  = pl.league_key
    and sr.season_year = pl.season_year
    and sr.player_id  = pl.player_id
