-- fct_team_season_performance.sql
-- Season-grain team performance -- the SHARED, format-modular team-stats
-- spine. One row per (league_key, season_year, team_id): a team's full
-- season stat line, calculated score lenses, and (where the format has them)
-- the official W-L record + authoritative platform total. Season is the atom;
-- "all-time" is a rollup OVER this fact, never baked in -- so the same model
-- serves single-season team stats and all-time franchise records.
--
-- Format-modularity (the design goal, MLB-43): the STAT rollup is
-- format-AGNOSTIC -- it sums the player-active fact, so a non-H2H points
-- league produces team stats the moment its players are attributed to teams,
-- with no matchups in sight. The W-L / opponent / authoritative-platform-total
-- OVERLAY is format-CONDITIONAL: it LEFT JOINs the matchup-gated team-week
-- fact, so those columns are populated where the league delivers matchups
-- (H2H, any platform) and NULL where it doesn't (points, any platform). The
-- toggle is data-presence, never a platform check -- a CBS H2H league would
-- fill W-L; an ESPN points league would leave it blank.
--
-- NULL-safe playoff filter: `not coalesce(is_playoff, false)` keeps the
-- regular season AND survives the unscoped schedule (a no-schedule league's
-- is_playoff resolves NULL; a bare `= false` would silently drop every one of
-- its rows -- the abnormal/playoff-NULL hazard, MLB-5). Abnormal weeks stay
-- in, matching mart_team_season_standings.
--
-- Rounding: calculated_* round ONCE here from the UNROUNDED player totals
-- (sum-of-rounds != round-of-sum -- the same discipline as the weekly facts).
--
-- Today this holds ESPN only -- CBS has no rows in the player-performance DAG
-- until that convergence lands (MLB-61 F1 + MLB-62); the fact is structurally
-- ready and CBS flows in with zero changes here. Verified against
-- mart_team_season_standings: counting stats match exactly, calculated points
-- within accumulated rounding.
--
-- Grain: one row per (league_key, season_year, team_id). Regular season only.
-- Materialization: table (float score sums; feeds records/almanac goldens).

{{ config(materialized='table') }}

with team_stats as (
    -- Format-agnostic: roll the player-active fact up to team-season. No
    -- matchup dependency, so this populates for any format once players
    -- carry a team_id.
    select
        league_key,
        season_year,
        team_id,

        -- Latest-week labels so a mid-season rename can't surface stale.
        max_by(team_name,   matchup_period) as team_name,
        max_by(team_abbrev, matchup_period) as team_abbrev,
        max_by(owner_name,  matchup_period) as owner_name,

        count(distinct matchup_period) as periods_played,

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

        -- Pitching counting
        sum(w)     as w,
        sum(l)     as l,
        sum(k)     as k,
        sum(er)    as er,
        sum(outs)  as outs,
        sum(qs)    as qs,
        sum(sv)    as sv,
        sum(hld)   as hld,
        sum(p_h)   as p_h,
        sum(p_bb)  as p_bb,
        sum(p_hr)  as p_hr,
        sum(p_r)   as p_r,
        sum(cg)    as cg,
        sum(blk)   as blk,
        sum(wp)    as wp,
        sum(hbp_p) as hbp_p,
        sum(blsv)  as blsv,
        sum(nh)    as nh,
        sum(pg)    as pg,
        sum(pk)    as pk,
        sum(sho)   as sho,

        -- Unrounded score totals, to round ONCE below.
        {{ stable_sum("total_hitting_stat_pts", none) }}  as total_hitting_stat_pts,
        {{ stable_sum("total_pitching_stat_pts", none) }} as total_pitching_stat_pts,
        {{ stable_sum("total_stat_pts", none) }}          as total_stat_pts,
        {{ stable_sum("negative_points", none) }}         as negative_points,

        -- Platform hitting/pitching split (player rollups; the wrapper gives
        -- no team-level breakdown). The authoritative platform TOTAL is the
        -- matchup overlay below.
        {{ stable_sum("platform_hitting_pts", none) }}  as platform_hitting_pts,
        {{ stable_sum("platform_pitching_pts", none) }} as platform_pitching_pts

    from {{ ref('fct_player_weekly_active_performance') }}
    where not coalesce(is_playoff, false)
    group by 1, 2, 3
),

