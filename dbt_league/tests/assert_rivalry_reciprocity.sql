-- assert_rivalry_reciprocity.sql
-- Every rivalry is told twice, and the two tellings must agree EXACTLY --
-- on BOTH ledgers.
--
-- The mart emits one row per ORDERED pair, so (A vs B) and (B vs A) are
-- separate rows built from the same underlying results. Disagreement means a
-- filter stopped being symmetric. The likely causes differ per ledger, which
-- is why both are checked here rather than one standing in for the other:
-- matchups break when a score is present on one side and missing on the
-- other, or when the closure gate reaches one direction only; seasons break
-- when the pairwise join stops being a self-join over one set.
--
-- INNER join deliberately: a missing reverse row is
-- assert_rivalry_pairs_are_mutual's job, and folding both into one outer join
-- would make a failure ambiguous about which rule broke.
--
-- Points compare in exact decimal. The columns are doubles, and two doubles
-- equal by construction can still differ in their last bit after a round trip;
-- comparing at the scale the model rounds to asks what the model promises.

with mutual as (
    select
        a.league_key,
        a.row_identity_key      as team_a,
        a.opponent_identity_key as team_b,

        a.matchup_meetings as a_matchup_meetings,
        b.matchup_meetings as b_matchup_meetings,
        a.matchup_wins     as a_matchup_wins,
        b.matchup_losses   as b_matchup_losses,
        a.matchup_losses   as a_matchup_losses,
        b.matchup_wins     as b_matchup_wins,
        a.matchup_ties     as a_matchup_ties,
        b.matchup_ties     as b_matchup_ties,

        cast(a.points_for     as decimal(18, 1)) as a_points_for,
        cast(b.points_against as decimal(18, 1)) as b_points_against,
        cast(a.points_against as decimal(18, 1)) as a_points_against,
        cast(b.points_for     as decimal(18, 1)) as b_points_for,
        cast(a.points_margin  as decimal(18, 1)) as a_margin,
        cast(b.points_margin  as decimal(18, 1)) as b_margin,

        a.season_meetings as a_season_meetings,
        b.season_meetings as b_season_meetings,
        a.season_wins     as a_season_wins,
        b.season_losses   as b_season_losses,
        a.season_losses   as a_season_losses,
        b.season_wins     as b_season_wins,
        a.season_ties     as a_season_ties,
        b.season_ties     as b_season_ties,

        cast(a.season_points_for     as decimal(18, 1)) as a_season_points_for,
        cast(b.season_points_against as decimal(18, 1)) as b_season_points_against,

        a.first_meeting_season  as a_first_meeting,
        b.first_meeting_season  as b_first_meeting,
        a.last_meeting_season   as a_last_meeting,
        b.last_meeting_season   as b_last_meeting,
        a.first_season_compared as a_first_season,
        b.first_season_compared as b_first_season,
        a.last_season_compared  as a_last_season,
        b.last_season_compared  as b_last_season

    from {{ ref('mart_franchise_rivalry') }} a
    join {{ ref('mart_franchise_rivalry') }} b
        on a.league_key = b.league_key
        and a.row_identity_key = b.opponent_identity_key
        and a.opponent_identity_key = b.row_identity_key
)

select *
from mutual
where a_matchup_meetings != b_matchup_meetings
   or a_matchup_wins     != b_matchup_losses
   or a_matchup_losses   != b_matchup_wins
   or a_matchup_ties     != b_matchup_ties
   or a_points_for       != b_points_against
   or a_points_against   != b_points_for
   or a_margin           != -b_margin
   or a_season_meetings  != b_season_meetings
   or a_season_wins      != b_season_losses
   or a_season_losses    != b_season_wins
   or a_season_ties      != b_season_ties
   or a_season_points_for != b_season_points_against
   -- Season windows are NULL together or equal; `is distinct from` says both
   -- in one operator, where `!=` would let a NULL on one side pass silently.
   or a_first_meeting is distinct from b_first_meeting
   or a_last_meeting  is distinct from b_last_meeting
   or a_first_season  is distinct from b_first_season
   or a_last_season   is distinct from b_last_season
