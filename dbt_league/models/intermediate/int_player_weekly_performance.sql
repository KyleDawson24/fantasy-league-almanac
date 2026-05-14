-- int_player_weekly_performance.sql
-- Wide-format player-weekly rollup. Source: int_player_daily (the wide-daily
-- model). Aggregates daily counting + per-stat point columns + negative_points
-- to the (season, matchup, team, player, slot) grain.
--
-- lineup_slot is preserved as a grain dimension so the fact layer can filter
-- active vs inactive contributions and aggregate the slot dimension away
-- post-filter. A player who occupied multiple slots within a matchup_period
-- produces multiple rows here -- one per (player, matchup, slot). The fact
-- layer applies the slot filter then SUMs across surviving slots.
--
-- Grain: one row per (season_year, matchup_period, team_id, player_id, lineup_slot).
-- Rates are computed at the fact layer because meaningful rate values require
-- the slot filter to be applied first (rate over all-slots sums mixes active
-- and bench production).

with daily as (
    select * from {{ ref('int_player_daily') }}
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

        -- Flag columns derived from lineup_slot. Make the active/inactive
        -- distinction explicit at the int layer so the active and inactive
        -- facts can both filter cleanly. Row count unchanged -- the int
        -- already carries every slot (no filter at this layer); these
        -- columns just annotate what's already there.
        --
        -- performance_status mirrors the WHERE clause on the active fact
        -- (lineup_slot NOT IN ('BE', 'IL', 'FA') -> 'active').
        -- wasted_bucket distinguishes the two flavors of inactive:
        -- 'FA' for free agents, 'ROSTERED_INACTIVE' for BE/IL, NULL for active.
        case
            when lineup_slot in ('BE', 'IL', 'FA') then 'inactive'
            else 'active'
        end as performance_status,
        case
            when lineup_slot = 'FA'          then 'FA'
            when lineup_slot in ('BE', 'IL') then 'ROSTERED_INACTIVE'
            else null
        end as wasted_bucket,

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
        -- represented in the wide *_pts columns). The fact layer uses
        -- these for calculated_points so the value is correct regardless
        -- of which stats are explicitly pivoted; per-stat *_pts columns
        -- remain available for "top N contributing stats" callouts.
        sum(total_hitting_stat_pts)  as total_hitting_stat_pts,
        sum(total_pitching_stat_pts) as total_pitching_stat_pts,
        sum(total_stat_pts)          as total_stat_pts,

        -- Phase 7 Hpre: negative_points rollup. Per-day platform-level
        -- net-negative magnitude (sum at the daily layer is preserved
        -- by simple SUM here -- magnitude semantics aggregate cleanly
        -- across days). Sub-chunk E's facts can now propagate this
        -- without re-deriving from int_player_daily directly.
        sum(negative_points) as negative_points,

        -- platform_points + slot-based hitting/pitching split pulled
        -- through from int_player_daily so the active fact can read them
        -- here without needing a separate scores-fact join.
        sum(platform_points)       as platform_points,
        sum(platform_hitting_pts)  as platform_hitting_pts,
        sum(platform_pitching_pts) as platform_pitching_pts,

        -- display_name is stable per player_id (nickname-resolved at
        -- stg_box_scores). MAX is just to satisfy the GROUP BY; same
        -- value in every row of a (player_id) partition.
        max(display_name) as display_name

    from daily
    -- Group by the 9 identifier columns + 2 derived flag columns.
    -- The flag columns are deterministic functions of lineup_slot
    -- (already in the grouping key), so they don't change uniqueness,
    -- but Snowflake requires non-aggregated SELECT columns to appear
    -- in GROUP BY.
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
)

select * from weekly
