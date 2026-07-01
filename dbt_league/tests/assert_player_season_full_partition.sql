-- Singular test: fct_player_season_performance covers its universe.
--   1. The fact is non-empty.
--   2. FA time is preserved (rows with team_id NULL exist -- free agency
--      happens every season; zero FA rows means the anti-join upstream broke).
--   3. performance_status is a full partition: active + inactive = total.
--
-- Returns one row per violated invariant; zero rows = pass.
-- Grain uniqueness is NOT re-checked here -- the dbt_utils
-- unique_combination_of_columns test on the model already enforces it.
-- (Converted from analyses/check_fct_player_season_counts.sql, which emitted
-- a metrics row for a human to eyeball; the invariants are now enforced.)

with counts as (
    select
        count(*) as row_count,
        count_if(team_id is null) as fa_rows,
        count_if(performance_status = 'active') as active_status_rows,
        count_if(performance_status = 'inactive') as inactive_status_rows
    from {{ ref('fct_player_season_performance') }}
)

select 'fact is empty' as failed_invariant, row_count as observed_value
from counts
where row_count = 0

union all

select 'no FA rows (team_id is null missing)', fa_rows
from counts
where fa_rows = 0

union all

select 'performance_status does not partition the fact', active_status_rows + inactive_status_rows
from counts
where active_status_rows + inactive_status_rows <> row_count