matchup_overlay as (
    -- Format-CONDITIONAL: W-L and the authoritative platform total exist only
    -- where the league delivers matchups. Empty for points leagues -> every
    -- column here resolves NULL after the LEFT JOIN.
    select
        league_key,
        season_year,
        team_id,
        sum(case when result = 'W' then 1 else 0 end) as wins,
        sum(case when result = 'L' then 1 else 0 end) as losses,
        sum(case when result = 'T' then 1 else 0 end) as ties,
        -- Exact-decimal summation (MLB-128).
        {{ stable_sum("platform_points") }}           as platform_points,
        {{ stable_sum("opponent_points") }}           as against_platform_points
    from {{ ref('fct_team_weekly_active_performance') }}
    where not coalesce(is_playoff, false)
    group by 1, 2, 3
)

select
    ts.league_key,
    ts.season_year,
    ts.team_id,
    ts.team_name,
    ts.team_abbrev,
    tod.owner_display,
    ts.periods_played,

    -- W-L overlay (NULL for points-format leagues).
    mo.wins,
    mo.losses,
    mo.ties,

    -- Hitting counting
    ts.h, ts.ab, ts.b_bb, ts.b_so, ts.hbp, ts.sf, ts.hr, ts.r, ts.rbi,
    ts.sb, ts.cs, ts.tb, ts.singles, ts.doubles, ts.triples, ts.xbh,
    ts.gdp, ts.b_ibb, ts.cyc,

    -- Pitching counting
    ts.w, ts.l, ts.k, ts.er, ts.outs, ts.qs, ts.sv, ts.hld,
    ts.p_h, ts.p_bb, ts.p_hr, ts.p_r, ts.cg, ts.blk, ts.wp,
    ts.hbp_p, ts.blsv, ts.nh, ts.pg, ts.pk, ts.sho,

    -- Hitting rates (recomputed from season counting sums)
    {{ batting_avg('ts.h', 'ts.ab') }}                            as avg,
    {{ on_base_pct('ts.h', 'ts.b_bb', 'ts.hbp', 'ts.ab', 'ts.sf') }} as obp,
    {{ slugging_pct('ts.tb', 'ts.ab') }}                          as slg,
    {{ ops('ts.h', 'ts.b_bb', 'ts.hbp', 'ts.ab', 'ts.sf', 'ts.tb') }} as ops,

    -- Pitching rates
    {{ era('ts.er', 'ts.outs') }}             as era,
    {{ whip('ts.p_bb', 'ts.p_h', 'ts.outs') }} as whip,
    {{ k_per_9('ts.k', 'ts.outs') }}          as k_per_9,
    {{ k_per_bb('ts.k', 'ts.p_bb') }}         as k_per_bb,

    -- Calculated score lenses, rounded ONCE from the unrounded totals.
    round(ts.total_hitting_stat_pts,  1) as calculated_hitting_pts,
    round(ts.total_pitching_stat_pts, 1) as calculated_pitching_pts,
    round(ts.total_stat_pts,          1) as calculated_points,
    round(ts.negative_points,         1) as negative_points,

    -- Platform lens: the split is a player rollup (always available); the
    -- authoritative TOTAL is the matchup overlay (NULL for points leagues,
    -- whose real platform total lives in the standings feed instead).
    round(ts.platform_hitting_pts,  1) as platform_hitting_pts,
    round(ts.platform_pitching_pts, 1) as platform_pitching_pts,
    mo.platform_points,
    mo.against_platform_points

from team_stats ts
left join matchup_overlay mo
    on ts.league_key = mo.league_key
    and ts.season_year = mo.season_year
    and ts.team_id = mo.team_id
left join {{ ref('dim_team_owner') }} tod
    on ts.league_key = tod.league_key
    and ts.season_year = tod.season_year
    and ts.team_id = tod.team_id
