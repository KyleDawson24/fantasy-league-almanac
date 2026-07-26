-- fct_player_weekly_slot_performance.sql
-- Comprehensive player-weekly performance fact. Aggregates the daily
-- comprehensive fact to weekly grain while preserving lineup_slot in
-- the grain so downstream active/inactive splits can filter cleanly.
--
-- ==========================================================================
-- GRAIN:
--   One row per (league_key, season_year, matchup_period, team_id,
--   player_id, lineup_slot).
-- ==========================================================================
--
-- The _slot_ in the name marks the grain: a player who occupied multiple
-- slots within a single matchup_period produces multiple rows here --
-- one per (player, MP, slot). This is deliberate: the downstream
-- active/inactive facts filter by slot before aggregating the slot
-- dimension away, which is only possible if the slot is preserved here.
-- (The model carried the confusable name fct_weekly_player_performance
-- until the entity-first naming pass added the explicit slot marker.)
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
    -- Weekly facts exist only where the platform defines matchup periods.
    -- CBS union rows (MLB-72) carry matchup_period NULL -- their weekly
    -- rollup is undefined and they'd collapse into one season-wide NULL
    -- pseudo-week per player; period-scoped CBS surfaces read the CBS
    -- staging directly instead. No-op for ESPN rows (always period-stamped).
    where matchup_period is not null
),

weekly as (
    select
        league_key,
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

        -- Catch-all totals (sum across ALL scored stats, even ones not
        -- represented in the wide *_pts columns). Downstream facts use
        -- these for calculated_points so the value is correct regardless
        -- of which stats are explicitly pivoted; per-stat *_pts columns
        -- remain available for "top N contributing stats" callouts.
        {{ stable_sum("total_hitting_stat_pts", none) }}  as total_hitting_stat_pts,
        {{ stable_sum("total_pitching_stat_pts", none) }} as total_pitching_stat_pts,
        {{ stable_sum("total_stat_pts", none) }}          as total_stat_pts,

        -- Negative-production rollup. Per-day platform-level net-negative
        -- magnitude (sum at the daily layer is preserved by simple SUM
        -- here -- magnitude semantics aggregate cleanly across days).
        {{ stable_sum("negative_points", none) }} as negative_points,

        -- platform_points + stat-contribution hitting/pitching split pulled
        -- through so the active fact can read them here without needing
        -- a separate scores-fact join. (Split computed at int_player_daily:
        -- platform_points apportioned by each day's unfiltered per-category
        -- stat production; single-role players land all-or-nothing as before,
        -- two-way days split by what actually earned the points.)
        {{ stable_sum("platform_points", none) }}       as platform_points,
        {{ stable_sum("platform_hitting_pts", none) }}  as platform_hitting_pts,
        {{ stable_sum("platform_pitching_pts", none) }} as platform_pitching_pts,

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
    -- Group by the 10 identifier columns + 2 derived flag columns.
    -- The flag columns are deterministic functions of lineup_slot
    -- (already in the grouping key), so they don't change uniqueness.
    -- Snowflake requires non-aggregated SELECT columns to appear in
    -- GROUP BY regardless.
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
)

select
    weekly.*,
    -- v1.3: CANONICAL owner display, resolved once here so every consumer
    -- reads owner_display directly with no per-query COALESCE (and no query
    -- can regress to the raw name). Nickname > "First Last" via the owner_id
    -- bridge, falling back to the raw box-score owner_name for the defunct
    -- ownerless team (2025 team 7). NULL only for true FA rows (no team,
    -- hence no owner at all).
    coalesce(tod.owner_display, weekly.owner_name) as owner_display
from weekly
left join {{ ref('dim_team_owner') }} tod
    on weekly.league_key = tod.league_key
    and weekly.season_year = tod.season_year
    and weekly.team_id = tod.team_id
