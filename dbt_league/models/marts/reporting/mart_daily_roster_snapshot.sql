-- mart_daily_roster_snapshot.sql
-- One row per rostered fantasy player per scoring period, including
-- players with zero MLB stats that day.
--
-- This is intentionally separate from fct_player_daily_performance. The
-- performance fact starts from stat breakdowns and is therefore ideal
-- for scoring/stat analysis, but zero-stat rostered players do not
-- survive that path. Roster pages need the full ESPN lineup shell from
-- stg_box_scores, including bench, IL, off-day pitchers, and quiet bats.
--
-- Grain: (season_year, scoring_period, team_id, player_id, lineup_slot).
-- Excludes FA rows; this is a rostered-player snapshot only.
--
-- Materialization: view. This is a thin mart contract over staging plus
-- dim_roster_slot_counts metadata. It is a "mart_" model rather than a
-- "fct_" model because its primary job is roster state/history, not
-- scoring measurement. The grain is still event-like enough that it is
-- not a stable entity dimension.

{{ config(materialized='view') }}

select
    b.season_year,
    b.matchup_period,
    b.scoring_period,
    b.team_id,
    b.team_name,
    b.team_abbrev,
    b.owner_name,
    -- v1.3: canonical preferred owner display, resolved here (like the other
    -- consumption surfaces) so the per-team-tab consumer reads owner_display
    -- directly instead of the raw box-score owner_name.
    coalesce(tod.owner_display, b.owner_name) as owner_display,
    b.player_id,
    b.player_name,
    b.display_name,
    b.position,
    b.pro_team,
    b.eligible_slots,
    b.lineup_slot,
    b.games_played,
    coalesce(b.points, 0) as platform_points,

    coalesce(rs.starter_count, 1) as slots_to_fill,
    rs.maximum_count,
    rs.maximum_type,
    rs.maximum_display,
    coalesce(rs.sort_order, 999) as slot_sort_order,
    coalesce(
        rs.is_active_lineup_slot,
        b.lineup_slot not in ('BE', 'IL')
    ) as is_active_lineup_slot,
    case
        when coalesce(rs.is_active_lineup_slot, b.lineup_slot not in ('BE', 'IL'))
            then 'active'
        else 'inactive'
    end as roster_status
from {{ ref('stg_box_scores') }} b
left join {{ ref('dim_roster_slot_counts') }} rs
    on b.season_year = rs.season_year
    and b.lineup_slot = rs.lineup_slot
left join {{ ref('int_team_owner_display') }} tod
    on b.season_year = tod.season_year
    and b.team_id = tod.team_id
where b.team_id is not null
  and b.lineup_slot <> 'FA'
