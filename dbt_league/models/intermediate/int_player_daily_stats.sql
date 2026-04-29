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
-- Phase 3.2 disambiguation: ESPN stat IDs 12 (batter HBP, +1) and 42
-- (pitcher HBP, -1) BOTH translate to the name "HBP" via the espn-api
-- wrapper's STATS_MAP. The breakdown VARIANT therefore can't tell us which
-- one a row represents. We disambiguate using lineup_slot: when a player is
-- in a pitching slot (SP/RP/P), HBP means "pitcher hit a batter" and gets
-- rewritten to HBP_P (which has the correct -1 weight via the seed). When
-- in a hitting slot, HBP stays as the +1 batter stat.
-- This logic lives at int (not staging) because staging stays a pure
-- reshape; business interpretation belongs at the intermediate layer.
--
-- Long format. lineup_slot preserved so downstream models can produce
-- active variants (fct_weekly_player_performance) and inactive variants
-- (future mart_wasted_points) from the same intermediate.
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

disambiguated as (
    -- Resolve the HBP name collision based on lineup_slot context.
    -- Extend this CTE if other stats turn out to share names across roles.
    select
        d.*,
        case
            when d.stat_name = 'HBP' and d.lineup_slot in ('SP', 'RP', 'P')
                then 'HBP_P'
            else d.stat_name
        end as resolved_stat_name
    from daily d
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
        d.resolved_stat_name as stat_name,
        c.stat_category,
        d.stat_value,
        coalesce(sc.points_per_unit, 0) as points_per_unit,
        d.stat_value * coalesce(sc.points_per_unit, 0) as stat_points
    from disambiguated d
    inner join classification c
        on d.resolved_stat_name = c.stat_name
    left join scoring sc
        on d.resolved_stat_name = sc.stat_name
    where c.is_counting = true
)

select * from filtered
