-- assert_head_to_head_reciprocity.sql
-- Every rivalry is told twice, and the two tellings must agree EXACTLY.
--
-- The ledger emits one row per ORDERED pair, so (A vs B) and (B vs A) are
-- separate rows built from the same underlying meetings. If they ever
-- disagree, one of the two filters stopped being symmetric -- the likeliest
-- cause being a score present on one side of a matchup and missing on the
-- other, which would keep one direction and drop the reverse.
--
-- Written as an INNER join deliberately: a missing reverse row is caught by
-- assert_head_to_head_pairs_are_mutual, and folding both checks into one
-- outer join would make a failure ambiguous about which rule broke.
--
-- Points compare in exact decimal. The columns are doubles, and two doubles
-- that are equal by construction can still differ in their last bit after a
-- round trip; comparing at the scale the model rounds to asks the question
-- the model actually promises to answer.

with mutual as (
    select
        a.league_key,
        a.row_canonical_franchise_id      as franchise_a,
        a.opponent_canonical_franchise_id as franchise_b,

        a.meetings       as a_meetings,
        b.meetings       as b_meetings,
        a.wins           as a_wins,
        b.losses         as b_losses,
        a.losses         as a_losses,
        b.wins           as b_wins,
        a.ties           as a_ties,
        b.ties           as b_ties,

        cast(a.points_for     as decimal(18, 1)) as a_points_for,
        cast(b.points_against as decimal(18, 1)) as b_points_against,
        cast(a.points_against as decimal(18, 1)) as a_points_against,
        cast(b.points_for     as decimal(18, 1)) as b_points_for,
        cast(a.points_margin  as decimal(18, 1)) as a_margin,
        cast(b.points_margin  as decimal(18, 1)) as b_margin,

        a.first_meeting_season as a_first,
        b.first_meeting_season as b_first,
        a.last_meeting_season  as a_last,
        b.last_meeting_season  as b_last

    from {{ ref('mart_franchise_head_to_head') }} a
    join {{ ref('mart_franchise_head_to_head') }} b
        on a.league_key = b.league_key
        and a.row_canonical_franchise_id = b.opponent_canonical_franchise_id
        and a.opponent_canonical_franchise_id = b.row_canonical_franchise_id
)

select *
from mutual
where a_meetings      != b_meetings
   or a_wins          != b_losses
   or a_losses        != b_wins
   or a_ties          != b_ties
   or a_points_for    != b_points_against
   or a_points_against != b_points_for
   or a_margin        != -b_margin
   or a_first         != b_first
   or a_last          != b_last
