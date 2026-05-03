-- int_player_daily_stats.sql
-- Slot-agnostic player-daily stat detail with stat_classification join applied.
-- Filters to is_counting=true (rate stats dropped here -- they can't be summed,
-- and rates get recomputed via macros at the fact layer where slot filter has
-- been applied).
--
-- Phase 3.2: joins stg_scoring_settings to compute stat_points (stat_value *
-- points_per_unit) for each counting stat. Stats not present in the scoring
-- settings get stat_points = 0 (they exist in the data but don't contribute
-- to fantasy scoring). The current season's weights are applied universally
-- -- including to historical data -- so cross-season comparisons use a common
-- scoring scale.
--
-- Phase 4: HBP_P disambiguation moved to extract.py (_STAT_ID_TO_NAME[42] =
-- 'HBP_P'). The wrapper's STATS_MAP previously collapsed stat IDs 12 (batter
-- HBP, +1) and 42 (pitcher HBP, -1) under the single name "HBP", which we
-- patched here with a lineup_slot-based CASE. That patch broke for FAs
-- (lineup_slot='FA' has no role signal) and for two-way Ohtani days where
-- both stats were summed under one name and zero-or-double-signed by slot
-- alone. Fixing at extract decouples the seed from the wrapper collision
-- and removes the need for the disambiguation CASE entirely.
--
-- Phase 4: slot-stat-category compatibility filter. ESPN's team-level
-- scoring (and our fct_weekly_team_performance.platform_points, sourced
-- direct from the wrapper home_score) credits a player's stats only when
-- the stat's category matches the slot type — a hitter's hitting stats
-- only count from a hitting slot, a pitcher's pitching stats only from a
-- pitching slot. Without this filter, calculated_points (and counting
-- rollups) would inflate for slot-mismatched cases (a hitter slotted in
-- RP, a two-way player whose hitting line bleeds into an SP-slot day).
-- The filter is wrapped in var('strict_slot_validity', true) so it can be
-- toggled off if league rules ever change to credit cross-slot stats.
-- Fielding-category stats pass through regardless (they're position-
-- agnostic in our scoring).
--
-- Long format. lineup_slot preserved so downstream models can produce
-- active variants (fct_weekly_player_performance) and inactive variants
-- (mart_wasted_points) from the same intermediate.
--
-- Grain: one row per (season, matchup, scoring_period, team, player, slot, stat_name).

with daily as (
    select * from {{ ref('stg_player_stat_breakdowns') }}
),

classification as (
    select stat_name, stat_category, is_counting
    from {{ ref('stat_classification') }}
),

scoring as (
    select stat_name, points_per_unit
    from {{ ref('stg_scoring_settings') }}
),

filtered as (
    select
        d.season_year,
        d.matchup_period,
        d.scoring_period,
        d.team_id,
        d.team_name,
        d.owner_name,
        d.player_id,
        d.player_name,
        d.lineup_slot,
        d.lineup_slot_category,
        d.stat_name,
        c.stat_category,
        d.stat_value,
        coalesce(sc.points_per_unit, 0) as points_per_unit,
        d.stat_value * coalesce(sc.points_per_unit, 0) as stat_points
    from daily d
    inner join classification c
        on d.stat_name = c.stat_name
    left join scoring sc
        on d.stat_name = sc.stat_name
    where c.is_counting = true
        {% if var('strict_slot_validity', true) %}
        and (
            c.stat_category = d.lineup_slot_category
            or c.stat_category = 'fielding'
            or d.lineup_slot_category = 'inactive'
        )
        {% endif %}
)

select * from filtered
