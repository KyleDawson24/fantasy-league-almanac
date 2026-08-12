-- assert_rivalry_has_no_diagonal.sql
-- A team never has a row against itself, on either ledger.
--
-- The requirement is that the rendered matrix's diagonal reads BLANK. An empty
-- cell says "not a thing that happens"; a 0-0 says "they played and nobody
-- won" -- which the matrix DOES use, for two active teams that have genuinely
-- never met. The two must stay distinguishable, so the diagonal has to be
-- absence rather than zero, and long format gives that only as long as no
-- self-pair is ever emitted.
--
-- The case worth pinning is not a team scheduled against itself -- platforms
-- do not do that -- but two DIFFERENT platform ids, or two different canonical
-- franchises, collapsing onto one identity through a shared configured
-- canonical name. That is invisible to any check on raw team ids and only
-- appears after identity resolution, which is where the model filters it and
-- where this test looks.

select
    league_key,
    row_identity_key,
    opponent_identity_key,
    matchup_meetings,
    season_meetings
from {{ ref('mart_franchise_rivalry') }}
where row_identity_key = opponent_identity_key
