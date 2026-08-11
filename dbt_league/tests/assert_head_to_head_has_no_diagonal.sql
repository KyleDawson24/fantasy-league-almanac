-- assert_head_to_head_has_no_diagonal.sql
-- A franchise never has a row against itself.
--
-- The requirement is that the rendered matrix's diagonal reads BLANK rather
-- than 0-0: an empty cell says "not a thing that happens", a 0-0 says "they
-- played and it was scoreless". Long format gets that for free only as long
-- as no self-pair is ever emitted, so this pins it.
--
-- The case worth pinning is not a team scheduled against itself -- platforms
-- do not do that -- but two DIFFERENT platform ids collapsing onto one
-- canonical franchise in the same season. That is invisible to any check on
-- raw team ids and only appears after lineage resolution, which is where the
-- model filters it and where this test looks.

select
    league_key,
    row_canonical_franchise_id,
    opponent_canonical_franchise_id,
    meetings
from {{ ref('mart_franchise_head_to_head') }}
where row_canonical_franchise_id = opponent_canonical_franchise_id
