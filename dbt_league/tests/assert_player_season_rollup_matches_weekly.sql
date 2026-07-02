-- Singular test: fct_player_season_performance is a faithful season-grain
-- rollup of fct_player_weekly_slot_performance.
--
-- Checked per grain row (season_year, team_id, player_id, lineup_slot):
--   1. Every weekly grain combination appears in the season fact and vice
--      versa (the FULL OUTER JOIN catches dropped/invented rows).
--   2. games_played matches exactly (integer sum).
--   3. platform_points / calculated_points match within 0.051. The season
--      fact deliberately rounds each points column to 1 decimal per row to
--      freeze float-summation-order drift (see the model header), so a
--      faithful rollup may differ from the raw weekly re-sum by up to 0.05;
--      anything past that is a real aggregation bug.
--
-- Returns one row per drifting grain row; zero rows = pass.
-- (Converted from analyses/check_fct_player_season_invariant.sql. The
-- analysis predated the season fact's round-once-per-row change and assumed
-- exact sums -- and, being an analysis, it never re-ran to notice. Enforced
-- as a test, with the tolerance the rounding actually implies.)

with weekly_rollup as (
    select
        season_year,
        team_id,
        player_id,
        lineup_slot,
        sum(platform_points) as platform_points,
        sum(total_stat_pts)  as calculated_points,
        sum(games_played)    as games_played
    from {{ ref('fct_player_weekly_slot_performance') }}
    group by 1, 2, 3, 4
),

season as (
    select
        season_year,
        team_id,
        player_id,
        lineup_slot,
        platform_points,
        calculated_points,
        games_played
    from {{ ref('fct_player_season_performance') }}
)

select
    coalesce(s.season_year, w.season_year)   as season_year,
    coalesce(s.team_id, w.team_id)           as team_id,
    coalesce(s.player_id, w.player_id)       as player_id,
    coalesce(s.lineup_slot, w.lineup_slot)   as lineup_slot,
    s.platform_points                        as season_platform_points,
    w.platform_points                        as weekly_platform_points,
    s.calculated_points                      as season_calculated_points,
    w.calculated_points                      as weekly_calculated_points,
    s.games_played                           as season_games_played,
    w.games_played                           as weekly_games_played
from season s
full outer join weekly_rollup w
    on  s.season_year = w.season_year
    and coalesce(to_varchar(s.team_id), 'FA') = coalesce(to_varchar(w.team_id), 'FA')
    and s.player_id = w.player_id
    and s.lineup_slot = w.lineup_slot
where
    s.player_id is null  -- weekly grain row missing from the season fact
    or w.player_id is null  -- season fact invented a grain row
    or coalesce(s.games_played, -1) <> coalesce(w.games_played, -1)
    or abs(coalesce(s.platform_points, 0)   - coalesce(w.platform_points, 0))   > 0.051
    or abs(coalesce(s.calculated_points, 0) - coalesce(w.calculated_points, 0)) > 0.051
