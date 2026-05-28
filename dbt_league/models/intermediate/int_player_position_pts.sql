-- int_player_position_pts.sql
-- v1.1.1: per-position points accumulation for the get_optimal_team
-- consumer. For each (season, matchup_period, team, player) day, the
-- player's eligible_slots VARIANT array is exploded; each eligible
-- slot code (minus BE / IL, which are roster slots not lineup
-- positions) gets attributed the player's calculated points (i.e.
-- total_hitting_stat_pts / total_pitching_stat_pts) for that day.
-- Points are then aggregated to matchup-period grain.
--
-- Points lens: calculated (cross-season-normalized scoring rules),
-- not platform. The optimal-team primitive answers "who historically
-- would have done well in this league's current scoring setting,"
-- which is the calculated lens by definition; platform points reflect
-- whatever ESPN's scoring was at the time and are not comparable
-- across seasons when rules have changed.
--
-- The mental model: "How many calculated points has this player
-- produced while being eligible at position P, within window W?"
-- Use this to fill an optimal lineup: each lineup slot picks its
-- best-fitting eligible player, where "best-fitting" is by SUM(points)
-- across the window.
--
-- ==========================================================================
-- GRAIN:
--   One row per (season_year, matchup_period, team_id, player_id, position).
--   team_id is NULLABLE -- NULL rows represent FA time during that period.
--   "position" is the eligible_slots code (e.g., '1B', 'OF', 'SP', 'UTIL',
--   plus combined codes like '1B/3B' or '2B/SS' when ESPN expresses them).
--   BE and IL are filtered out.
-- ==========================================================================
--
-- Materialization: table (v1.2). Was a view, but the get_optimal_team
-- selector is gap-based -- it breaks near-equal ties (relievers, mostly)
-- on calculated points rounded to 1 decimal. As a view, Snowflake
-- re-summed those points with a slightly different float order on every
-- query, so a value sitting on a .x5 rounding boundary could flip
-- between regens and silently reshuffle RP picks across team tabs +
-- the Home All-League Team. Materializing freezes the rounded points so
-- the selection is deterministic across regens (the byte-diff then only
-- moves on real changes). Rebuilds it on new data like any fact.
-- Only consumer is the Python get_optimal_team dispatcher in
-- output/almanac_data.py. Row count ~65K for two seasons.
--
-- Notes on "position" semantics:
--   - For this league's roster shape, the position codes that map to
--     lineup slots are: C, 1B, 2B, 3B, SS, IF, LF, CF, RF, OF, DH,
--     UTIL, SP, RP. Combined codes (CI=1B/3B, MI=2B/SS, P=any pitcher)
--     exist in the data but don't map to any current lineup slot; they
--     stay in the int for forward compatibility.
--   - eligible_slots already enumerates every fillable slot per ESPN;
--     a 1B-eligible player automatically shows IF and UTIL eligibility
--     in their array. No derivation needed at this layer.
--
-- Rounding: per-row points are rounded to 1 decimal at this layer,
-- matching the brick + active fact precedents. Stabilizes downstream
-- SUMs against float-summation-order drift.

{{ config(materialized='table') }}

with daily_eligible as (
    select
        d.season_year,
        d.matchup_period,
        d.scoring_period,
        d.team_id,
        d.player_id,
        d.player_name,
        d.display_name,
        d.pro_team,
        slot.value::string as position,

        -- Position-category-appropriate calculated points. For pitching
        -- positions (SP / RP / P), use total_pitching_stat_pts; for
        -- every other position (hitter slots + UTIL + DH + the unused
        -- CI/MI/hybrid codes), use total_hitting_stat_pts. Both columns
        -- are derived at int_player_daily by summing per-stat
        -- stat_points within their respective categories, so they
        -- represent the calculated (current-rules-normalized) point
        -- value of that day's production.
        --
        -- For single-discipline players (~99% of the league), the
        -- "other" component is 0, so position_calculated_pts equals
        -- the player's full daily calculated total -- behavior matches
        -- intuition.
        --
        -- For two-way players (Shohei): his SP row gets ONLY his
        -- pitching contribution; his UTIL/DH rows get ONLY his
        -- hitting contribution. The disjoint-stat-categories selection
        -- rule (pick a player at most once per category) then correctly
        -- credits the team with hitting + pitching = full production
        -- when picked twice, without double-counting either component.
        case when slot.value::string in ('SP', 'RP', 'P')
             then d.total_pitching_stat_pts
             else d.total_hitting_stat_pts
        end as position_calculated_pts,

        d.performance_status
    from {{ ref('fct_player_daily_performance') }} d,
    lateral flatten(input => d.eligible_slots) slot
    where slot.value::string not in ('BE', 'IL')
),

aggregated as (
    select
        season_year,
        matchup_period,
        team_id,
        player_id,
        position,

        -- Display helpers (stable per player_id within a matchup;
        -- MAX is a no-op aggregation that just satisfies GROUP BY).
        max(player_name)   as player_name,
        max(display_name)  as display_name,
        max(pro_team)      as pro_team,

        -- Two point lenses (active / inactive). Consumers wanting an
        -- "all points" view compute active_pts + inactive_pts at query
        -- time -- storing it as a separately-rounded third column
        -- creates a 0.1 rounding gap vs. the sum-of-the-two (the SUM
        -- of two independently-rounded values doesn't always equal
        -- ROUND of the underlying SUM). Cleaner to derive at consumer
        -- time.
        --
        -- Rounded at the fact layer (v1.0.1 precedent) so downstream
        -- SUMs across matchup_periods or positions stay stable across
        -- summation orders.
        round(sum(case when performance_status = 'active'
                       then position_calculated_pts else 0 end), 1) as active_pts,
        round(sum(case when performance_status = 'inactive'
                       then position_calculated_pts else 0 end), 1) as inactive_pts,

        -- Eligibility days: count of distinct scoring periods (= days)
        -- the player was eligible at this position during this matchup
        -- period. Useful for downstream "rate per day at position"
        -- queries; not strictly required by the optimal-team selector.
        count(distinct scoring_period) as eligible_days

    from daily_eligible
    group by 1, 2, 3, 4, 5
)

select * from aggregated
