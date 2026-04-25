-- fct_weekly_player_stats.sql
-- Wide-format player-weekly convergence fact. The consumer-facing entity for
-- "what did this player do this week" -- counting stats, rate stats, and
-- platform fantasy points all in a single row per player per matchup.
--
-- Pipeline:
--   1. Read int_player_weekly_stats (slot-preserved counting columns)
--   2. Filter to active slots (lineup_slot NOT IN ('BE', 'IL', 'FA'))
--   3. Aggregate counting columns across slots (collapse slot dimension)
--   4. Compute rate stats via macros from the aggregated counting columns
--   5. Join fct_weekly_player_scores for total_points / hitting_points / pitching_points
--
-- Grain: one row per (season_year, matchup_period, team_id, player_id).
--
-- Incremental fact -- merge by unique_key. Re-extracted matchup periods
-- overwrite existing rows. For historical corrections, use --full-refresh.

{{ config(
    materialized='incremental',
    unique_key=['season_year', 'matchup_period', 'team_id', 'player_id'],
    on_schema_change='fail'
) }}

with active as (
    select
        season_year,
        matchup_period,
        team_id,
        team_name,
        owner_name,
        player_id,
        player_name,
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
        sum(wp)      as wp
    from {{ ref('int_player_weekly_stats') }}
    where lineup_slot not in ('BE', 'IL', 'FA')
    group by 1, 2, 3, 4, 5, 6, 7
),

scores as (
    select
        season_year,
        matchup_period,
        team_id,
        player_id,
        display_name,
        total_points,
        hitting_points,
        pitching_points
    from {{ ref('fct_weekly_player_scores') }}
)

select
    a.season_year,
    a.matchup_period,
    a.team_id,
    a.team_name,
    a.owner_name,
    a.player_id,
    a.player_name,
    coalesce(s.display_name, a.player_name) as display_name,

    -- Hitting counting
    a.h, a.ab, a.b_bb, a.b_so, a.hbp, a.sf, a.hr, a.r, a.rbi,
    a.sb, a.cs, a.tb, a.singles, a.doubles, a.triples, a.xbh,

    -- Pitching counting
    a.w, a.l, a.k, a.er, a.outs, a.qs, a.sv, a.hld,
    a.p_h, a.p_bb, a.p_hr, a.p_r, a.cg, a.blk, a.wp,

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

    -- Scoring (from fct_weekly_player_scores, which rolls up int_player_daily_scores)
    s.total_points,
    s.hitting_points,
    s.pitching_points

from active a
left join scores s
    on a.season_year = s.season_year
    and a.matchup_period = s.matchup_period
    and a.team_id = s.team_id
    and a.player_id = s.player_id

{% if is_incremental() %}
where (a.season_year * 100 + a.matchup_period) >= (
    select coalesce(max(season_year * 100 + matchup_period), 0) from {{ this }}
)
{% endif %}
