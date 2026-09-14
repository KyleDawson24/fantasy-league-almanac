-- Singular test: on every daily row, hitting points + pitching points =
-- total points (MLB-222, finding SC-04).
--
-- int_player_daily computes total_hitting_stat_pts and
-- total_pitching_stat_pts by stat_category and total_stat_pts
-- unconditionally. The seed allows a third scored category -- 'fielding',
-- seven ESPN stats (ids 67-73) -- and the strict-slot filter deliberately
-- lets fielding through for any slot. So a league that scores errors,
-- assists or putouts gets those points in the total and in NEITHER
-- subtotal, and every almanac breakdown that assumes the two halves make
-- the whole (Advanced Standings, the team pages, the records book) is
-- quietly short. Nothing tested the invariant; this does.
--
-- It is green on both pioneer leagues today (measured 2026-09-14 on
-- Snowflake: 0 of 133,813 ESPN rows and 0 of 988,153 CBS rows off by more
-- than 0.001; the largest gap is 3e-16, float noise). When a league first
-- scores a fielding stat this fails loudly on the first build, which is
-- the moment to decide where fielding points belong -- a third subtotal,
-- or a rule folding them into hitting -- rather than after a season of
-- breakdowns that do not add up.
--
-- Tolerance 0.001: the subtotals and the total are three separate
-- stable_sum passes over the same rows, so they can differ by float
-- noise but never by a stat's points.
--
-- Returns one row per violating daily row; zero rows = pass.

select
    league_key,
    season_year,
    scoring_period,
    team_id,
    player_id,
    total_hitting_stat_pts,
    total_pitching_stat_pts,
    total_stat_pts,
    'hitting + pitching != total: a scored stat is in a category neither '
    || 'subtotal counts' as failed_invariant
from {{ ref('fct_player_daily_performance') }}
where abs(
        coalesce(total_hitting_stat_pts, 0)
        + coalesce(total_pitching_stat_pts, 0)
        - coalesce(total_stat_pts, 0)
      ) > 0.001
