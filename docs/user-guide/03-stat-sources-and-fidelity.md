# Stat sources and walk-back fidelity

Status: **DRAFT** -- not yet published.

The head-to-head league's history is short and fully recorded. The points
league's history runs back to 2001, and most of it was never recorded in
a form anyone intended to be read again. This page is about how much of
each era is *known* versus *reconstructed*, because the almanac shows
both and labels which is which.

If you only read one thing: **the further back you go, the more the
numbers are a careful reconstruction rather than a transcript.** The
workbook says so per era, on the team pages and on the points league's
Home tab.

## Where the numbers come from

**The head-to-head league** reads the fantasy platform's own feeds: per-week
box scores with full lineups and stat breakdowns, the scoring settings,
roster settings, ownership, and the draft. This is direct and complete
for the seasons covered. Transaction history is only reachable for the
current season -- older seasons are not exposed by the platform.

**The points league** is assembled from three different sources, because
no single one is sufficient:

1. **The platform's API**, for current-season rosters, standings, config,
   and season-level stat totals. Note what is *not* here: the platform
   publishes no per-game fantasy points at all, and its player-stat
   endpoint turned out to cover only free agents -- meaning the players
   actually on rosters were the ones missing. That discovery is why the
   third source exists.
2. **The league's own web pages, scraped**, for the history the API does
   not serve: standings back to 2001, year-end roster snapshots back to
   2003, roughly 56,000 individual transaction records back to 2001, and
   drafts back to 2009.
3. **The public MLB Stats API**, for the actual baseball: full career game
   logs for every player involved, independent of any fantasy platform.
   This is the universal layer -- real games, real stat lines -- which
   the fantasy layer is then mapped onto.

Splitting it that way means the *baseball* is never in doubt. What has to
be reconstructed is the *fantasy* state around it: who owned whom, and
who was in the lineup on a given day.

## What "walk-back" means

Drafts were never logged in the points league's early years. So a
season's opening rosters exist in no transaction record anywhere -- the
log tells you that someone was traded in May, but not who started the
year on the roster.

The walk-back reconstructs roster membership day by day, working from
two kinds of evidence: the transaction log's move history, and year-end
roster snapshots as anchors. If a player shows up in the year-end
snapshot with no acquisition earlier that season, the reconstruction
infers they were there from opening day.

It also treats **lineup activity as ownership evidence**. Starting or
benching a player is not an acquisition, but you cannot bench a player
you do not own -- so those events count as proof of membership even
though they say nothing about how the player arrived.

Crucially, the reconstruction is **graded, not asserted**. The pipeline
carries a report card that compares its reconstructed season totals
against the league's official published final standings, for every season
it covers. The error figures below come from that comparison.

## Fidelity by era

| Era | How it is known | Typical error vs official |
|---|---|---|
| 2026 onward | Captured live, daily | -- |
| 2021-2025 | Reconstructed day by day from the transaction log | about 2-4% |
| 2004-2020 | Estimated from year-end start-share rates | about 5-13% |
| 2001-2003 | Reconstructed from the transaction log, without roster anchors | 2001-02 around 80% |

A few things this table implies that are worth stating outright:

**The 2004-2020 estimate is deliberately conservative.** For those years
the log records who was on a roster but not who was in the lineup on a
given day. The reconstruction uses each player's season-long rate of
being started as a weight. Where even that is unavailable, the
contribution is counted as **zero** rather than guessed -- so those
seasons under-count rather than over-count. From 2011 onward the estimate
runs roughly 8-13% low.

**That start-share rate is a player statistic, not a team one.** If a
player was traded mid-season, the rate describing how often he was
started is the rate he finished the year with, wherever he finished it.
For a mid-season stint, that borrows a number earned somewhere else.

**2001 and 2002 are the weakest years in the book.** There are no
year-end roster pages for them, which removes the anchors the
reconstruction leans on everywhere else. Treat those two seasons as
directional. Production that can be tied to the league but not to any
team is parked in a clearly-labelled placeholder franchise rather than
being silently assigned to somebody.

## Known gaps and open questions

**Pre-season trades are structurally invisible.** The transaction report
the history is scraped from does not include them. In the one season
where a second independent source was available for comparison, 746 of
748 moves matched -- and one of the two misses was a pre-season trade.

**One scoring category disagrees with itself.** The platform's
season-level figure for one pitching category is off by up to three
either way from its own per-game data. The pipeline derives that category
from the per-game numbers instead and confines the platform's own figure
to reconciliation. There is a documented case where the derived number is
more accurate than the platform's published total, because the platform's
feed was missing the underlying events.

**Unresolved:** official standings for 2021-2023 show team pitching
running roughly 8-11% below the reconstruction, while hitting tracks
within 3-5%, and both line up again from 2024. That pattern suggests a
team-level pitching cap in those seasons that has not been confirmed.
It is an open question, not a finding -- do not read those three seasons'
pitching totals as settled.

**Residual loose ends:** a small number of roster stints whose end was
never logged, and a few players whose last logged event was a departure
despite appearing in the year-end snapshot.

## Why the record book uses re-priced points

Because the platform's own historical player archive covers only free
agents, it silently omits exactly the players you would most expect to
see in a record book -- the stars, who were rostered. A record book built
on that archive would have been quietly, badly wrong. Records therefore
run on stats re-priced from the universal MLB data, where every player is
present. See [The points lenses](02-points-lenses.md).

Two population caveats remain and are worth knowing: the earliest era
misses players who passed through without ever appearing in an anchor
snapshot, and a player's production counts toward league-wide leaderboards
even in seasons when nobody rostered him.
