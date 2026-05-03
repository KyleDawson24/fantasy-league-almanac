-- mart_wasted_points.sql
-- Phase 4: "what did you leave on the table" — points produced by players
-- in inactive lineup slots (bench, IL) and by free agents the league did
-- not roster.
--
-- Reads int_player_daily_stats with the inverse slot filter
-- (lineup_slot_category = 'inactive', i.e. BE/IL/FA) and aggregates to
-- weekly grain. The slot-validity filter at int passes inactive rows
-- through unchanged, so a benched hitter's hitting stats and a benched
-- pitcher's pitching stats both arrive intact and get bucketed by
-- stat_category here.
--
-- Bucket: BE and IL are collapsed into ROSTERED_INACTIVE (a benched
-- player and an IL'd player are substantively the same wasted-points
-- story for fantasy managers — both "rostered, didn't contribute"). FA
-- stays its own bucket. Anyone wanting per-IL or per-BE detail can drop
-- down to int_player_daily_stats.
--
-- A player who was benched 4 days and FA 3 days within the same
-- matchup_period produces TWO rows (one ROSTERED_INACTIVE, one FA) since
-- the bucket changed mid-week. Different rows of the same player are
-- distinguishable for analyses that care about transition stories.
--
-- Materialization: view. The dataset is small (~thousands of rows / week)
-- and bucket assignment is deterministic from the inputs, so always-fresh
-- on every dbt run is cheaper than incremental state management.
--
-- Grain: one row per (season_year, matchup_period, player_id, wasted_bucket).

{{ config(materialized='view') }}

with inactive_stats as (
    select
        season_year,
        matchup_period,
        team_id,
        team_name,
        owner_name,
        player_id,
        player_name,
        lineup_slot,
        case
            when lineup_slot = 'FA' then 'FA'
            else 'ROSTERED_INACTIVE'
        end as wasted_bucket,
        stat_category,
        stat_points,
        scoring_period
    from {{ ref('int_player_daily_stats') }}
    where lineup_slot_category = 'inactive'
),

aggregated as (
    select
        season_year,
        matchup_period,
        player_id,
        player_name,
        wasted_bucket,
        -- BE/IL: team_id/owner_name carry through (the team that benched
        -- them). FA: NULLs (no fantasy team owns them). MAX is used in
        -- the rare case a player switched teams within the bucket within
        -- a single matchup_period — pick one team rather than producing
        -- multiple rows per (player, bucket).
        max(team_id)        as team_id,
        max(team_name)      as team_name,
        max(owner_name)     as owner_name,
        sum(stat_points)    as wasted_points,
        sum(case when stat_category = 'hitting'  then stat_points else 0 end) as wasted_hitting_pts,
        sum(case when stat_category = 'pitching' then stat_points else 0 end) as wasted_pitching_pts,
        count(distinct scoring_period) as days_in_bucket
    from inactive_stats
    group by 1, 2, 3, 4, 5
)

select
    a.*,
    coalesce(n.nickname, a.player_name) as display_name
from aggregated a
left join {{ ref('player_nicknames') }} n
    on a.player_id = n.player_id
