-- fct_weekly_team_performance.sql
-- Wide-format team-weekly convergence fact. Absorbs everything the old
-- fct_weekly_team_scores carried (scoring totals, opponent context, W/L)
-- and adds counting + rate stats.
--
-- Phase 3.2 additions: rolls up the per-stat *_pts columns and the
-- calculated_* totals from fct_weekly_player_performance, so team-level
-- consumers can ask the same "top N stats by point contribution" and
-- "what would this team have scored under current rules" questions that
-- the player fact supports.
--
-- Pipeline:
--   1. Roll up fct_weekly_player_performance to team grain (SUM counting,
--      SUM *_pts, SUM scoring totals, SUM calculated_*)
--   2. Recompute rate stats via macros from team-level counting sums
--   3. Extract matchup pairings from raw box scores
--   4. Self-join for opponent context (home + away halves UNIONed)
--   5. Join matchup_schedule for days_in_period metadata
--
-- Grain: one row per (season_year, matchup_period, team_id).
--
-- Incremental — merge by unique_key. For historical corrections use --full-refresh.

{{ config(
    materialized='incremental',
    unique_key=['season_year', 'matchup_period', 'team_id'],
    on_schema_change='fail'
) }}

with team_rollup as (
    select
        season_year,
        matchup_period,
        team_id,
        team_name,
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

        -- Platform scoring (ESPN-computed; renamed from total_points etc. in Phase 3.2)
        sum(platform_points)        as platform_points,
        sum(platform_hitting_pts)   as platform_hitting_pts,
        sum(platform_pitching_pts)  as platform_pitching_pts,

        -- Calculated scoring (rules-normalized derivation)
        sum(calculated_hitting_pts)  as calculated_hitting_pts,
        sum(calculated_pitching_pts) as calculated_pitching_pts,
        sum(calculated_points)       as calculated_points,

        count(distinct player_id) as active_player_count
    from {{ ref('fct_weekly_player_performance') }}
    group by 1, 2, 3, 4, 5
),

-- Matchup pairings extracted from raw. Same pattern as old fct_weekly_team_scores.
matchup_pairs as (
    select distinct
        season_year,
        matchup_period,
        m.value:home_team_id::integer as home_team_id,
        m.value:away_team_id::integer as away_team_id
    from {{ source('raw', 'box_scores') }},
        lateral flatten(input => raw_json) m
    qualify row_number() over (
        partition by season_year,
            matchup_period,
            m.value:home_team_id::integer,
            m.value:away_team_id::integer
        order by scoring_period
    ) = 1
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
    from team_rollup t
    inner join matchup_pairs mp
        on t.season_year = mp.season_year
        and t.matchup_period = mp.matchup_period
        and t.team_id = mp.home_team_id
    inner join team_rollup opp
        on mp.season_year = opp.season_year
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
    from team_rollup t
    inner join matchup_pairs mp
        on t.season_year = mp.season_year
        and t.matchup_period = mp.matchup_period
        and t.team_id = mp.away_team_id
    inner join team_rollup opp
        on mp.season_year = opp.season_year
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
        {{ k_per_bb() }}      as k_per_bb
    from with_opponents wo
)

select
    wr.*,
    datediff('day', ms.start_date, ms.end_date) + 1 as days_in_period
from with_rates wr
left join {{ ref('matchup_schedule') }} ms
    on wr.season_year = ms.season_year
    and wr.matchup_period = ms.matchup_period

{% if is_incremental() %}
where (wr.season_year * 100 + wr.matchup_period) >= (
    select coalesce(max(season_year * 100 + matchup_period), 0) from {{ this }}
)
{% endif %}
