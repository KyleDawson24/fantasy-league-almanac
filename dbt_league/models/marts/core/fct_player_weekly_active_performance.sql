-- fct_player_weekly_active_performance.sql
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
--   1. Read fct_player_weekly_slot_performance (slot-preserved counting + pts +
--      platform totals; v1.1.0 promoted this from the int layer).
--   2. Filter to active rows (performance_status = 'active').
--   3. Aggregate counting and pts columns across slots (collapse slot dim).
--   4. Compute rate stats via macros from the aggregated counting columns.
--   5. Sum *_pts columns into calculated_points / hitting / pitching subtotals.
--   6. LEFT JOIN dim_matchup_period for schedule attributes (is_abnormal /
--      is_playoff / playoff_round denormalized onto the fact for consumer-
--      side filter/label use).
--
-- Grain: one row per (league_key, season_year, matchup_period, team_id, player_id).
--
-- Incremental fact -- merge by unique_key. Re-extracted matchup periods
-- overwrite existing rows. For historical corrections, use --full-refresh.
-- The incremental watermark is per-league (league_period_watermark macro)
-- so one league's progress never gates another's.

{{ config(
    materialized='incremental',
    unique_key=['league_key', 'season_year', 'matchup_period', 'team_id', 'player_id'],
    on_schema_change='fail'
) }}

