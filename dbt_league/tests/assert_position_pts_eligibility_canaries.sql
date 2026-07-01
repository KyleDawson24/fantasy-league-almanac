-- Data canaries for fct_player_position_pts, pinned to known 2026 facts:
--   1. Trout carries multi-position eligibility rows (OF/CF/UTIL/DH).
--   2. Soto picked up DH + LF eligibility mid-season (scoring period 29);
--      both rows should exist with eligible_days >= 1.
--   3. FA rows exist (team_id IS NULL) -- free agency happens.
--
-- severity warn, not error: these assert that *known historical data* still
-- looks right after a rebuild (an eligibility-explosion or FA-handling
-- regression), not a structural invariant of the model. A warehouse reload
-- that drops 2026 would trip them without any code being wrong.
-- (Converted from analyses/check_position_pts_invariants.sql.)

{{ config(severity='warn') }}

with trout_multi_position as (
    select count(*) as n
    from {{ ref('fct_player_position_pts') }}
    where display_name ilike '%Trout%'
      and season_year = 2026
      and position in ('OF', 'CF', 'UTIL', 'DH')
),

soto_midseason_pickup as (
    select count(*) as n
    from {{ ref('fct_player_position_pts') }}
    where display_name ilike '%Soto%'
      and season_year = 2026
      and position in ('DH', 'LF')
      and eligible_days > 0
),

fa_rows_present as (
    select count(*) as n
    from {{ ref('fct_player_position_pts') }}
    where team_id is null
)

select 'no Trout multi-position rows for 2026' as failed_canary, n as observed_rows
from trout_multi_position
where n = 0

union all

select 'Soto DH/LF post-pickup rows missing (expected >= 2)', n
from soto_midseason_pickup
where n < 2

union all

select 'no FA rows -- check upstream FA handling', n
from fa_rows_present
where n = 0
