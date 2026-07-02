-- mart_team_matchup.sql
-- v1.1.1: wide matchup-grain view over fct_team_weekly_active_performance.
-- Each row carries one team's full active line, the opponent's calculated
-- score lens, head-to-head margin and combined totals, and the league-wide
-- per-week averages on the calculated lens.
--
-- Per BRAINTHOUGHTS [ARCH] "A matchup / relative view belongs over the team
-- fact, not on it": the underlying fact's invariant is team_total =
-- SUM(players). Opponent points, margin, and league averages are properties
-- of the *matchup* and the *league*, not the team -- putting them as
-- columns on the fact dilutes what the fact means. Also: the fact is
-- incrementally merged by team-week key; the league-average window
-- partitions across every team in a period, so it cannot sit on an
-- incrementally-built base.
--
-- Grain: one row per (season_year, matchup_period, team_id), filtered to
-- team-weeks with an opponent (so the FA-pool team row and any unpaired
-- bye-week rows are excluded; matchup view by definition needs an
-- opponent). Two rows per matchup -- consumers ranking a matchup as a
-- single game dedupe via LEAST/GREATEST on team_id or by filtering to
-- team_id < opponent_id.
--
-- Materialization: table. The league-average window forbids
-- incrementality, and as a view the AVG re-summed floats in a
-- nondeterministic order on every query -- a league-average cell on a
-- .x5 rounding boundary could flip between two reads with no data
-- change (observed in Matchup History regen diffs). A plain table
-- freezes each build's values; it is small and rebuilds with the fact.
--
-- First consumer: output/almanac_sheets.py:get_team_weeks (Team Weeks tab).
-- Lifted verbatim from the inlined query that previously lived there.

{{ config(materialized='table') }}

select
    t.*,

    -- Stable chronological sort key. Convenient for descending recency
    -- ordering without re-deriving in every consumer.
    (t.season_year * 100 + t.matchup_period) as sort_key,

    -- Opponent's calculated-lens scores. The base fact already carries
    -- opponent_id / opponent_name / opponent_owner / opponent_points
    -- (platform lens, authoritative for W/L). The matchup view adds the
    -- calculated lens so cross-season-comparable margin and combined
    -- totals can be computed without a consumer-side self-join.
    opp.calculated_hitting_pts  as opponent_calculated_hitting_pts,
    opp.calculated_pitching_pts as opponent_calculated_pitching_pts,
    opp.calculated_points       as opponent_calculated_points,

    -- Head-to-head deltas on the calculated lens. Positive margin = this
    -- team scored more than its opponent under current-season rules.
    -- May disagree with platform-derived `result` (W/L from
    -- opponent_points) when commissioner score adjustments exist; that
    -- divergence is meaningful, not drift.
    t.calculated_points - opp.calculated_points as calculated_margin,

    -- Combined matchup totals (this team + opponent). Useful for "highest
    -- combined scoring week" content and for normalizing one team's
    -- output against the contest it was in.
    t.calculated_hitting_pts  + opp.calculated_hitting_pts  as matchup_calculated_hitting_pts,
    t.calculated_pitching_pts + opp.calculated_pitching_pts as matchup_calculated_pitching_pts,
    t.calculated_points       + opp.calculated_points       as matchup_calculated_points,

    -- League-wide per-week averages on the calculated lens. Partitioned
    -- across every team in the period (including this team and its
    -- opponent) so the value is constant for all rows sharing
    -- (season_year, matchup_period). This window is the reason the model
    -- can't live on the incrementally-merged base fact.
    avg(t.calculated_hitting_pts) over (
        partition by t.season_year, t.matchup_period
    ) as league_avg_hitting_points,
    avg(t.calculated_pitching_pts) over (
        partition by t.season_year, t.matchup_period
    ) as league_avg_pitching_points,
    avg(t.calculated_points) over (
        partition by t.season_year, t.matchup_period
    ) as league_avg_total_points

from {{ ref('fct_team_weekly_active_performance') }} t
left join {{ ref('fct_team_weekly_active_performance') }} opp
    on t.season_year    = opp.season_year
    and t.matchup_period = opp.matchup_period
    and t.opponent_id    = opp.team_id
    and t.team_id        = opp.opponent_id
where t.opponent_id is not null
