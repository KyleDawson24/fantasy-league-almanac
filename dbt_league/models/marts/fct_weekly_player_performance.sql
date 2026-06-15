-- fct_weekly_player_performance.sql
-- Comprehensive player-weekly performance fact. Aggregates the daily
-- comprehensive fact to weekly grain while preserving lineup_slot in
-- the grain so downstream active/inactive splits can filter cleanly.
--
-- ==========================================================================
-- GRAIN NOTE (read carefully -- the model name is slightly misleading):
-- ==========================================================================
--   One row per (season_year, matchup_period, team_id, player_id, lineup_slot).
--
-- The name "weekly_player_performance" implies one row per player per
-- week. The actual grain includes lineup_slot, so a player who occupied
-- multiple slots within a single matchup_period produces multiple rows
-- here -- one per (player, MP, slot). This is deliberate: the
-- downstream active/inactive facts filter by slot before aggregating
-- the slot dimension away, which is only possible if the slot is
-- preserved here.
-- ==========================================================================
--
-- v1.1.0 DAG cleanup: this model was previously
-- `int_player_weekly_performance` (intermediate-layer view). Promoted to
-- a mart-layer fact because it represents a legitimate analytical
-- surface in its own right: the comprehensive weekly stat line with
-- fantasy roster context attached, before the active/inactive lens is
-- applied. Other fantasy platforms (ESPN, Yahoo, etc.) surface this
-- shape by default ("here's what Cal Raleigh did this week"); having a
-- consumer-facing contract for it lets future readers query directly
-- without going through the active/inactive split.
--
-- Source: fct_player_daily_performance. Previously sourced directly from
-- int_player_daily; v1.1.0 re-pointed to fct_player_daily so the daily-
-- to-weekly edge in the DAG is real (the fct_player_daily layer is
-- load-bearing, not a dead-end branch).
--
-- performance_status and wasted_bucket are inherited from the daily
-- fact (computed centrally there from lineup_slot). Both are
-- functionally dependent on lineup_slot, so propagating via GROUP BY
-- works without changing the grain.
--
-- Materialization: table. This is the aggregation/cache boundary --
-- two downstream facts (active, inactive) read from it for every
-- mart_stat_leaderboard query. Materializing avoids re-aggregating
-- ~600K daily rows on each consumer query.

{{ config(materialized='table') }}

with daily as (
    select * from {{ ref('fct_player_daily_performance') }}
),

weekly as (
    select
        season_year,
        matchup_period,
        team_id,
        team_name,
        team_abbrev,
        owner_name,
        player_id,
        player_name,
        lineup_slot,

        -- Inherited from fct_player_daily_performance (computed once at
        -- the daily layer from lineup_slot). Functionally dependent on
        -- lineup_slot which is in the grouping key, so adding them to
        -- GROUP BY doesn't change uniqueness.
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
        sum(hbp_p)   as hbp_p,
        sum(blsv)    as blsv,
        sum(nh)      as nh,
        sum(pg)      as pg,
        sum(pk)      as pk,
        sum(sho)     as sho,

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

        -- Catch-all totals (sum across ALL scored stats, even ones not
        -- represented in the wide *_pts columns). Downstream facts use
        -- these for calculated_points so the value is correct regardless
        -- of which stats are explicitly pivoted; per-stat *_pts columns
        -- remain available for "top N contributing stats" callouts.
        sum(total_hitting_stat_pts)  as total_hitting_stat_pts,
        sum(total_pitching_stat_pts) as total_pitching_stat_pts,
        sum(total_stat_pts)          as total_stat_pts,

        -- Negative-production rollup. Per-day platform-level net-negative
        -- magnitude (sum at the daily layer is preserved by simple SUM
        -- here -- magnitude semantics aggregate cleanly across days).
        sum(negative_points) as negative_points,

        -- platform_points + stat-contribution hitting/pitching split pulled
        -- through so the active fact can read them here without needing
        -- a separate scores-fact join. (Split computed at int_player_daily:
        -- platform_points apportioned by each day's unfiltered per-category
        -- stat production; single-role players land all-or-nothing as before,
        -- two-way days split by what actually earned the points.)
        sum(platform_points)       as platform_points,
        sum(platform_hitting_pts)  as platform_hitting_pts,
        sum(platform_pitching_pts) as platform_pitching_pts,

        -- v1.1.1: MLB games played, summed up from int_player_daily
        -- (where DH days carry games_played=2). Natural denominator for
        -- per-game analysis and required by fct_player_season_performance
        -- for the season-grain rollup.
        sum(games_played) as games_played,

        -- display_name is stable per player_id (nickname-resolved at
        -- stg_box_scores). MAX is just to satisfy GROUP BY; same value
        -- in every row of a (player_id) partition.
        max(display_name) as display_name

    from daily
    -- Group by the 9 identifier columns + 2 derived flag columns.
    -- The flag columns are deterministic functions of lineup_slot
    -- (already in the grouping key), so they don't change uniqueness.
    -- Snowflake requires non-aggregated SELECT columns to appear in
    -- GROUP BY regardless.
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
)

select
    weekly.*,
    -- v1.2: owner display name (nickname > "First Last"), resolved off
    -- the owner_id bridge. NULL for FA rows (no team_id to join); the
    -- consumer falls back to owner_name when null.
    tod.owner_display
from weekly
left join {{ ref('int_team_owner_display') }} tod
    on weekly.season_year = tod.season_year
    and weekly.team_id = tod.team_id
