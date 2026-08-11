-- assert_head_to_head_pairs_are_mutual.sql
-- If A has a row against B, B must have one against A.
--
-- Separate from assert_head_to_head_reciprocity because the two failures mean
-- different things and want different diagnoses: this one says a direction is
-- MISSING (an asymmetric filter, or an identity that resolved on one side of a
-- meeting and not the other), while that one says both directions exist and
-- their numbers disagree. An outer join covering both would report either as
-- "reciprocity broke" and leave the reader to work out which.

select
    a.league_key,
    a.row_canonical_franchise_id,
    a.opponent_canonical_franchise_id,
    a.meetings
from {{ ref('mart_franchise_head_to_head') }} a
left join {{ ref('mart_franchise_head_to_head') }} b
    on a.league_key = b.league_key
    and a.row_canonical_franchise_id = b.opponent_canonical_franchise_id
    and a.opponent_canonical_franchise_id = b.row_canonical_franchise_id
where b.league_key is null
