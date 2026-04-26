-- fct_weekly_team_stats.sql
-- Wide-format team-weekly convergence fact. Absorbs everything the old
-- fct_weekly_team_scores carried (scoring totals, opponent context, W/L)
-- and adds counting + rate stats.
--
-- Pipeline:
--   1. Roll up fct_weekly_player_stats to team grain (SUM counting, SUM scoring)
--   2. Recompute rate stats via macros from team-level counting sums
--   3. Extract matchup pairings from raw box scores
--   4. Self-join for opponent context (home + away halves UNIONed)
--   5. Join matchup_schedule for days_in_period metadata
--
-- Grain: one row per (season_year, matchup_period, team_id).
--
-- Incremental -- merge by unique_key. For historical corrections use --full-refresh.

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
        sum(total_points)    as total_points,
        sum(hitting_points)  as hitting_points,
        sum(pitching_points) as pitching_points,
        count(distinct player_id) as active_player_count
    from {{ ref('fct_weekly_player_stats') }}
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
        opp.total_points as opponent_points,
        case
            when t.total_points > opp.total_points then 'W'
            when t.total_points < opp.total_points then 'L'
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
        opp.total_points as opponent_points,
        case
            when t.total_points > opp.total_points then 'W'
            when t.total_points < opp.total_points then 'L'
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
