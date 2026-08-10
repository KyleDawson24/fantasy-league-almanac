# Standings order: the platform's seed vs. the reconstruction

**Status: in force.** Decided 2026-08-09 (MLB-227 capture, MLB-230 render).
Revisit when the ordering becomes a user config setting -- see *The knob this
is really waiting for* at the bottom.

## The two quantities

An ESPN head-to-head league has **two different answers** to "where does this
team stand", and the almanac renders both. They are not interchangeable and
neither is wrong.

| | what it is | where it comes from | covers |
|---|---|---|---|
| **`playoff_seed`** | the platform's own regular-season standing, `1..N`, assigned to every team including non-qualifiers | `RAW.TEAM_STANDINGS` -> `stg_team_standings` -> `dim_team_season_standing` | one number per team-season |
| **the reconstruction** | standing after matchup period *N*, recomputed from results | `get_team_rank_arc`, over `fct_team_weekly_active_performance` | every week of the season |

They disagree because **ESPN seeds division winners ahead of the field**, and
then orders the rest by record. No sort over wins, ties or points reproduces
that. In the two captured seasons:

- The two-division season seeds both division leaders 1 and 2, and the best
  record among teams leading nothing -- two games better than the second
  division's leader -- seeds only 3. A flat record sort swaps those.
- The four-division season seeds its four division leaders 1 through 4, and
  two teams tied on record within one division split 2nd and 6th on
  head-to-head, which no points tiebreak reproduces at all.

The remaining seeds match record order exactly in both, which is what makes
this the rule rather than a coincidence.

## What each surface uses, and why

**Tables use the seed.** The Detailed Standings tables and the Season
Finishes side table (`get_team_standings`, `get_espn_season_finishes`) all
order by and print `playoff_seed`. These assert a standing, the platform
publishes one, and reconstructing it was provably producing a different
answer than the league's own site.

**The chart uses the reconstruction, and has to.** Rank by Week plots a
position for every matchup period. ESPN keeps no intra-season standings
snapshots -- there is no per-week seed to read, at any price. Week 9's
standing exists only by recomputation. The choice is a reconstructed series
or no chart.

**So the chart's last point can disagree with the standings table above it.**
That is accepted, not overlooked. The alternative -- pinning only the final
point to the seed -- would put a kink in the line exactly where the rule
changes: a team could visibly jump a place between the last two weeks with no
game explaining it. One consistent quantity end to end beats a series that
changes definition at its final point.

The Advanced Standings tab explainer states this outright, because a reader
who spots the disagreement unaided will read it as a bug.

## The one thing that was a bug

Until MLB-230 the side table's **in-flight column** read the arc's endpoint
while its **Avg** column averaged `finish` -- the seed -- across every season
including the one in flight. A row could therefore show `13` and `3` and an
average of `7.5`. Both inputs were defensible and the row still did not add
up, because a reader checks a mean against the numbers printed beside it.

That column now reads the seed like every other column in the table. The
chart was left alone. This is the *only* part of the disagreement that was
worth removing: a mean that contradicts its own row is an error, whereas a
chart and a table measuring different things is a fact about the league that
just needs saying.

## Podium marks read a third quantity

`rankCalculatedFinal` -- the **post-playoff** finish -- is neither of the
above, and it is what the silver and bronze medals key on (MLB-230). Seed and
final routinely disagree at exactly those ranks: in the last closed season
the 7 seed won the title, the 1 seed finished 2nd and the 2 seed finished
6th. A medal placed off the seed would decorate the wrong teams.

Verified against the bracket rather than assumed: the two semi-final losers
play each other in the finals week, and `rankCalculatedFinal = 3` is that
game's winner. All sixteen final ranks are bracket-determined, championship
bracket for 1-8 and consolation for 9-16.

The champion trophy is **not** `rankCalculatedFinal = 1`. It stays derived
from winning every playoff week -- the older definition, the one the Titles
column counts, and the one that still works for a season whose capture
predates MLB-227. Where the two could disagree the derivation wins, so a
second trophy can never appear that Titles would not count.

CBS is a season-long points league with no playoffs. None of this applies
there: second and third are the season standings, and its ordering was not
touched.

## The seed used to go stale quietly. Resolved.

`RAW.TEAM_STANDINGS` was written only under `--include-settings` /
`--settings-only`, because the standings arrive on the same ESPN response as
the settings and MLB-227 wrote them from there. That flag is opt-in because
settings "change rarely and don't need to run on every weekly pull" -- true
while the capture served history, and false the moment it started ordering
the live standings.

The failure mode was silent: a box-score pull advanced the W-L column while
the row order stayed frozen at the last settings capture, so the table
disagreed with itself and read as a rendering bug.

**Resolved 2026-08-09 by splitting them.** `extract_team_standings` asks for
`mTeam` alone -- a smaller response with no settings parsing -- and runs on
every invocation, `--transactions-only` included. `--no-standings` opts out.
A settings run still writes standings from its own combined payload rather
than fetching twice.

The general lesson outlives the fix: **two datasets arriving on one response
are not thereby one dataset.** These two share a payload and have opposite
refresh needs, and the flag that made sense for the slow one silently
throttled the fast one. Do not re-merge them for the sake of the saved
request.

## The knob this is really waiting for

Whether standings order follows the platform's seeding or a plain record sort
is a *league preference*, not a fact -- a manager who thinks division winners
should not jump a better record is not wrong, they want a different book. The
honest shape is a config setting with the platform's own order as the
default, alongside the other lens toggles (cf. `strict_slot_validity`).

Until that exists the default is the platform's answer, on the grounds that a
tool reproducing the league's own site should agree with it out of the box.
