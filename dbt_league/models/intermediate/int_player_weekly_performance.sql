-- int_player_weekly_performance.sql
-- Wide pivot of counting stats and per-stat point contributions from
-- int_player_daily_stats. Renamed from int_player_weekly_stats in Phase 3.2
-- because the table now carries scoring-derived point columns alongside
-- counting stats.
--
-- lineup_slot is preserved as a grain dimension so the fact layer can filter
-- active vs inactive contributions and aggregate the slot dimension away
-- post-filter.
--
-- A player who occupied multiple slots within a matchup_period produces
-- multiple rows here — one per (player, matchup, slot). The fact layer
-- applies slot filter then SUMs across surviving slots.
--
-- Phase 3.2: each counting stat now has a corresponding *_pts column
-- (stat_value * points_per_unit, pre-computed in int_player_daily_stats).
-- These carry through to the performance fact for per-stat point attribution
-- and calculated_points summation.
--
-- Grain: one row per (season_year, matchup_period, team_id, player_id, lineup_slot).
-- Counting columns and their point equivalents at this grain. Rates are
-- computed at the fact layer because meaningful rate values require the slot
-- filter to be applied first (rate over all-slots sums mixes active and
-- bench production).

with daily as (
    select * from {{ ref('int_player_daily_stats') }}
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

        -- Phase 7 D1: additive flag columns derived from lineup_slot. Make
        -- the active/inactive distinction explicit at the int layer so
        -- sub-chunk D2's renamed active fact and the future inactive fact
        -- can both filter cleanly. Row count unchanged — the int already
        -- carries every slot (no filter at this layer); these columns just
        -- annotate what's already there.
        --
        -- performance_status mirrors the filter today's fct_weekly_player_
        -- performance applies at its WHERE clause (lineup_slot NOT IN
        -- ('BE', 'IL', 'FA') -> 'active'). wasted_bucket mirrors the
        -- semantics in mart_wasted_points: 'FA' for free agents, 'ROSTERED_
        -- INACTIVE' for BE/IL, NULL for active slots.
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
        sum(case when stat_name = 'GDP'   then stat_value else 0 end) as gdp,
        sum(case when stat_name = 'B_IBB' then stat_value else 0 end) as b_ibb,

        -- Hitting point contributions
        sum(case when stat_name = 'H'     then stat_points else 0 end) as h_pts,
        sum(case when stat_name = 'AB'    then stat_points else 0 end) as ab_pts,
        sum(case when stat_name = 'B_BB'  then stat_points else 0 end) as b_bb_pts,
        sum(case when stat_name = 'B_SO'  then stat_points else 0 end) as b_so_pts,
        sum(case when stat_name = 'HBP'   then stat_points else 0 end) as hbp_pts,
        sum(case when stat_name = 'SF'    then stat_points else 0 end) as sf_pts,
        sum(case when stat_name = 'HR'    then stat_points else 0 end) as hr_pts,
        sum(case when stat_name = 'R'     then stat_points else 0 end) as r_pts,
        sum(case when stat_name = 'RBI'   then stat_points else 0 end) as rbi_pts,
        sum(case when stat_name = 'SB'    then stat_points else 0 end) as sb_pts,
        sum(case when stat_name = 'CS'    then stat_points else 0 end) as cs_pts,
        sum(case when stat_name = 'TB'    then stat_points else 0 end) as tb_pts,
        sum(case when stat_name = '1B'    then stat_points else 0 end) as singles_pts,
        sum(case when stat_name = '2B'    then stat_points else 0 end) as doubles_pts,
        sum(case when stat_name = '3B'    then stat_points else 0 end) as triples_pts,
        sum(case when stat_name = 'XBH'   then stat_points else 0 end) as xbh_pts,
        sum(case when stat_name = 'GDP'   then stat_points else 0 end) as gdp_pts,
        sum(case when stat_name = 'B_IBB' then stat_points else 0 end) as b_ibb_pts,

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
        sum(case when stat_name = 'WP'    then stat_value else 0 end) as wp,
        sum(case when stat_name = 'HBP_P' then stat_value else 0 end) as hbp_p,
        sum(case when stat_name = 'BLSV'  then stat_value else 0 end) as blsv,
        sum(case when stat_name = 'NH'    then stat_value else 0 end) as nh,
        sum(case when stat_name = 'PG'    then stat_value else 0 end) as pg,
        sum(case when stat_name = 'PK'    then stat_value else 0 end) as pk,
        sum(case when stat_name = '64'    then stat_value else 0 end) as sho,

        -- Pitching point contributions
        sum(case when stat_name = 'W'     then stat_points else 0 end) as w_pts,
        sum(case when stat_name = 'L'     then stat_points else 0 end) as l_pts,
        sum(case when stat_name = 'K'     then stat_points else 0 end) as k_pts,
        sum(case when stat_name = 'ER'    then stat_points else 0 end) as er_pts,
        sum(case when stat_name = 'OUTS'  then stat_points else 0 end) as outs_pts,
        sum(case when stat_name = 'QS'    then stat_points else 0 end) as qs_pts,
        sum(case when stat_name = 'SV'    then stat_points else 0 end) as sv_pts,
        sum(case when stat_name = 'HLD'   then stat_points else 0 end) as hld_pts,
        sum(case when stat_name = 'P_H'   then stat_points else 0 end) as p_h_pts,
        sum(case when stat_name = 'P_BB'  then stat_points else 0 end) as p_bb_pts,
        sum(case when stat_name = 'P_HR'  then stat_points else 0 end) as p_hr_pts,
        sum(case when stat_name = 'P_R'   then stat_points else 0 end) as p_r_pts,
        sum(case when stat_name = 'CG'    then stat_points else 0 end) as cg_pts,
        sum(case when stat_name = 'BLK'   then stat_points else 0 end) as blk_pts,
        sum(case when stat_name = 'WP'    then stat_points else 0 end) as wp_pts,
        sum(case when stat_name = 'HBP_P' then stat_points else 0 end) as hbp_p_pts,
        sum(case when stat_name = 'BLSV'  then stat_points else 0 end) as blsv_pts,
        sum(case when stat_name = 'NH'    then stat_points else 0 end) as nh_pts,
        sum(case when stat_name = 'PG'    then stat_points else 0 end) as pg_pts,
        sum(case when stat_name = 'PK'    then stat_points else 0 end) as pk_pts,
        sum(case when stat_name = '64'    then stat_points else 0 end) as sho_pts,

        -- Catch-all totals: sum stat_points across ALL scored stats. The fact
        -- layer uses these for calculated_points so the value is correct
        -- regardless of which stats are explicitly pivoted. Per-stat *_pts
        -- columns remain available above for "top N contributing stats"
        -- consumer callouts (Phase 6.3.3 expanded the per-stat pivots to cover
        -- GDP, B_IBB, HBP_P, BLSV, NH, PG, PK, SHO; the catch-all approach
        -- keeps calculated_points robust to future seed additions either way).
        sum(case when stat_category = 'hitting'  then stat_points else 0 end) as total_hitting_stat_pts,
        sum(case when stat_category = 'pitching' then stat_points else 0 end) as total_pitching_stat_pts,
        sum(stat_points)                                                       as total_stat_pts

    from daily
    -- Group by the 9 identifier columns + 2 derived flag columns (Phase 7 D1).
    -- The flag columns are deterministic functions of lineup_slot (already in
    -- the grouping key), so they don't change uniqueness, but Snowflake
    -- requires non-aggregated SELECT columns to appear in GROUP BY.
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
)

select * from weekly
