-- Row-count sanity. Expect:
--   - row_count > 0 (the brick has data)
--   - fa_rows > 0 (FA time is preserved with team_id NULL)
--   - active_status_rows + inactive_status_rows = row_count (full partition)
--   - distinct grain keys = row_count (no accidental duplicates beyond unique test)

select
    count(*) as row_count,
    count_if(team_id is null) as fa_rows,
    count_if(performance_status = 'active') as active_status_rows,
    count_if(performance_status = 'inactive') as inactive_status_rows,
    count(distinct season_year || '-' || coalesce(to_varchar(team_id), 'FA') || '-' || player_id || '-' || lineup_slot) as distinct_grain_keys,
    count(distinct season_year) as seasons_present,
    count(distinct player_id) as players_present
from {{ ref('fct_player_season_performance') }}
