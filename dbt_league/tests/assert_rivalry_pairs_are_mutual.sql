-- assert_rivalry_pairs_are_mutual.sql
-- If A has a row against B, B must have one against A.
--
-- Separate from assert_rivalry_reciprocity because the two failures mean
-- different things and want different diagnoses: this one says a direction is
-- MISSING (an asymmetric filter, or an identity that resolved on one side of a
-- result and not the other), while that one says both directions exist and
-- their numbers disagree. An outer join covering both would report either as
-- "reciprocity broke" and leave the reader to work out which.

select
    a.league_key,
    a.row_identity_key,
    a.opponent_identity_key,
    a.matchup_meetings,
    a.season_meetings
from {{ ref('mart_franchise_rivalry') }} a
left join {{ ref('mart_franchise_rivalry') }} b
    on a.league_key = b.league_key
    and a.row_identity_key = b.opponent_identity_key
    and a.opponent_identity_key = b.row_identity_key
where b.league_key is null
