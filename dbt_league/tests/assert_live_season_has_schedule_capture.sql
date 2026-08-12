-- assert_live_season_has_schedule_capture.sql
-- WARN: a league's latest season has no schedule capture, so nothing can prove
-- which of its matchups are finished.
--
-- The rivalry ledger fails CLOSED on this -- an uncaptured, unproven season
-- mints no results -- which is correct and also silent. A maintainer whose
-- current season is simply missing from the matrix has no way to tell that
-- from a season in which nothing has happened yet, and the fix (run the
-- schedule extract) is not one they would guess at.
--
-- So the gate stays closed and this says why. Scoped to the season that is
-- actually affected: an old season with no capture is either superseded or
-- proven by final standings, and needs nothing.
--
-- WARN, not ERROR: the capture is opt-in by design, a league that has never
-- run it is in a supported state, and the ledger's answer is already safe.
-- Failing a build over a missing OPTIONAL extract would make the option a lie.
{{ config(severity='warn') }}

select
    league_key,
    season_year,
    completion_evidence,
    'latest season has no schedule capture -- its matchups cannot be proven '
    || 'closed, so the rivalry ledger is excluding them. Run the matchup '
    || 'schedule extract for this league to include the live season.'
        as remedy
from {{ ref('int_league_season_closure') }}
where not has_schedule_capture
  and not is_season_complete
