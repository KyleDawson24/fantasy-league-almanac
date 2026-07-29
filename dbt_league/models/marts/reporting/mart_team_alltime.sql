-- mart_team_alltime.sql
-- All-time franchise records: one row per (league_key, team_id), rolling
-- fct_team_season_performance across every season a franchise played. The
-- accumulation axis of the records surface (MLB-69) -- "the Aching Hippos
-- have the most HR in league history".
--
-- RESERVED FOR MLB-69: built ahead of its consumer, so it has NO readers
-- today BY DESIGN. Not dead code -- do not delete on a zero-readers finding
-- alone (see MLB-134). An earlier version of this header claimed to feed the
-- CBS almanac's all-time leaders; that was never true and the claim is what
-- kept drawing deletion proposals.
--
-- Franchise = team_id (stable across seasons and renames; the continuity
-- key). Display name/owner take the LATEST season so a rename surfaces the
-- current identity while the totals span the whole lineage.
--
-- All-time is a plain GROUP BY over the season fact -- season is the atom,
-- this is the rollup. Counting stats sum; rates recompute from the summed
-- counting (never average season rates); W-L sums where the format has it
-- (NULL for points leagues, inherited from the fact). calculated_points sums
-- the already-once-rounded season values -- acceptable at the all-time grain
-- (these are headline totals, not goldens-pinned per-week cells).
--
-- Platform-neutral: any league (ESPN H2H, CBS points, future) rolls up here,
-- filtered by league_key. Holds ESPN until CBS team stats land via the
-- player-performance convergence.
--
-- Materialization: table (float sums; a headline/records surface).

{{ config(materialized='table') }}

with seasons as (
    select * from {{ ref('fct_team_season_performance') }}
),

rolled as (
    select
        league_key,
        team_id,

        -- Current identity (latest season), whole-lineage totals.
        max_by(team_name,    season_year) as team_name,
        max_by(team_abbrev,  season_year) as team_abbrev,
        max_by(owner_display, season_year) as owner_display,

        count(*)          as seasons_played,
        min(season_year)  as first_season,
        max(season_year)  as last_season,

        -- All-time W-L (format-conditional: NULL summands for points leagues
        -- sum to NULL/0 -- guard with a coverage check below so a points
        -- league reads NULL, not a fake 0-0).
        sum(wins)   as wins,
        sum(losses) as losses,
        sum(ties)   as ties,
        count(wins) as seasons_with_wl,

        -- Hitting counting (all-time)
        sum(h) as h, sum(ab) as ab, sum(b_bb) as b_bb, sum(b_so) as b_so,
        sum(hbp) as hbp, sum(sf) as sf, sum(hr) as hr, sum(r) as r,
        sum(rbi) as rbi, sum(sb) as sb, sum(cs) as cs, sum(tb) as tb,
        sum(singles) as singles, sum(doubles) as doubles, sum(triples) as triples,
        sum(xbh) as xbh, sum(gdp) as gdp, sum(b_ibb) as b_ibb, sum(cyc) as cyc,

        -- Pitching counting (all-time)
        sum(w) as w, sum(l) as l, sum(k) as k, sum(er) as er, sum(outs) as outs,
        sum(qs) as qs, sum(sv) as sv, sum(hld) as hld, sum(p_h) as p_h,
        sum(p_bb) as p_bb, sum(p_hr) as p_hr, sum(p_r) as p_r, sum(cg) as cg,
        sum(blk) as blk, sum(wp) as wp, sum(hbp_p) as hbp_p, sum(blsv) as blsv,
        sum(nh) as nh, sum(pg) as pg, sum(pk) as pk, sum(sho) as sho,

        -- Score lenses (all-time). Exact-decimal summation (MLB-128).
        {{ stable_sum("calculated_points") }}       as calculated_points,
        {{ stable_sum("calculated_hitting_pts") }}  as calculated_hitting_pts,
        {{ stable_sum("calculated_pitching_pts") }} as calculated_pitching_pts,

        -- Peak: best single season on the calculated lens, and when.
        round(max(calculated_points), 1)              as best_season_points,
        max_by(season_year, calculated_points)        as best_season_year

    from seasons
    group by 1, 2
)

select
    league_key,
    team_id,
    team_name,
    team_abbrev,
    owner_display,
    seasons_played,
    first_season,
    last_season,

    -- W-L only where the franchise has H2H seasons; NULL for a pure points
    -- franchise (seasons_with_wl = 0) rather than a misleading 0-0-0.
    case when seasons_with_wl > 0 then wins   end as wins,
    case when seasons_with_wl > 0 then losses end as losses,
    case when seasons_with_wl > 0 then ties   end as ties,

    h, ab, b_bb, b_so, hbp, sf, hr, r, rbi, sb, cs, tb,
    singles, doubles, triples, xbh, gdp, b_ibb, cyc,
    w, l, k, er, outs, qs, sv, hld, p_h, p_bb, p_hr, p_r,
    cg, blk, wp, hbp_p, blsv, nh, pg, pk, sho,

    -- All-time rates, recomputed from the summed counting (never averaged).
    {{ batting_avg('h', 'ab') }}                     as avg,
    {{ on_base_pct('h', 'b_bb', 'hbp', 'ab', 'sf') }} as obp,
    {{ slugging_pct('tb', 'ab') }}                   as slg,
    {{ ops('h', 'b_bb', 'hbp', 'ab', 'sf', 'tb') }}  as ops,
    {{ era('er', 'outs') }}                          as era,
    {{ whip('p_bb', 'p_h', 'outs') }}                as whip,
    {{ k_per_9('k', 'outs') }}                       as k_per_9,
    {{ k_per_bb('k', 'p_bb') }}                      as k_per_bb,

    calculated_points,
    calculated_hitting_pts,
    calculated_pitching_pts,
    best_season_points,
    best_season_year

from rolled
