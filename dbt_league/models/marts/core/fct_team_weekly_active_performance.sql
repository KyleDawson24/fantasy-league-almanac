-- fct_team_weekly_active_performance.sql
-- Phase 7 E2: renamed from fct_weekly_team_performance. The active half
-- of the team-weekly active/inactive symmetry. A thin compatibility view
-- at the old name (fct_weekly_team_performance.sql) lives alongside so
-- Python consumers continue to resolve the old name until G rewires them;
-- H drops the compat view as part of dead-model cleanup.
--
-- Wide-format team-weekly convergence fact. Absorbs everything the old
-- fct_weekly_team_scores carried (scoring totals, opponent context, W/L)
-- and adds counting + rate stats.
--
-- Phase 3.2 additions: rolls up the per-stat *_pts columns and the
-- calculated_* totals from fct_player_weekly_active_performance, so team-
-- level consumers can ask the same "top N stats by point contribution" and
-- "what would this team have scored under current rules" questions that
-- the player fact supports.
--
-- Phase 4 change to platform_points: now read directly from the wrapper's
-- home_score/away_score at the LAST scoring_period of each matchup_period.
-- This is ESPN's authoritative team total — slot-aware AND inclusive of
-- any commissioner manual scoring adjustments. Previously platform_points
-- was sum(player.platform_points) which inflated for teams with slot-
-- mismatched players (a hitter slotted at RP would contribute his hitting
-- points to the team total despite ESPN crediting them as 0). Player-level
-- platform_points remains a direct-from-API passthrough; team-level is now
-- ALSO a direct-from-API passthrough (just from the team-score field
-- instead of summed from players).
--
-- This breaks the "team = SUM(players)" invariant from project_conventions
-- for platform_points specifically. That divergence is meaningful and
-- authoritative — captured in the platform_calculated_delta column. The
-- hitting/pitching splits at team level are still player rollups (the
-- wrapper provides only a single team total, no breakdown).
--
-- Pipeline:
--   1. Roll up fct_player_weekly_slot_performance to team grain (SUM counting,
--      SUM *_pts, SUM player platform_*_pts, SUM calculated_*)
--   2. Join team platform scores from stg_matchup_scores (the wrapper's
--      final per-matchup team totals)
--   3. Recompute rate stats via macros from team-level counting sums
--   4. Join matchup pairings from stg_matchup_pairs
--   5. Self-join for opponent context (home + away halves UNIONed)
--   6. Join matchup_schedule for days_in_period metadata
--
-- Grain: one row per (league_key, season_year, matchup_period, team_id).
--
-- Incremental — merge by unique_key. For historical corrections use --full-refresh.
-- The incremental watermark is per-league (league_period_watermark macro).

{{ config(
    materialized='incremental',
    unique_key=['league_key', 'season_year', 'matchup_period', 'team_id'],
    on_schema_change='fail'
) }}

with team_rollup as (
    select
        league_key,
        season_year,
        matchup_period,
        team_id,
        team_name,
        team_abbrev,
        owner_name,

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

        -- Platform scoring splits — still player rollups (wrapper gives no
        -- hitting/pitching breakdown). Sum will not equal team-level
        -- platform_points when slot-mismatched players exist (Phase 4);
        -- that divergence is captured in platform_calculated_delta below.
        sum(platform_hitting_pts)   as platform_hitting_pts,
        sum(platform_pitching_pts)  as platform_pitching_pts,

        -- Calculated scoring (rules-normalized derivation, slot-validity-filtered).
        -- Round ONCE at team grain from the UNROUNDED player totals. The prior
        -- SUM(per-player-rounded calculated_*) overshot ESPN's platform total
        -- by the accumulated rounding (~0.1/team): sum-of-rounds != round-of-sum.
        round(sum(total_hitting_stat_pts),  1) as calculated_hitting_pts,
        round(sum(total_pitching_stat_pts), 1) as calculated_pitching_pts,
        round(sum(total_stat_pts),          1) as calculated_points,

        -- Phase 7 Hpre: team-level rollup of gross-negative-production
        -- across active players.
        sum(negative_points)         as negative_points,

        count(distinct player_id) as active_player_count
    from {{ ref('fct_player_weekly_active_performance') }}
    group by 1, 2, 3, 4, 5, 6, 7
),

