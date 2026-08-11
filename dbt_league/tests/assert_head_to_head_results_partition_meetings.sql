-- assert_head_to_head_results_partition_meetings.sql
-- wins + losses + ties = meetings, and the season window is ordered.
--
-- The W/L/T columns are counted from `result`, while `meetings` is a plain
-- count(*). They can only agree if every countable row carries a result in
-- {W, L, T} -- so a fourth value, or a NULL slipping past the model's filter,
-- shows up here as an arithmetic gap rather than as a quietly short total in
-- a rendered cell.
--
-- The season check rides along because it fails the same way: first/last come
-- from min/max over the same rows, so last < first is not a data condition at
-- all -- it is proof the two were computed over different sets.

select
    league_key,
    row_canonical_franchise_id,
    opponent_canonical_franchise_id,
    meetings,
    wins,
    losses,
    ties,
    first_meeting_season,
    last_meeting_season
from {{ ref('mart_franchise_head_to_head') }}
where wins + losses + ties != meetings
   or last_meeting_season < first_meeting_season
