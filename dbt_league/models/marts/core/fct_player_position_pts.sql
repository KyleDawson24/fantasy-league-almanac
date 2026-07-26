-- fct_player_position_pts.sql
-- v1.1.1: per-position points accumulation for the get_optimal_team
-- consumer. Born as int_player_position_pts; promoted to a core fact
-- once it became a declared exposure dependency -- the almanac's
-- optimal-team selector reads it directly, and consumer contracts live
-- in core, not intermediate (the same reasoning that promoted
-- fct_player_daily_performance in v1.1.0).
--
-- For each (season, matchup_period, team, player) day, the
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
--   One row per (league_key, season_year, matchup_period, team_id, player_KEY, position).
--   team_id is NULLABLE -- NULL rows represent FA time during that period.
--   matchup_period is NULLABLE -- CBS rows carry no period boundaries, so a
--   CBS (season, team, player, position) aggregates into ONE season-grain
--   row with matchup_period NULL (ESPN rows always carry their period).
--   player_key is the identity column (MLB-72): ESPN ids stringified, CBS
--   platform ids including the 'ui-only-' synthetics whose player_id is
--   NULL. player_id remains for ESPN consumers, 1:1 with player_key there.
--   "position" is the eligible_slots code (e.g., '1B', 'OF', 'SP', 'UTIL',
--   plus combined codes like '1B/3B' or '2B/SS' when ESPN expresses them;
--   CBS emits its own vocabulary {C,1B,2B,3B,SS,OF,DH,P}).
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
--     stay in the fact for forward compatibility.
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
        d.league_key,
        d.season_year,
        d.matchup_period,
        d.scoring_period,
        d.team_id,
        d.player_id,
        d.player_key,
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
        -- pitching contribution; his UTIL/DH rows get ONLY his hitting
        -- contribution. Because total_*_stat_pts is slot-validity-
        -- filtered upstream at int_player_daily (a stat survives only
        -- when its category matches that day's lineup_slot_category),
        -- each row carries only the production from days he was ACTUALLY
        -- slotted in that discipline. So "Shohei-pitcher" and "Shohei-
        -- hitter" behave as two day-disjoint pseudo-players (he occupies
        -- one slot per day, so the two rows never share a day), and the
        -- disjoint-stat-categories selection rule (pick a player at most
        -- once per category) fields both without double-counting.
        --
        -- This INTENTIONALLY does not recover his full line on a genuine
        -- two-way day (pitched AND hit, but slotted in one): the off-slot
        -- discipline is zeroed upstream and is lost to the optimal team
        -- too. By design -- this league can't field a player as a hitter
        -- and a pitcher on the same day, so the best achievable lineup
        -- can't claim both either; whatever is lost reflects league rules
        -- or manager slotting, which is exactly what an optimal-deployment
        -- view should surface. (If the league ever allows same-day two-
        -- slot two-way players, the fix is to feed this CASE from
        -- UNFILTERED per-category points -- the same knob as the
        -- strict_slot_validity var on int_player_daily.)
        case when slot.value::string in ('SP', 'RP', 'P')
             then d.total_pitching_stat_pts
             else d.total_hitting_stat_pts
        end as position_calculated_pts,

        d.performance_status,
        d.active_weight
    from {{ ref('fct_player_daily_performance') }} d,
    lateral flatten(input => d.eligible_slots) slot
    where slot.value::string not in ('BE', 'IL')
),

aggregated as (
    select
        league_key,
        season_year,
        matchup_period,
        team_id,
        player_key,
        position,

        -- Display helpers (stable per player_key within a matchup;
        -- MAX is a no-op aggregation that just satisfies GROUP BY).
        -- player_id rides as an aggregate: 1:1 with player_key on ESPN
        -- rows (identical output), NULL on CBS ui-only synthetics.
        max(player_id)     as player_id,
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
        --
        -- MLB-128: rounding the RESULT was not enough, and neither was
        -- materializing this as a table. Measured -- a rebuild with NO code
        -- change moved 14 of 22,587 rows by 0.1, which flipped rendered
        -- Records cells by 1. stable_sum() sums in exact decimal, so the
        -- result no longer depends on the order the engine picked.
        {{ stable_sum("case when performance_status = 'active'
                       then position_calculated_pts else 0 end") }} as active_pts,
        {{ stable_sum("case when performance_status = 'inactive'
                       then position_calculated_pts else 0 end") }} as inactive_pts,

        -- MLB-72 lenses. weighted_active_pts generalizes active_pts across
        -- the union: ESPN days weight 1/0 by slot (identical to active_pts
        -- by construction); CBS estimated-era days contribute their
        -- start-share fraction (NULL-weight days -- membership confirmed,
        -- activity unknown -- contribute 0: conservative, and the almanac's
        -- provenance note owns the caveat). rostered_pts is the
        -- weight-independent total (the CBS bench ranking axis).
        {{ stable_sum("position_calculated_pts * coalesce(active_weight, 0)") }}
            as weighted_active_pts,
        {{ stable_sum("position_calculated_pts") }} as rostered_pts,

        -- Eligibility days: count of distinct scoring periods (= days)
        -- the player was eligible at this position during this matchup
        -- period. Useful for downstream "rate per day at position"
        -- queries; not strictly required by the optimal-team selector.
        count(distinct scoring_period) as eligible_days

    from daily_eligible
    group by 1, 2, 3, 4, 5, 6
)

select * from aggregated
