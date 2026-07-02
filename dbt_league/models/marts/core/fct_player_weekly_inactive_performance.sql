-- fct_player_weekly_inactive_performance.sql
-- Wide-format player-weekly fact for INACTIVE-slot performance
-- (lineup_slot IN ('BE', 'IL', 'FA')). Mirror of
-- fct_player_weekly_active_performance for the inactive half of the split;
-- together they cover the full universe of player-weeks captured at
-- fct_player_weekly_slot_performance.
--
-- Grain: one row per (season_year, matchup_period, player_id, wasted_bucket).
-- wasted_bucket is in the grain so a player who appeared as both BE/IL on a
-- team AND as FA in the same matchup_period (rare: drop mid-week) produces
-- two rows instead of being collapsed.
--
-- team_id is NULLABLE: NULL for the 'FA' bucket (no fantasy team owns free
-- agents). For 'ROSTERED_INACTIVE', team_id is set to the team that benched
-- the player; MAX() aggregation handles the edge case of a player traded
-- mid-matchup-period within the same inactive bucket.
--
-- What's carried:
--   - Wide counting + per-stat *_pts (same as the active fact)
--   - Catch-all totals: calculated_*. For inactive players, these represent
--     the unrealized point production -- what the player WOULD have scored
--     if started.
--   - Player rate stats (era/whip/k_per_9/k_per_bb): present for symmetry
--     with the active fact and for ad-hoc analysis. NOT surfaced at the
--     mart leaderboard (the mart's player-grain UNPIVOT excludes them per
--     the same noise-floor rationale as the active fact).
--   - wasted_bucket dim
--   - negative_points: per-day magnitude rollup, symmetric with the
--     active fact.
--
-- What's omitted (vs the active fact):
--   - platform_* columns: inactive players don't roll up to a team's
--     platform score (ESPN's home_score includes only active-slot
--     contributions). Per-day platform_points exists on int_player_daily
--     for inactive players, but surfacing it at the weekly fact would
--     suggest a team-rollup equivalence that doesn't hold. If a consumer
--     needs it, derive from int_player_daily directly.

{{ config(
    materialized='incremental',
    unique_key=['season_year', 'matchup_period', 'player_id', 'wasted_bucket'],
    on_schema_change='fail'
) }}

with inactive as (
    select
        season_year,
        matchup_period,
        player_id,
        player_name,
        wasted_bucket,

        -- Carry-through identifiers. MAX picks one value when a player
        -- traded teams within a matchup_period while staying in the same
        -- wasted_bucket (rare but possible). For the FA bucket, all these
        -- are NULL by definition.
        max(team_id)         as team_id,
        max(team_name)       as team_name,
        max(team_abbrev)     as team_abbrev,
        max(owner_name)      as owner_name,

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

        -- Catch-all totals across ALL scored stats. For inactive players,
        -- these are "unrealized point production" -- what the player would
        -- have contributed if started.
        sum(total_hitting_stat_pts)  as total_hitting_stat_pts,
        sum(total_pitching_stat_pts) as total_pitching_stat_pts,
        sum(total_stat_pts)          as total_stat_pts,

        -- Phase 7 Hpre: gross-negative-production rollup (per-day
        -- magnitude of net-negative platform_points, summed across
        -- the player's inactive days in this bucket).
        sum(negative_points)         as negative_points

    from {{ ref('fct_player_weekly_slot_performance') }}
    where performance_status = 'inactive'
    group by 1, 2, 3, 4, 5
)

select
    i.season_year,
    i.matchup_period,
    i.team_id,
    i.team_name,
    i.team_abbrev,
    i.owner_name,
    i.player_id,
    i.player_name,
    i.wasted_bucket,

    -- Hitting counting
    h, ab, b_bb, b_so, hbp, sf, hr, r, rbi,
    sb, cs, tb, singles, doubles, triples, xbh,
    gdp, b_ibb, cyc,

    -- Hitting point contributions
    h_pts, ab_pts, b_bb_pts, b_so_pts, hbp_pts, sf_pts,
    hr_pts, r_pts, rbi_pts, sb_pts, cs_pts, tb_pts,
    singles_pts, doubles_pts, triples_pts, xbh_pts,
    gdp_pts, b_ibb_pts, cyc_pts,

    -- Pitching counting
    w, l, k, er, outs, qs, sv, hld,
    p_h, p_bb, p_hr, p_r, cg, blk, wp,
    hbp_p, blsv, nh, pg, pk, sho,

    -- Pitching point contributions
    w_pts, l_pts, k_pts, er_pts, outs_pts, qs_pts,
    sv_pts, hld_pts, p_h_pts, p_bb_pts, p_hr_pts, p_r_pts,
    cg_pts, blk_pts, wp_pts,
    hbp_p_pts, blsv_pts, nh_pts, pg_pts, pk_pts, sho_pts,

    -- Hitting rates (informational; mart leaderboard does not UNPIVOT
    -- player rates due to single-day noise floor).
    {{ batting_avg('h', 'ab') }}                                  as avg,
    {{ on_base_pct('h', 'b_bb', 'hbp', 'ab', 'sf') }}             as obp,
    {{ slugging_pct('tb', 'ab') }}                                as slg,
    {{ ops('h', 'b_bb', 'hbp', 'ab', 'sf', 'tb') }}               as ops,

    -- Pitching rates (informational; same caveat).
    {{ era('er', 'outs') }}              as era,
    {{ whip('p_bb', 'p_h', 'outs') }}    as whip,
    {{ k_per_9('k', 'outs') }}           as k_per_9,
    {{ k_per_bb('k', 'p_bb') }}          as k_per_bb,

    -- Calculated scoring. For inactive players, this is the "would-have-
    -- scored" line: rules-normalized point production if their stat line
    -- had counted (which it didn't, because they were benched / on IL /
    -- on the waiver wire).
    --
    -- v1.x: rounded to 1 decimal at the fact layer (mirrors the active
    -- player fact). ROUND returns a NUMBER, so when the team inactive
    -- fact does SUM(rounded_player_values) the team total stays exact
    -- and the team-total = SUM(players) invariant is preserved.
    round(total_hitting_stat_pts,  1) as calculated_hitting_pts,
    round(total_pitching_stat_pts, 1) as calculated_pitching_pts,
    round(total_stat_pts,          1) as calculated_points,
    round(negative_points,         1) as negative_points,

    -- v1.1.0: schedule attributes denormalized onto the fact. See
    -- fct_player_weekly_active_performance for the convention rationale.
    s.is_abnormal,
    s.is_playoff,
    s.playoff_round

from inactive i
left join {{ ref('dim_matchup_period') }} s
    on i.season_year = s.season_year
    and i.matchup_period = s.matchup_period

{% if is_incremental() %}
where (i.season_year * 100 + i.matchup_period) >= (
    select coalesce(max(season_year * 100 + matchup_period), 0) from {{ this }}
)
{% endif %}
