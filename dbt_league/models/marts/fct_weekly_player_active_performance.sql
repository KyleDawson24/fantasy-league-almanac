-- fct_weekly_player_active_performance.sql
-- Wide-format player-weekly convergence fact for ACTIVE-slot performance.
-- The consumer-facing entity for "what did this player do this week" --
-- counting stats, rate stats, per-stat point contributions, and fantasy
-- scoring totals all in a single row per player per matchup.
--
-- Carries:
--   - Per-stat *_pts columns (e.g., hr_pts, k_pts) showing how many fantasy
--     points each counting stat generated under current-season scoring rules.
--     Enables "top N stats by point contribution" callouts.
--   - calculated_points: SUM of all *_pts columns. What this player's stat
--     line would score under current rules. For the current season closely
--     matches platform_points; for historical seasons normalizes to a common
--     scale for cross-season comparison.
--   - calculated_hitting_pts / calculated_pitching_pts: category subtotals.
--   - platform_points / platform_hitting_pts / platform_pitching_pts: the
--     ESPN-computed scores. Official arbiters for W/L. calculated_* sit
--     alongside as the rules-normalized derivation.
--
-- Pipeline:
--   1. Read fct_weekly_player_performance (slot-preserved counting + pts +
--      platform totals; v1.1.0 promoted this from the int layer).
--   2. Filter to active rows (performance_status = 'active').
--   3. Aggregate counting and pts columns across slots (collapse slot dim).
--   4. Compute rate stats via macros from the aggregated counting columns.
--   5. Sum *_pts columns into calculated_points / hitting / pitching subtotals.
--   6. LEFT JOIN dim_matchup_period for schedule attributes (is_abnormal /
--      is_playoff / playoff_round denormalized onto the fact for consumer-
--      side filter/label use).
--
-- Grain: one row per (season_year, matchup_period, team_id, player_id).
--
-- Incremental fact -- merge by unique_key. Re-extracted matchup periods
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
        team_abbrev,
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
        sum(hbp_p)   as hbp_p,
        sum(blsv)    as blsv,
        sum(nh)      as nh,
        sum(pg)      as pg,
        sum(pk)      as pk,
        sum(sho)     as sho,

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
        sum(hbp_p_pts) as hbp_p_pts,
        sum(blsv_pts) as blsv_pts,
        sum(nh_pts)   as nh_pts,
        sum(pg_pts)   as pg_pts,
        sum(pk_pts)   as pk_pts,
        sum(sho_pts)  as sho_pts,

        -- Catch-all totals (sum across ALL scored stats, even ones not
        -- represented in the wide *_pts columns). Used for calculated_points.
        sum(total_hitting_stat_pts)  as total_hitting_stat_pts,
        sum(total_pitching_stat_pts) as total_pitching_stat_pts,
        sum(total_stat_pts)          as total_stat_pts,

        -- Phase 7 Hpre: gross-negative-production rollup. Per-day
        -- magnitude of net-negative platform_points, summed across
        -- active days. The recap's get_wasted_points calc currently
        -- uses week-net (GREATEST(0, -platform_points)) -- this column
        -- is the per-day-summed sibling metric, available for ad-hoc
        -- analysis. NOT in the leaderboard UNPIVOT for v1.0 (not
        -- is_record_candidate in the seed); promotion is a v1.x call.
        sum(negative_points) as negative_points,

        -- Platform scoring rolled up across the player's active slots
        -- in the matchup. fct_weekly_player_performance carries
        -- SUM(platform_points) plus the slot-based hitting/pitching
        -- split (computed at fct_player_daily_performance); this fact aggregates
        -- those across surviving slots after the active filter.
        sum(platform_points)       as platform_points,
        sum(platform_hitting_pts)  as platform_hitting_pts,
        sum(platform_pitching_pts) as platform_pitching_pts,

        -- display_name flows through from int_player_daily (nickname-
        -- resolved at stg_box_scores). MAX since it's stable per
        -- player_id (same value across the player's active slots).
        max(display_name)          as display_name

    from {{ ref('fct_weekly_player_performance') }}
    -- Phase 7 D3: filter switched from the slot-enumeration form to the
    -- seed-aligned performance_status flag (D1 added it). team_id IS NOT
    -- NULL is implied by performance_status = 'active' in current data
    -- (every active slot has a team_id; FAs are inactive by definition)
    -- but pinned explicitly as a defensive guardrail and to match the
    -- brief's locked spec for the active fact.
    where performance_status = 'active'
      and team_id is not null
    group by 1, 2, 3, 4, 5, 6, 7, 8
)

