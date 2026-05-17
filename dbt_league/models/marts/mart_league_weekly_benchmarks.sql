-- mart_league_weekly_benchmarks.sql
-- One row per (season_year, matchup_period) with league-wide aggregate
-- score lenses (mean, max, min) plus percentile-in-history rank of each
-- mean. Powers "hot week / cold week" callouts and similar
-- collective-mood content in the weekly recap.
--
-- Grain: (season_year, matchup_period). Excludes is_abnormal weeks
-- (All-Star break, weather-shortened, etc.) to match the convention
-- every other leaderboard CTE uses.
--
-- Aggregation includes every active-roster team-week including bye-
-- week teams (when applicable; this league is 14-team H2H so no byes,
-- but other leagues might). Bye-week teams still rostered players and
-- accrued points -- the absence of a matchup pairing doesn't make
-- their production any less part of the league's collective output.
--
-- Percentile uses PERCENT_RANK over all weeks in history: a current-
-- season week sitting at .95 means it's higher than 95% of weeks the
-- league has ever recorded. Callouts read it as "Nth percentile" via
-- ROUND(pctile * 100, 0).
--
-- Materialization: view. Aggregation is small (one row per league-week,
-- ~26 per season) and the source fact is already a table.

{{ config(materialized='view') }}

with per_week as (
    select
        t.season_year,
        t.matchup_period,
        count(*)                            as team_count,

        -- Calculated lens (cross-season comparable under current weights;
        -- the right lens for "how does this week stack up historically").
        avg(t.calculated_points)            as calculated_points_mean,
        max(t.calculated_points)            as calculated_points_max,
        min(t.calculated_points)            as calculated_points_min,

        avg(t.calculated_hitting_pts)       as calculated_hitting_pts_mean,
        max(t.calculated_hitting_pts)       as calculated_hitting_pts_max,
        min(t.calculated_hitting_pts)       as calculated_hitting_pts_min,

        avg(t.calculated_pitching_pts)      as calculated_pitching_pts_mean,
        max(t.calculated_pitching_pts)      as calculated_pitching_pts_max,
        min(t.calculated_pitching_pts)      as calculated_pitching_pts_min

    from {{ ref('fct_weekly_team_active_performance') }} t
    inner join {{ ref('matchup_schedule') }} s
        on t.season_year = s.season_year
        and t.matchup_period = s.matchup_period
    where s.is_abnormal = false
    group by 1, 2
)

select
    season_year,
    matchup_period,
    team_count,

    round(calculated_points_mean,         1) as calculated_points_mean,
    calculated_points_max,
    calculated_points_min,
    percent_rank() over (order by calculated_points_mean)
                                             as calculated_points_pctile,

    round(calculated_hitting_pts_mean,    1) as calculated_hitting_pts_mean,
    calculated_hitting_pts_max,
    calculated_hitting_pts_min,
    percent_rank() over (order by calculated_hitting_pts_mean)
                                             as calculated_hitting_pts_pctile,

    round(calculated_pitching_pts_mean,   1) as calculated_pitching_pts_mean,
    calculated_pitching_pts_max,
    calculated_pitching_pts_min,
    percent_rank() over (order by calculated_pitching_pts_mean)
                                             as calculated_pitching_pts_pctile

from per_week
