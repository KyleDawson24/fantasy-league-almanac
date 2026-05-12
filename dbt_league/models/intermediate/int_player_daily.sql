-- int_player_daily.sql
-- Phase 7 C: wide daily row per (season, scoring_period, team, player, slot)
-- consolidating today's int_player_daily_stats (long, stat-level points) +
-- int_player_daily_scores (per-player ESPN platform total).
--
-- This is an ADDITIVE new model. The old int_player_daily_stats /
-- int_player_daily_scores stay in place and feed
-- int_player_weekly_performance unchanged until sub-chunk D rewires the
-- weekly model to consume this one. Final consumer cutover happens in
-- sub-chunks G/H.
--
-- Grain: (season_year, scoring_period, team_id, player_id, lineup_slot).
-- matchup_period travels as a derived column (functionally determined by
-- (season_year, scoring_period) via matchup_schedule). Team and owner names
-- are denormalized for downstream convenience.
--
-- Carries:
--   - Identifier 5-tuple + matchup_period
--   - Player metadata: display_name (nickname-resolved), position, pro_team,
--     lineup_slot_category, games_played
--   - platform_points: ESPN's per-player per-day total (from stg_box_scores)
--   - Wide counting stats for the 39 stats that flow through
--     int_player_daily_stats today (post-B1's PA is_counting=false drop)
--   - Per-stat *_pts: stat_value * current-season points_per_unit, computed
--     via the stg_scoring_settings join (Phase 7 follows HANDOFF §7's
--     calculated_* semantic: current weights applied universally including
--     to historical rows -- stg_scoring_settings already surfaces only the
--     current season's settings, so this is automatic)
--   - Catch-all totals: total_hitting_stat_pts, total_pitching_stat_pts,
--     total_stat_pts (sum across ALL scored stats, robust to future seed
--     additions even if the per-stat *_pts columns don't get extended)
--   - is_active_slot: true when lineup_slot_category != 'inactive'.
--     Downstream active/inactive facts apply the inverse filter at their
--     level; this model carries every slot so both can read from one source
--   - negative_points: magnitude (positive) sum of all negative point
--     contributions on this day. Per design table, the per-day generalization
--     of the doubly_wasted_pts concept (which today lives in the recap
--     formatter). Stored as magnitude so the leaderboard's
--     ORDER BY stat_value DESC ranks "most damage" naturally
--
-- The pivot uses seed-side stat_names as CASE keys ('1B'/'2B'/'3B'/'64' for
-- SINGLES/DOUBLES/TRIPLES/SHO) with column aliases as the leaderboard
-- column names -- matching int_player_weekly_performance's existing
-- convention. The seed-to-leaderboard translation lives in stat_catalog.py
-- (for Python consumers) and in this CASE-key/alias pattern (for SQL).
--
-- Materialization: view. Daily layer at our scale (~600K rows) reads fine
-- live; the table cost is at the weekly layer one level up. View at daily,
-- table at weekly is the right split.

{{ config(materialized='view') }}

with daily_long as (
    -- Mechanical replica of int_player_daily_stats's filter chain. INNER
    -- JOIN classification (is_counting=true filter), LEFT JOIN scoring
    -- settings (current-season weights). Slot-stat compatibility filter
    -- kept identical so per-stat hitter-in-pitching-slot mismatches drop
    -- the same way they do today.
    select
        d.season_year,
        d.matchup_period,
        d.scoring_period,
        d.team_id,
        d.team_name,
        d.team_abbrev,
        d.owner_name,
        d.player_id,
        d.player_name,
        d.lineup_slot,
        d.lineup_slot_category,
        d.stat_name,
        c.stat_category,
        d.stat_value,
        coalesce(sc.points_per_unit, 0) as points_per_unit,
        d.stat_value * coalesce(sc.points_per_unit, 0) as stat_points
    from {{ ref('stg_player_stat_breakdowns') }} d
    inner join {{ ref('stat_classification') }} c
        on d.stat_name = c.stat_name
    left join {{ ref('stg_scoring_settings') }} sc
        on d.stat_name = sc.stat_name
    where c.is_counting = true
        {% if var('strict_slot_validity', true) %}
        and (
            c.stat_category = d.lineup_slot_category
            or c.stat_category = 'fielding'
            or d.lineup_slot_category = 'inactive'
        )
        {% endif %}
),

daily_wide as (
    select
        season_year,
        matchup_period,
        scoring_period,
        team_id,
        team_name,
        team_abbrev,
        owner_name,
        player_id,
        player_name,
        lineup_slot,
        lineup_slot_category,

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

        -- Catch-all totals across the seed's scored stats (robust to future
        -- per-stat *_pts additions; same pattern as int_player_weekly_performance)
        sum(case when stat_category = 'hitting'  then stat_points else 0 end) as total_hitting_stat_pts,
        sum(case when stat_category = 'pitching' then stat_points else 0 end) as total_pitching_stat_pts,
        sum(stat_points)                                                       as total_stat_pts
        -- negative_points computed in the `final` CTE below where
        -- platform_points is available via the stg_box_scores join.
    from daily_long
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
),

final as (
    -- Join back to stg_box_scores for per-player-day metadata that doesn't
    -- live on stg_player_stat_breakdowns: display_name (nickname-resolved),
    -- position, pro_team, games_played, and the ESPN platform total
    -- (b.points). LEFT JOIN because daily_wide's grain is a subset of
    -- stg_box_scores (zero-stat players don't survive daily_long's INNER
    -- JOIN to classification on a stat_name they don't emit).
    select
        w.season_year,
        w.matchup_period,
        w.scoring_period,
        w.team_id,
        w.team_name,
        w.team_abbrev,
        w.owner_name,
        w.player_id,
        w.player_name,
        coalesce(b.display_name, w.player_name) as display_name,
        b.position,
        b.pro_team,
        w.lineup_slot,
        w.lineup_slot_category,
        case when w.lineup_slot_category != 'inactive' then true else false end as is_active_slot,
        b.games_played,
        b.points as platform_points,

        -- Wide stats (passthrough)
        w.h, w.ab, w.b_bb, w.b_so, w.hbp, w.sf, w.hr, w.r, w.rbi,
        w.sb, w.cs, w.tb, w.singles, w.doubles, w.triples, w.xbh,
        w.gdp, w.b_ibb,
        w.h_pts, w.ab_pts, w.b_bb_pts, w.b_so_pts, w.hbp_pts, w.sf_pts,
        w.hr_pts, w.r_pts, w.rbi_pts, w.sb_pts, w.cs_pts, w.tb_pts,
        w.singles_pts, w.doubles_pts, w.triples_pts, w.xbh_pts,
        w.gdp_pts, w.b_ibb_pts,
        w.w, w.l, w.k, w.er, w.outs, w.qs, w.sv, w.hld,
        w.p_h, w.p_bb, w.p_hr, w.p_r, w.cg, w.blk, w.wp,
        w.hbp_p, w.blsv, w.nh, w.pg, w.pk, w.sho,
        w.w_pts, w.l_pts, w.k_pts, w.er_pts, w.outs_pts, w.qs_pts,
        w.sv_pts, w.hld_pts, w.p_h_pts, w.p_bb_pts, w.p_hr_pts, w.p_r_pts,
        w.cg_pts, w.blk_pts, w.wp_pts, w.hbp_p_pts, w.blsv_pts,
        w.nh_pts, w.pg_pts, w.pk_pts, w.sho_pts,

        w.total_hitting_stat_pts,
        w.total_pitching_stat_pts,
        w.total_stat_pts,

        -- Per-day net-negative platform_points magnitude. Stored UNSIGNED
        -- (positive) so the leaderboard ranks "most damage" naturally with
        -- ORDER BY DESC. Aggregates via SUM at the weekly layer: for a
        -- player with one -5 day and net-positive week, the weekly rollup
        -- = 5 (the -5 magnitude). Matches the existing doubly_wasted_pts
        -- concept (consumer-side: GREATEST(0, -week_active_platform_pts))
        -- when all the player's negative days fall in active slots, and is
        -- finer-grained otherwise (separately attributes each negative day).
        case when b.points < 0 then -b.points else 0 end as negative_points
    from daily_wide w
    left join {{ ref('stg_box_scores') }} b
        on w.season_year = b.season_year
        and w.scoring_period = b.scoring_period
        and w.team_id is not distinct from b.team_id
        and w.player_id = b.player_id
        and w.lineup_slot = b.lineup_slot
)

select * from final