select
    a.season_year,
    a.matchup_period,
    a.team_id,
    a.team_name,
    a.team_abbrev,
    a.owner_name,
    a.player_id,
    a.player_name,
    a.display_name,

    -- Hitting counting
    a.h, a.ab, a.b_bb, a.b_so, a.hbp, a.sf, a.hr, a.r, a.rbi,
    a.sb, a.cs, a.tb, a.singles, a.doubles, a.triples, a.xbh,
    a.gdp, a.b_ibb, a.cyc,

    -- Hitting point contributions
    a.h_pts, a.ab_pts, a.b_bb_pts, a.b_so_pts, a.hbp_pts, a.sf_pts,
    a.hr_pts, a.r_pts, a.rbi_pts, a.sb_pts, a.cs_pts, a.tb_pts,
    a.singles_pts, a.doubles_pts, a.triples_pts, a.xbh_pts,
    a.gdp_pts, a.b_ibb_pts, a.cyc_pts,

    -- Pitching counting
    a.w, a.l, a.k, a.er, a.outs, a.qs, a.sv, a.hld,
    a.p_h, a.p_bb, a.p_hr, a.p_r, a.cg, a.blk, a.wp,
    a.hbp_p, a.blsv, a.nh, a.pg, a.pk, a.sho,

    -- Pitching point contributions
    a.w_pts, a.l_pts, a.k_pts, a.er_pts, a.outs_pts, a.qs_pts,
    a.sv_pts, a.hld_pts, a.p_h_pts, a.p_bb_pts, a.p_hr_pts, a.p_r_pts,
    a.cg_pts, a.blk_pts, a.wp_pts,
    a.hbp_p_pts, a.blsv_pts, a.nh_pts, a.pg_pts, a.pk_pts, a.sho_pts,

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
    -- Uses the catch-all totals from fct_weekly_player_performance, which sum
    -- stat_points across ALL scored stats. This is correct even for stats that
    -- aren't pivoted into dedicated *_pts columns above (e.g. GDP, B_IBB,
    -- HBP_P, PK, BLSV, NH, PG). The *_pts columns remain available for per-stat
    -- consumer callouts; calculated_* uses the comprehensive totals.
    --
    -- v1.x: rounded to 1 decimal at the fact layer to kill the cosmetic
    -- float-precision wobble (e.g. 126.95 -> 126.9 vs 127.0 across
    -- --full-refresh rebuilds). ROUND returns a NUMBER, so when the team
    -- fact does SUM(rounded_player_values) the team total stays exact
    -- and the team-total = SUM(players) invariant is preserved.
    round(a.total_hitting_stat_pts,  1) as calculated_hitting_pts,
    round(a.total_pitching_stat_pts, 1) as calculated_pitching_pts,
    round(a.total_stat_pts,          1) as calculated_points,

    -- Gross-negative-production rollup (per-day magnitude of net-negative
    -- platform_points, summed across active days). Rounded 1 decimal at
    -- fact layer (see calculated_* note above).
    round(a.negative_points, 1)         as negative_points,

    -- Platform scoring (ESPN's pre-computed values, the official arbiter for W/L).
    -- Sourced from fct_weekly_player_performance via the active CTE above.
    -- Rounded 1 decimal at fact layer (see calculated_* note above).
    round(a.platform_points,         1) as platform_points,
    round(a.platform_hitting_pts,    1) as platform_hitting_pts,
    round(a.platform_pitching_pts,   1) as platform_pitching_pts,

    -- v1.1.0 DAG cleanup: schedule attributes denormalized onto the
    -- fact so format_week_label and is_abnormal-filter consumers can
    -- read them directly off rows. dim_matchup_period is the canonical
    -- source. Future: records.load_schedule_lookup() and the Python
    -- schedule_lookup dict can be dropped once all consumers migrate.
    s.is_abnormal,
    s.is_playoff,
    s.playoff_round

from active a
left join {{ ref('dim_matchup_period') }} s
    on a.season_year = s.season_year
    and a.matchup_period = s.matchup_period

{% if is_incremental() %}
where (a.season_year * 100 + a.matchup_period) >= (
    select coalesce(max(season_year * 100 + matchup_period), 0) from {{ this }}
)
{% endif %}
