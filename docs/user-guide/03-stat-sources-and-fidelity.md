# Stat sources and walk-back fidelity
This page documents the sources and reconstruction quality of the two
almanacs this project currently publishes: one for an ESPN head-to-head
league and one for a CBS season-points league. The differences below are
not inherent to those scoring formats. They reflect the data each
platform exposes, the records retained by these particular leagues, and
when collection began.

The published ESPN league's lineup and scoring history is short and
recorded directly. The published CBS league's history runs back to 2001,
and most of it was never recorded in a form anyone intended to be read
again. This page is about how much of each era is *known* versus
*reconstructed*, because the CBS almanac shows both and labels which is
which.

If you only read one thing: **in the CBS history, the further back you
go, the more the numbers are a careful reconstruction rather than a
transcript.** The workbook says so per era, on the team pages and on its
Home tab.

Nothing here promises the same coverage for an unseen league. A new
league has to be assessed against the history its platform exposes and
the records that survive: full historical lineups can provide direct
history; transactions and roster anchors can support reconstruction;
season totals alone support only coarser estimates. Scoring format by
itself does not determine fidelity.

## Where the numbers come from

**The published ESPN league** reads ESPN's own feeds: per-week box scores
with full lineups and stat breakdowns, the scoring settings, roster
settings, ownership, and the draft. For the seasons covered, those feeds
provide direct lineup and scoring history rather than a reconstruction.
One important exception is transaction history, which ESPN exposes only
for the current season.

A second exception is the MLB club attached to each player. ESPN's player
records carry only his *current* club, not the one he played for on a
given day, so a season collected as it happens tracks trades correctly
while a season loaded after the fact does not. Of the two ESPN seasons
published here, 2026 was collected live and 2025 was loaded in one pass
afterwards. Lineups, stats and scoring for 2025 are unaffected -- they
come from the box scores directly -- but the club label on them is the
one the player held at collection time.

The roster-affinity chart on Advanced Standings is the only surface that
depends on that label, and it does two things about it. Production it
cannot place is shown in a row labelled **Unattributed** -- about 12% of
2025, and effectively none of 2026 -- rather than being dropped, which is
what previously happened. And production it places *wrongly* is still
there: a player traded after the 2025 season is credited to the club he
moved to. Read the 2025 half of that chart as directional. The
equivalent CBS chart does not have this problem; it resolves each club
from the game itself.

**The published CBS league's 2001-2026 history** draws on three different
sources, because no single source available during implementation covers
the full history:

1. **CBS's API**, for current-season rosters, standings, config, and
   season-level stat totals. In the endpoints verified for this league,
   CBS publishes no per-game fantasy points, and its historical
   player-stat endpoint covered only free agents -- meaning the players
   actually on rosters were the ones missing. That discovery is why the
   third source exists.
2. **The league's own web pages, scraped**, for the history the API does
   not serve: standings back to 2001, year-end roster snapshots back to
   2003, roughly 56,000 individual transaction records back to 2001, and
   drafts back to 2009.
3. **The public MLB Stats API**, for the actual baseball: full career game
   logs for every player involved, independent of any fantasy platform.
   This is the platform-independent layer -- real games, real stat lines
   -- which the fantasy layer is then mapped onto.

The MLB feed supplies the underlying *baseball* statistics. The primary
reconstruction uncertainty is in the *fantasy* state around them: who
owned whom, and who was in the lineup on a given day.

## What "walk-back" means

The surviving records for the CBS league's early years do not include
drafts, so a season's opening rosters exist in no transaction record
available to the project -- the log tells you that someone was traded in
May, but not who started the year on the roster.

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

## Fidelity of the CBS history by era

| Era | How it is known | Typical error vs official |
|---|---|---|
| 2026 onward | Captured live, daily | -- |
| 2021-2025 | Reconstructed day by day from the transaction log | about 2-4% |
| 2004-2020 | Estimated from year-end start-share rates | about 5-13% |
| 2001-2003 | Reconstructed from the transaction log, without roster anchors | 2001-02 about 12-15% |

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

**2001 and 2002 are the weakest years in the book, but not for the
reason the error column alone suggests.** There are no year-end roster
pages for them, which removes the anchors the reconstruction leans on
everywhere else. That mainly costs *coverage*, not accuracy: a player
who was drafted and held all season -- never added, dropped, traded, or
reserved -- never touches the transaction log, so the walk-back has no
way to know whose roster he was on. Those are disproportionately the
stars, since nobody drops or trades their best players. Production that
can be tied to the league but not to any team is parked in a
clearly-labelled placeholder franchise rather than being silently
assigned to somebody -- currently about a quarter of 2001's reconstructed
production and over a third of 2002's. Treat those two seasons as
directional until that gap closes.

## Known gaps and open questions in the CBS history

**Pre-season trades are structurally invisible.** The transaction report
the history is scraped from does not include them. In the one season
where a second independent source was available for comparison, 746 of
748 moves matched -- and one of the two misses was a pre-season trade.

**One scoring category disagrees with itself.** CBS's season-level figure
for one pitching category is off by up to three either way from its own
per-game data. The pipeline derives that category from the per-game
numbers instead and confines CBS's own figure to reconciliation. There is
a documented case where the derived number is more accurate than CBS's
published total, because its feed was missing the underlying events.

**Unresolved:** official standings for 2021-2023 show team pitching
running roughly 8-11% below the reconstruction, while hitting tracks
within 3-5%, and both line up again from 2024. That pattern suggests a
team-level pitching cap in those seasons that has not been confirmed.
It is an open question, not a finding -- do not read those three seasons'
pitching totals as settled.

**Residual loose ends:** a small number of roster stints whose end was
never logged, and a few players whose last logged event was a departure
despite appearing in the year-end snapshot.

## Why the CBS record book uses re-priced points

Because the CBS historical player archive available to this project
covers only free agents, it silently omits exactly the players you would
most expect to see in a record book -- the stars, who were rostered. A
record book built on that archive would have been quietly, badly wrong.
Records therefore run on stats re-priced from the MLB data, where every
player is present. See [The points lenses](02-points-lenses.md).

Two population caveats remain and are worth knowing: the earliest era
misses players who passed through without ever appearing in an anchor
snapshot, and a player's production counts toward league-wide leaderboards
even in seasons when nobody rostered him.