with active as (
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
        {{ stable_sum("w_pts", none) }}    as w_pts,
        {{ stable_sum("l_pts", none) }}    as l_pts,
        {{ stable_sum("k_pts", none) }}    as k_pts,
        {{ stable_sum("er_pts", none) }}   as er_pts,
        {{ stable_sum("outs_pts", none) }} as outs_pts,
        {{ stable_sum("qs_pts", none) }}   as qs_pts,
        {{ stable_sum("sv_pts", none) }}   as sv_pts,
        {{ stable_sum("hld_pts", none) }}  as hld_pts,
        {{ stable_sum("p_h_pts", none) }}  as p_h_pts,
        {{ stable_sum("p_bb_pts", none) }} as p_bb_pts,
        {{ stable_sum("p_hr_pts", none) }} as p_hr_pts,
        {{ stable_sum("p_r_pts", none) }}  as p_r_pts,
        {{ stable_sum("cg_pts", none) }}   as cg_pts,
        {{ stable_sum("blk_pts", none) }}  as blk_pts,
        {{ stable_sum("wp_pts", none) }}   as wp_pts,
        {{ stable_sum("hbp_p_pts", none) }} as hbp_p_pts,
        {{ stable_sum("blsv_pts", none) }} as blsv_pts,
        {{ stable_sum("nh_pts", none) }}   as nh_pts,
        {{ stable_sum("pg_pts", none) }}   as pg_pts,
        {{ stable_sum("pk_pts", none) }}   as pk_pts,
        {{ stable_sum("sho_pts", none) }}  as sho_pts,

        -- Catch-all totals (sum across ALL scored stats, even ones not
        -- represented in the wide *_pts columns). Used for calculated_points.
        {{ stable_sum("total_hitting_stat_pts", none) }}  as total_hitting_stat_pts,
        {{ stable_sum("total_pitching_stat_pts", none) }} as total_pitching_stat_pts,
        {{ stable_sum("total_stat_pts", none) }}          as total_stat_pts,

        -- Phase 7 Hpre: gross-negative-production rollup. Per-day
        -- magnitude of net-negative platform_points, summed across
        -- active days. The recap's get_wasted_points calc currently
        -- uses week-net (GREATEST(0, -platform_points)) -- this column
        -- is the per-day-summed sibling metric, available for ad-hoc
        -- analysis. NOT in the leaderboard UNPIVOT for v1.0 (not
        -- is_record_candidate in the seed); promotion is a v1.x call.
        {{ stable_sum("negative_points", none) }} as negative_points,

        -- Platform scoring rolled up across the player's active slots
        -- in the matchup. fct_player_weekly_slot_performance carries
        -- SUM(platform_points) plus the stat-contribution hitting/pitching
        -- split (computed at int_player_daily: platform_points apportioned
        -- by each day's unfiltered per-category stat production, so two-way
        -- days split correctly); this fact aggregates those across surviving
        -- slots after the active filter.
        {{ stable_sum("platform_points", none) }}       as platform_points,
        {{ stable_sum("platform_hitting_pts", none) }}  as platform_hitting_pts,
        {{ stable_sum("platform_pitching_pts", none) }} as platform_pitching_pts,

        -- display_name flows through from int_player_daily (nickname-
        -- resolved at stg_box_scores). MAX since it's stable per
        -- player_id (same value across the player's active slots).
        max(display_name)          as display_name

    from {{ ref('fct_player_weekly_slot_performance') }}
    -- Phase 7 D3: filter switched from the slot-enumeration form to the
    -- seed-aligned performance_status flag (D1 added it). team_id IS NOT
    -- NULL is implied by performance_status = 'active' in current data
    -- (every active slot has a team_id; FAs are inactive by definition)
    -- but pinned explicitly as a defensive guardrail and to match the
    -- brief's locked spec for the active fact.
    where performance_status = 'active'
      and team_id is not null
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9
)

select
    a.league_key,
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
    -- Uses the catch-all totals from fct_player_weekly_slot_performance, which sum
    -- stat_points across ALL scored stats. This is correct even for stats that
    -- aren't pivoted into dedicated *_pts columns above (e.g. GDP, B_IBB,
    -- HBP_P, PK, BLSV, NH, PG). The *_pts columns remain available for per-stat
    -- consumer callouts; calculated_* uses the comprehensive totals.
    --
    -- Two forms are emitted:
    --   total_*_stat_pts -- UNROUNDED, so the team fact can sum-then-round
    --     ONCE at team grain. Summing the per-player-rounded calculated_*
    --     instead overshoots ESPN's platform total by the accumulated
    --     rounding (~0.1/team) -- the bug that made a team's calculated_
    --     points read 396.0 where ESPN's authoritative total was 395.86
    --     (-> 395.9). sum-of-rounds != round-of-sum.
    --   calculated_* -- rounded 1 decimal, the player-grain display value
    --     (player records, per-player callouts); per-player rounding also
    --     damps cosmetic float wobble at the player grain.
    a.total_hitting_stat_pts,
    a.total_pitching_stat_pts,
    a.total_stat_pts,
    round(a.total_hitting_stat_pts,  1) as calculated_hitting_pts,
    round(a.total_pitching_stat_pts, 1) as calculated_pitching_pts,
    round(a.total_stat_pts,          1) as calculated_points,

    -- Gross-negative-production rollup (per-day magnitude of net-negative
    -- platform_points, summed across active days). Rounded 1 decimal at
    -- fact layer (see calculated_* note above).
    round(a.negative_points, 1)         as negative_points,

    -- Platform scoring (ESPN's pre-computed values, the official arbiter for W/L).
    -- Sourced from fct_player_weekly_slot_performance via the active CTE above.
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
    -- MLB-235: the non-null gate. LEFT join, so an unanswered period
    -- arrives NULL and must read FALSE -- unknown eligibility is not
    -- ordinary, and leaving it NULL would hand the three-valued problem to
    -- every consumer that reads fact rows directly.
    coalesce(s.is_record_eligible, false) as is_record_eligible,
    s.is_playoff,
    s.playoff_round

from active a
-- MLB-235: dim_matchup_period is league-scoped and platform-derived now.
-- Shape comes from ESPN's own matchup membership, the hand-maintained seed
-- supplies the calendar and a fallback, and its rows are stamped espn-main
-- so they reach no other league -- hence league_key in the join below.
left join {{ ref('dim_matchup_period') }} s
    on a.league_key = s.league_key
    and a.season_year = s.season_year
    and a.matchup_period = s.matchup_period

{{ league_period_watermark('a') }}
