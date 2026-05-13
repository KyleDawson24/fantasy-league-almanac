-- fct_weekly_team_inactive_performance.sql
-- Phase 7 E3: team-grain rollup of inactive (BE/IL/FA) player performance.
-- Symmetric counterpart to fct_weekly_team_active_performance for the
-- inactive half of the player-weekly split.
--
-- Grain: one row per (season_year, matchup_period, team_id, wasted_bucket).
-- Two row flavors per matchup:
--   - 14 ROSTERED_INACTIVE rows (one per team; team_id set)
--   - 1 FA row (league-wide aggregate; team_id NULL). The FA pool is not
--     attributable to a specific team -- mart_wasted_points's convention.
--
-- team_id NULLABLE: the FA bucket carries no team. GROUP BY team_id
-- handles NULL as a single group, so all FA player-weeks roll up into
-- ONE row per (season_year, matchup_period, 'FA') by construction.
--
-- What's carried:
--   - Counting + per-stat *_pts (SUM rollups of the player-level inactive
--     fact)
--   - Catch-all calculated_* totals (unrealized point production at the
--     team / FA-pool level)
--   - wasted_bucket dim
--
-- What's omitted (asymmetries vs the active team fact -- intentional):
--   - platform_points: no wrapper home_score equivalent for "the team's
--     bench." Inactive contributions don't roll up to an ESPN team total.
--   - platform_hitting_pts / platform_pitching_pts: today's player-level
--     hitting/pitching split derives from lineup_slot
--     (SP/RP -> pitching, else -> hitting), which gives bogus results for
--     bench players (a bench pitcher in BE slot would categorize as
--     'hitting' under that rule). A position-driven split would be the
--     right fix and lives in v1.x. v1.0 just skips these columns.
--   - Team rates (era, whip, etc.): bench rate stats aren't meaningfully
--     interpretable (sum of bench IP that didn't get pitched). Skipped.
--   - negative_points: deferred (matches the player-inactive fact's
--     deferral pending int_player_weekly_performance source switch).
--
-- Materialization: incremental. Re-extracting a matchup_period replaces
-- both ROSTERED_INACTIVE and FA rows for that period.

{{ config(
    materialized='incremental',
    unique_key=['season_year', 'matchup_period', 'team_id', 'wasted_bucket'],
    on_schema_change='fail'
) }}

with player_inactive as (
    select * from {{ ref('fct_weekly_player_inactive_performance') }}
),

team_rollup as (
    select
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

        -- Catch-all totals: rules-normalized "unrealized point production"
        -- at the team grain. For ROSTERED_INACTIVE, this is the team's
        -- bench/IL waste under current scoring weights. For FA, the
        -- league-wide free-agent production that no team claimed.
        sum(calculated_hitting_pts)  as calculated_hitting_pts,
        sum(calculated_pitching_pts) as calculated_pitching_pts,
        sum(calculated_points)       as calculated_points,

        count(distinct player_id) as inactive_player_count

    from player_inactive
    group by 1, 2, 3, 4
)

select * from team_rollup

{% if is_incremental() %}
where (season_year * 100 + matchup_period) >= (
    select coalesce(max(season_year * 100 + matchup_period), 0) from {{ this }}
)
{% endif %}
