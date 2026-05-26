-- Sanity invariant: fct_player_season_performance is a faithful season-grain
-- rollup of fct_weekly_player_performance. SUM(platform_points) and
-- SUM(games_played) per player_id should match exactly between the two facts.
-- Discrepancies surface real aggregation bugs in the new view.
--
-- Expected: zero rows returned. Any returned row indicates drift.

with season_totals as (
    select
        player_id,
        sum(platform_points) as season_platform_points,
        sum(games_played)    as season_games_played,
        sum(calculated_points) as season_calculated_points
    from {{ ref('fct_player_season_performance') }}
    group by 1
),

weekly_totals as (
    select
        player_id,
        sum(platform_points) as weekly_platform_points,
        sum(games_played)    as weekly_games_played,
        sum(total_stat_pts)  as weekly_calculated_points
    from {{ ref('fct_weekly_player_performance') }}
    group by 1
)

select
    coalesce(s.player_id, w.player_id) as player_id,
    s.season_platform_points,
    w.weekly_platform_points,
    s.season_games_played,
    w.weekly_games_played,
    s.season_calculated_points,
    w.weekly_calculated_points
from season_totals s
full outer join weekly_totals w
    on s.player_id = w.player_id
where
    -- floats: tolerate sub-cent rounding drift just in case
    abs(coalesce(s.season_platform_points, 0)   - coalesce(w.weekly_platform_points, 0))   > 0.001
    or coalesce(s.season_games_played, 0)        <> coalesce(w.weekly_games_played, 0)
    or abs(coalesce(s.season_calculated_points, 0) - coalesce(w.weekly_calculated_points, 0)) > 0.001
