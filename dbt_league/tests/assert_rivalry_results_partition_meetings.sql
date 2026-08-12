-- assert_rivalry_results_partition_meetings.sql
-- wins + losses + ties = meetings on BOTH ledgers, the season windows are
-- ordered, and no row is empty on both.
--
-- The matchup W/L/T columns are counted from `result` while matchup_meetings
-- is a plain count(*): they agree only if every countable row carries a result
-- in {W, L, T}, so a fourth value or a NULL slipping past the model's filter
-- shows up here as an arithmetic gap rather than as a quietly short total in a
-- rendered cell. The season columns are counted from three mutually exclusive
-- comparisons of the same two numbers, so a gap there means a NULL total
-- reached the comparison -- which would silently become "neither outscored".
--
-- The season-window checks ride along because they fail the same way:
-- first/last come from min/max over the same rows, so last < first is not a
-- data condition at all, it is proof the two were computed over different
-- sets. Each window must also be present exactly when its ledger has rows --
-- a NULL window on a ledger with meetings, or a window on an empty one, means
-- the coalesce and the aggregate disagree about which pairs exist.
--
-- The both-empty check is the reason a pair exists at all: a row with nothing
-- on either ledger is a pair the full outer join invented.

select
    league_key,
    row_identity_key,
    opponent_identity_key,
    matchup_meetings,
    matchup_wins,
    matchup_losses,
    matchup_ties,
    season_meetings,
    season_wins,
    season_losses,
    season_ties,
    first_meeting_season,
    last_meeting_season,
    first_season_compared,
    last_season_compared
from {{ ref('mart_franchise_rivalry') }}
where matchup_wins + matchup_losses + matchup_ties != matchup_meetings
   or season_wins + season_losses + season_ties != season_meetings
   or last_meeting_season < first_meeting_season
   or last_season_compared < first_season_compared
   or (matchup_meetings > 0 and first_meeting_season is null)
   or (matchup_meetings = 0 and first_meeting_season is not null)
   or (season_meetings > 0 and first_season_compared is null)
   or (season_meetings = 0 and first_season_compared is not null)
   or (matchup_meetings = 0 and season_meetings = 0)
