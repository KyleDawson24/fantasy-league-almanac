# The points lenses

Status: **DRAFT** -- not yet published. Contains one open question for
the maintainer, marked below.

Almost every points number in the almanac exists in two versions, and
they disagree. That is not a bug, and neither one is "the wrong number."
They answer different questions.

## Awarded points vs re-priced points

**Awarded points** are what the fantasy platform itself gave you. These
are the authority for what actually happened: who won a matchup, what
the standings say. If your league's site says you scored 309.4 that
week, that is the awarded number.

**Re-priced points** take the same underlying stat line -- the same
homers, the same strikeouts -- and score it under **this season's**
scoring rules, including for seasons played long before those rules
existed.

You want the second one whenever you compare across seasons. Leagues
change their scoring settings; a home run has not always been worth what
it is worth now. Comparing a 2015 raw total against a 2026 raw total
mostly measures the rule change, not the players. Re-pricing everything
with one rulebook is what makes "the best hitting week in league
history" a real claim instead of an artifact.

You want the first one whenever the question is about the record books
of the league as it was actually played -- results, standings, titles.

**Where each shows up.** The record book runs on the re-priced lens.
Match results and standings run on the awarded lens. Season-long
benchmark comparisons deliberately exclude the awarded numbers entirely,
because averaging across seasons with different rulebooks would be
misleading.

**One honest caveat.** In the head-to-head league, a team's awarded score
comes straight from the platform's own reported total, and it does not
always exactly equal the sum of the individual players' awarded points.
When a player is slotted somewhere unusual, those can drift apart. The
workbook keeps a column for that difference rather than papering over
it, which is also the easiest way to spot a commissioner's manual
adjustment.

**In the points league**, there is no per-day awarded number at all --
the platform only ever published season totals. So day-to-day and
week-to-week points in that league are always the re-priced lens. The
season totals are kept as an anchor to check the reconstruction against;
see [Stat sources and fidelity](03-stat-sources-and-fidelity.md).

## Active, inactive, and wasted

Every player-day is one of:

- **Active** -- in a starting lineup slot, so the production counted for
  the team.
- **Inactive** -- rostered but on the bench or the injured list. The
  production happened; it just didn't count.
- **Unowned** -- nobody rostered the player that day. The production
  happened for nobody.

The almanac tracks all three, which is what makes the futility surfaces
possible: benching someone who then hits three homers is a real event,
and the workbook can find it.

Note that the inactive side carries no *awarded* points, only re-priced
ones. That is not an omission -- bench production never rolled up into
any platform score, so there is nothing for the platform to have
awarded.

<!-- OPEN QUESTION FOR KYLE -- do not publish until resolved.
     "Wasted points" is defined three different ways in three places:
       1. Home tab Points Glossary (almanac_logic.py:520-524 and the CBS
          twin at cbs_almanac_sheets.py:2762-2764) says
          "inactive points + the size of any negative active-game totals".
       2. The warehouse column WASTED_POINTS
          (mart_stat_leaderboard.sql:167-168) is ROSTERED_INACTIVE
          calculated points ONLY -- the negative-active piece is a
          separate column, negative_points. FA production is excluded.
       3. The CBS Wasted Hall of Shame (cbs_almanac_sheets.py:3183-3184)
          says "unrostered + benched", which INCLUDES the FA pool that
          definition 2 excludes.
     Also: get_wasted_points_records exists and is exported
     (almanac_data.py:2239) but is not in the ESPN Records tab's default
     spec list (almanac_logic.py:1532-1537), so I could not find a Wasted
     Points block actually rendering on the ESPN Records tab.
     I have written the section below to the shape all three agree on and
     left the arithmetic out. Tell me which definition is canonical and I
     will make the glossary, the column, and the CBS block say it. -->

**Wasted points** is the umbrella term for production a team had access
to and did not get. Read the specific definition on whichever surface you
are looking at, because the surfaces currently scope it differently --
some count only your own bench, some also count players nobody rostered.

## The optimal-lineup boards

The Home tab's all-league teams, and the Starters block on each team's
page, are **computed best lineups**: given everything that was produced,
what was the best legal arrangement of it?

Two things are worth knowing about how they are chosen.

**It is not simply "best player at each slot."** Filling greedily from
the top gives away too much -- a player eligible at three positions gets
consumed by the first one considered. Instead, at each step the selection
fills the slot where the gap between its best and second-best available
candidate is largest. In plain terms: fill the slot where settling for
the runner-up would hurt most, and leave the flexible slots for last.

**These lineups are stable between rebuilds.** That sounds obvious but
took deliberate work. Summing floating-point numbers in a different
order gives very slightly different totals, which was enough to flip
values sitting exactly on a rounding boundary and reshuffle relief-pitcher
picks between two runs with no data change at all. The sums are now done
in exact decimal and ties are broken by a fixed rule, so the same inputs
always produce the same board.

## A note on totals

Per-stat point columns (points from home runs, points from strikeouts)
are useful for breakdowns but should not be summed to get a team's
total. The workbook keeps separate catch-all totals that sum *every*
scored stat, including ones that have no dedicated column of their own.
In the points league, at least one scored category exists only inside
that catch-all -- so the catch-all is the authoritative number there, not
a convenience.

Displayed totals are rounded once, at the level being displayed, rather
than summed from already-rounded parts.
