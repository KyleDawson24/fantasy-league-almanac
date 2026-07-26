-- fct_team_weekly_inactive_performance.sql
-- Team-grain rollup of inactive (BE/IL/FA) player performance.
-- Symmetric counterpart to fct_team_weekly_active_performance for the
-- inactive half of the player-weekly split.
--
-- Grain: one row per (league_key, season_year, matchup_period, team_id, wasted_bucket).
-- Two row flavors per matchup:
--   - N ROSTERED_INACTIVE rows (one per fantasy team; team_id set)
--   - 1 FA row (league-wide aggregate; team_id NULL). The FA pool is not
--     attributable to a specific team.
--
-- team_id NULLABLE: the FA bucket carries no team. GROUP BY team_id handles
-- NULL as a single group, so all FA player-weeks roll up into ONE row per
-- (season_year, matchup_period, 'FA') by construction.
--
-- What's carried:
--   - Counting + per-stat *_pts (SUM rollups of the player-level inactive fact)
--   - Catch-all calculated_* totals (unrealized point production at the
--     team / FA-pool level)
--   - wasted_bucket dim
--   - negative_points rollup (symmetric with the active team fact)
--
-- What's omitted (asymmetries vs the active team fact -- intentional):
--   - platform_points: no wrapper home_score equivalent for "the team's
--     bench." Inactive contributions don't roll up to an ESPN team total.
--   - platform_hitting_pts / platform_pitching_pts: today's player-level
--     hitting/pitching split derives from lineup_slot (SP/RP/P -> pitching,
--     else -> hitting), which gives bogus results for bench players (a
--     bench pitcher in BE slot would categorize as 'hitting' under that
--     rule). A position-driven split would be the right fix and lives in
--     v1.x. v1.0 just skips these columns.
--   - Team rates (era, whip, etc.): bench rate stats aren't meaningfully
--     interpretable (sum of bench IP that didn't get pitched). Skipped.
--
-- Materialization: table (not incremental). The unique_key includes
-- team_id which is NULL for FA rows; dbt's incremental MERGE treats
-- NULL != NULL, so re-running on the latest matchup_period would
-- INSERT a second FA row instead of UPDATEing the existing one. At
-- 500-ish rows the full-rebuild cost is trivial.

{{ config(
    materialized='table'
) }}

with player_inactive as (
    select * from {{ ref('fct_player_weekly_inactive_performance') }}
),

team_rollup as (
    select
        league_key,
        season_year,
        matchup_period,
        team_id,
        wasted_bucket,

        -- Team identity for ROSTERED_INACTIVE; NULL for FA pool. MAX is
        -- safe because within a (season, mp, team_id, wasted_bucket)
        -- group every row already shares the same team_id / team_name /
        -- owner_name (the grain forces it).
        max(team_name)   as team_name,
        max(team_abbrev) as team_abbrev,
        max(owner_name)  as owner_name,

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

        -- Catch-all totals: rules-normalized "unrealized point production"
        -- at the team grain. For ROSTERED_INACTIVE, this is the team's
        -- bench/IL waste under current scoring weights. For FA, the
        -- league-wide free-agent production that no team claimed.
        {{ stable_sum("calculated_hitting_pts", none) }}  as calculated_hitting_pts,
        {{ stable_sum("calculated_pitching_pts", none) }} as calculated_pitching_pts,
        {{ stable_sum("calculated_points", none) }}       as calculated_points,

        -- Phase 7 Hpre: gross-negative-production rollup across inactive
        -- players (FA pool aggregate or per-team ROSTERED_INACTIVE).
        {{ stable_sum("negative_points", none) }}         as negative_points,

        count(distinct player_id) as inactive_player_count

    from player_inactive
    group by 1, 2, 3, 4, 5
)

select
    tr.*,
    -- v1.1.0: schedule attributes denormalized onto the fact for
    -- consumer-side filter/label use. See fct_weekly_team_active for
    -- the convention rationale.
    s.is_abnormal,
    s.is_playoff,
    s.playoff_round
from team_rollup tr
left join {{ ref('dim_matchup_period') }} s
    on tr.season_year = s.season_year
    and tr.matchup_period = s.matchup_period
