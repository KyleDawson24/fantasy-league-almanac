# The points lenses

Status: **DRAFT** -- not yet published.

Almost every points number in the almanac exists in two versions, and
they disagree. That is not a bug, and neither one is "the wrong number."
They answer different questions.

## Platform Points vs calculated points

**Platform points** are what the fantasy platform itself gave you. 
These are the authority for what actually happened: who won a matchup, what
place a team finished in for a given year. 
If your league's site says you scored 309.4 that week, that is the platform number.

**Calculated points** take the same underlying stat line -- the same homers, the same strikeouts -- and score it under **this season's** scoring rules.

You want the second one whenever you compare across seasons. 
Leagues change their scoring settings; a home run has not always been worth what it is worth now. Comparing a 2015 raw total against a 2026 raw total largely measures the rule change. 
Re-pricing everything with one rulebook is what makes "the best hitting week in league
history" a real claim observed through a modern lens instead of an artifact.

You want the first one whenever the question is about the record books of the league as it was actually played -- results, standings, titles.

**Where each shows up.** The record book runs on the calculated lens, where scores are involved. Match results and standings run on the platform lens. 
Season-long benchmark comparisons deliberately exclude the platform numbers entirely,
because averaging across seasons with different rulebooks would be confusing and noisy.

**One caveat.** In a head-to-head league, a team's platform score comes straight from the platform's own reported total, and it does not always exactly equal the sum of the individual players' platform points. In the case of 2-way players and manual scoring adjustments, there can be some drift between those two numbers. 
The workbook keeps a column for that difference rather than papering over
it, which is also the easiest way to spot a commissioner's manual
adjustment.

**In the points league**, there is no per-day platform number for historic seasons at all --
the platform only ever published season totals. 
So day-to-day and week-to-week points in that league are always the calculated lens. The
season totals are kept as an anchor to check the reconstruction against;
see [Stat sources and fidelity](03-stat-sources-and-fidelity.md).

## Active, inactive, and wasted
Every player-game is one of:

- **Active** -- in a Fantasy Team's starting lineup slot, so the production counted for
  that team.
- **Inactive** -- rostered but not in an active lineup slot: bench or reserve (includes IL slots). The production happened and belongs to a team, but did not count towards any league scores.
- **Unrostered** -- nobody rostered the player that day. The production happened for nobody.

The almanac tracks all three, which is what makes the futility surfaces possible: benching someone who then hits three homers is a real event, and the workbook can find it.

Note that the inactive side carries no *platform* points, only calculated ones. That is not an omission -- bench production never rolled up into any platform score, so there is nothing for the platform to have awarded.

**Wasted points** is production a team had access to and did not get, and it means one thing everywhere in the workbook: **unrostered + inactive + the size of any negative active-game totals**.

That third term is there because giving points back is its own kind of
waste. A starter who goes +100 one day and -100 the next could have given his team 100 more points simply if his owner had sat him on day 2, and simply netting him out at 0 would hide that.

## The optimal-lineup boards
The Home tab's all-league teams, and the Starters block on each team's page, are **computed best lineups**: given everything that was produced, what was the best legal arrangement of it?

Two things are worth knowing about how they are chosen:

**It is not simply "best player at each slot."** Filling greedily from the top gives away too much -- a player eligible at three positions gets consumed by the first one considered. 
Instead, at each step the selection fills the slot where the gap between its best and second-best available candidate is largest. In plain terms: fill the slot where settling for the runner-up would hurt most, and leave the flexible slots for last. This typically means the resulting lineup is the best lineup that could have been fielded, in the window it is being viewed.

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
