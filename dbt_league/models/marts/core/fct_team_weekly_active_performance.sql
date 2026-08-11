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

        -- Platform scoring splits — still player rollups (wrapper gives no
        -- hitting/pitching breakdown). Sum will not equal team-level
        -- platform_points when slot-mismatched players exist (Phase 4);
        -- that divergence is captured in platform_calculated_delta below.
        {{ stable_sum("platform_hitting_pts", none) }}   as platform_hitting_pts,
        {{ stable_sum("platform_pitching_pts", none) }}  as platform_pitching_pts,

        -- Calculated scoring (rules-normalized derivation, slot-validity-filtered).
        -- Round ONCE at team grain from the UNROUNDED player totals. The prior
        -- SUM(per-player-rounded calculated_*) overshot ESPN's platform total
        -- by the accumulated rounding (~0.1/team): sum-of-rounds != round-of-sum.
        -- Exact-decimal summation (MLB-128) so the rounded result cannot
        -- land either side of a boundary depending on the engine's chosen
        -- summation order.
        {{ stable_sum("total_hitting_stat_pts") }}  as calculated_hitting_pts,
        {{ stable_sum("total_pitching_stat_pts") }} as calculated_pitching_pts,
        {{ stable_sum("total_stat_pts") }}          as calculated_points,

        -- Phase 7 Hpre: team-level rollup of gross-negative-production
        -- across active players.
        {{ stable_sum("negative_points", none) }}         as negative_points,

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
    --
    -- The OPPONENT join is a left join, not an inner one (MLB-222 C-1b).
    -- In an odd-numbered league one team per period has no opponent, and
    -- stg_matchup_pairs carries that as a NULL away_team_id. Under an
    -- inner join that team produced no team-week row at all and its whole
    -- week of production vanished from every downstream surface -- a
    -- silent hole, not a visible gap. The pairing join above stays INNER:
    -- it is the schedule spine, and a team-week with no schedule row is
    -- genuinely unknown rather than a bye.
    --
    -- result is NULL for a bye rather than 'T'. That is the contract the
    -- consumers were already written against -- output/league_notes.py's
    -- `if team['result'] is None: return []  # bye-week team breaks the
    -- rule` could never fire before this change.
    select
        t.*,
        opp.team_id      as opponent_id,
        opp.team_name    as opponent_name,
        opp.owner_name   as opponent_owner,
        opp.platform_points as opponent_points,
        case
            when opp.team_id is null then null
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
    left join team_with_platform opp
        on mp.league_key = opp.league_key
        and mp.season_year = opp.season_year
        and mp.matchup_period = opp.matchup_period
        and mp.away_team_id = opp.team_id

    union all

    -- Away side. Symmetric with the home arm above; see that comment.
    -- A bye row (NULL away_team_id) matches no team here, so the bye team
    -- is emitted exactly once -- by the home arm -- and never doubled.
    select
        t.*,
        opp.team_id      as opponent_id,
        opp.team_name    as opponent_name,
        opp.owner_name   as opponent_owner,
        opp.platform_points as opponent_points,
        case
            when opp.team_id is null then null
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
    left join team_with_platform opp
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
    -- MLB-235: the platform's own scoring-period count when it has one,
    -- and the seed's date arithmetic only as the legacy fallback. ESPN
    -- serves no calendar, so a league with no hand-maintained schedule
    -- still gets a day count.
    ms.scoring_period_count as days_in_period,
    -- v1.1.0: schedule attributes denormalized onto the fact so
    -- format_week_label and is_abnormal-filter consumers can read
    -- them directly off fact rows.
    ms.is_abnormal,
    -- MLB-235: the non-null gate. LEFT join, so an unanswered period
    -- arrives NULL and must read FALSE -- unknown eligibility is not
    -- ordinary, and leaving it NULL would hand the three-valued problem to
    -- every consumer that reads fact rows directly.
    coalesce(ms.is_record_eligible, false) as is_record_eligible,

    ms.is_playoff,
    ms.playoff_round
from with_rates wr
left join {{ ref('dim_matchup_period') }} ms
    on wr.league_key = ms.league_key
    and wr.season_year = ms.season_year
    and wr.matchup_period = ms.matchup_period

{{ league_period_watermark('wr') }}
