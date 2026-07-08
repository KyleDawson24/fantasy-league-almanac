-- mart_team_slot_production.sql
-- v2.0: season-grain lineup-slot production. One row per
-- (season_year, team_id, lineup_slot) with the calculated points the
-- team's players generated while DEPLOYED in that slot -- the slot on the
-- box score, not position eligibility (that's fct_player_position_pts's
-- question). Feeds the Advanced Standings "Points by Lineup Slot" grid,
-- which previously aggregated this inline in
-- output/almanac_data.py:get_team_slot_points.
--
-- Source: fct_player_weekly_slot_performance -- the slot-preserving weekly
-- fact -- NOT the active convergence fact, which collapses lineup_slot out
-- of its grain by design. FA pool rows (team_id IS NULL) are excluded; a
-- free agent produces for no team's lineup.
--
-- Every deployed slot is kept, including BE / IL, with
-- is_active_lineup_slot carried from dim_roster_slot_counts so consumers
-- choose the cut: the v2.0 grid shows active slots only, and a future
-- bench/IL view (likely on the inactive-points lens) can read the same
-- contract without a model change.
--
-- Regular season only (is_playoff = false), matching
-- mart_team_season_standings so the two Advanced Standings blocks describe
-- the same universe. The slot fact doesn't carry schedule flags (it isn't
-- one of the four convergence facts), hence the dim_matchup_period join.
--
-- Grain: one row per (league_key, season_year, team_id, lineup_slot).
--
-- Materialization: table -- float point sums feeding byte-diff goldens
-- (same determinism rationale as mart_team_season_standings).

{{ config(materialized='table') }}

with slot_weeks as (
    select p.*
    from {{ ref('fct_player_weekly_slot_performance') }} p
    inner join {{ ref('dim_matchup_period') }} mp
        on p.season_year = mp.season_year
        and p.matchup_period = mp.matchup_period
    where p.team_id is not null
      and not mp.is_playoff
),

team_slot as (
    select
        league_key,
        season_year,
        team_id,
        lineup_slot,
        max_by(team_name,   matchup_period) as team_name,
        max_by(team_abbrev, matchup_period) as team_abbrev,
        max(owner_display)                  as owner_display,

        -- Calculated lens (catch-all total across every scored stat),
        -- rounded once at season grain.
        round(sum(total_stat_pts), 1) as slot_calculated_points
    from slot_weeks
    group by 1, 2, 3, 4
)

select
    ts.*,
    -- Display ordering + the active/inactive cut, from the roster-settings
    -- dim. A deployed slot is always a configured slot, so the fallbacks
    -- only guard cross-season settings drift.
    coalesce(rsc.sort_order, 999)              as sort_order,
    coalesce(rsc.is_active_lineup_slot, false) as is_active_lineup_slot,
    rsc.starter_count
from team_slot ts
left join {{ ref('dim_roster_slot_counts') }} rsc
    on ts.league_key = rsc.league_key
    and ts.season_year = rsc.season_year
    and ts.lineup_slot = rsc.lineup_slot