-- Phase 4: team-level platform_points sourced from the wrapper's
-- home_score/away_score (ESPN's authoritative team total). The
-- matchup-grain extraction mechanics live in the staging layer --
-- stg_matchup_scores carries the final score per (matchup, team),
-- stg_matchup_pairs carries the who-played-whom spine.
team_platform_scores as (
    select
        league_key,
        season_year,
        matchup_period,
        team_id,
        platform_points
    from {{ ref('stg_matchup_scores') }}
),

matchup_pairs as (
    select
        league_key,
        season_year,
        matchup_period,
        home_team_id,
        away_team_id
    from {{ ref('stg_matchup_pairs') }}
),

team_with_platform as (
    select
        tr.*,
        -- v1.x: round at fact layer to kill cosmetic float wobble. The
        -- raw API value (wrapper's home_score) is a FLOAT and shouldn't
        -- carry more than 1 decimal of meaningful precision anyway --
        -- ESPN scores tab displays 1 decimal. calculated_* are rounded ONCE
        -- here at team grain (see team_rollup, from unrounded player totals);
        -- platform_*_pts and negative_points inherit the player-fact NUMBER
        -- rounding upstream.
        round(tps.platform_points, 1)            as platform_points,
        tr.platform_hitting_pts + tr.platform_pitching_pts
            as player_rollup_platform_points,
        round(
            tps.platform_points - tr.calculated_points, 4
        ) as platform_calculated_delta
    from team_rollup tr
    left join team_platform_scores tps
        on tr.league_key = tps.league_key
        and tr.season_year = tps.season_year
        and tr.matchup_period = tps.matchup_period
        and tr.team_id = tps.team_id
),

with_opponents as (
    -- Home side
    select
        t.*,
        opp.team_id      as opponent_id,
        opp.team_name    as opponent_name,
        opp.owner_name   as opponent_owner,
        opp.platform_points as opponent_points,
        case
            when t.platform_points > opp.platform_points then 'W'
            when t.platform_points < opp.platform_points then 'L'
            else 'T'
        end as result
    from team_with_platform t
    inner join matchup_pairs mp
        on t.league_key = mp.league_key
        and t.season_year = mp.season_year
        and t.matchup_period = mp.matchup_period
        and t.team_id = mp.home_team_id
    inner join team_with_platform opp
        on mp.league_key = opp.league_key
        and mp.season_year = opp.season_year
        and mp.matchup_period = opp.matchup_period
        and mp.away_team_id = opp.team_id

    union all

    -- Away side
    select
        t.*,
        opp.team_id      as opponent_id,
        opp.team_name    as opponent_name,
        opp.owner_name   as opponent_owner,
        opp.platform_points as opponent_points,
        case
            when t.platform_points > opp.platform_points then 'W'
            when t.platform_points < opp.platform_points then 'L'
            else 'T'
        end as result
    from team_with_platform t
    inner join matchup_pairs mp
        on t.league_key = mp.league_key
        and t.season_year = mp.season_year
        and t.matchup_period = mp.matchup_period
        and t.team_id = mp.away_team_id
    inner join team_with_platform opp
        on mp.league_key = opp.league_key
        and mp.season_year = opp.season_year
        and mp.matchup_period = opp.matchup_period
        and mp.home_team_id = opp.team_id
),

with_rates as (
    -- Rates computed here so macros can use bare column names from with_opponents.
    select
        wo.*,
        {{ batting_avg() }}   as avg,
        {{ on_base_pct() }}   as obp,
        {{ slugging_pct() }}  as slg,
        {{ ops() }}           as ops,
        {{ era() }}           as era,
        {{ whip() }}          as whip,
        {{ k_per_9() }}       as k_per_9,
        {{ k_per_bb() }}      as k_per_bb,
        -- Phase 7 E4: promoted from mart-inline to fct columns. The
        -- mart's seed-driven Jinja-loop UNPIVOT in F needs every rate
        -- column addressable by name on the source CTE; matches the
        -- ERA/WHIP/K_PER_9/K_PER_BB pattern.
        {{ hr_per_9() }}      as hr_per_9,
        {{ bb_per_9() }}      as bb_per_9
    from with_opponents wo
)

select
    wr.*,
    datediff('day', ms.start_date, ms.end_date) + 1 as days_in_period,
    -- v1.1.0: schedule attributes denormalized onto the fact so
    -- format_week_label and is_abnormal-filter consumers can read
    -- them directly off fact rows.
    ms.is_abnormal,
    ms.is_playoff,
    ms.playoff_round
from with_rates wr
left join {{ ref('dim_matchup_period') }} ms
    on wr.season_year = ms.season_year
    and wr.matchup_period = ms.matchup_period

{{ league_period_watermark('wr') }}
