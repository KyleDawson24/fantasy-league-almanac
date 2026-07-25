# Release Notes — v1.5.1 (DRAFT)

*The record book, made correct. A waypoint on the road to 2.0.*

> Status: ready to tag. **MLB-123** — the ESPN renderer's twin rounding fix —
> has landed. It is byte-neutral on the current two-season data (a preventive
> hardening rather than a visible correction), keeping both books' rounding
> consistent.

## In one line

A batch of correctness fixes to the CBS almanac's record book: attribution
that had been silently mis-crediting player-games across 26 seasons is now
provably exact — every game credits exactly one franchise — and the record
book produces the same answer twice.

## The story

This release began as a one-line display refactor and became the arc of the
version. Routing every team's display through the franchise dimension
surfaced the CBS almanac's first byte-diff golden — and within an hour that
golden caught a silent corruption: attribution had been mis-crediting
player-games across the league's entire 26-season history, and the record
book didn't even produce the same answer twice.

Every fix uncovered the next. The non-determinism traced to same-day roster
stints truncating each other; that to a transaction capture silently dropping
~408 rows at pagination seams; a headline that disagreed with its own
breakdown to record values being rounded twice.

Then the two large ones:

- **The roster walk-back** had paired transactions in effective-date order,
  silently assuming managers never reprocess a move. A queued lineup change, a
  retroactive drop, or a same-minute trade flurry each mis-credited a
  franchise or stranded a real roster as free agents. It now resolves each
  day's state by the most-recently-*executed* transaction effective by then —
  reconstructing roster history as the transaction log actually describes it.

- **Player identity** returned nothing whenever a name had two live
  candidates. It now resolves each ambiguous name per-franchise, from that
  franchise's own position-and-club paperwork matched against the universal
  MLB stats spine — so the two Will Smiths and the three Luis Garcias land on
  the right rosters. What the evidence genuinely can't decide goes to a
  human-owned override seed.

## The result

`attribution_contested`: **0 → 0.** From zero *because the flag itself
miscounted* to zero *because it is true* — every player-game across 26 seasons
credits exactly one franchise. Reconstruction accuracy, measured against the
platform's own published standings, improved across the seasons the work
touched (2023's mean team error 4.4% → 3.9%). The record book now produces the
same answer twice.

## What changed

**Fixed** — record-book non-determinism; ~408 transaction rows lost at capture
seams; double-rounded and unstable record values; the walk-back's
effective-date ordering (plus a companion row-order inversion); ambiguous-name
identity; a slugger's season rendered under a minor-leaguer's misspelled name.

**Changed** — franchise identity resolves through one season-grained
dimension; the CBS almanac gains a byte-diff golden (the test that caught all
of the above); identity becomes a reusable, platform-general, id-first
resolver keyed on MLBAM rather than names.

## Scope

PATCH, not minor — everything corrects existing surfaces rather than adding
new ones. ESPN is untouched by the work itself; a matching ESPN renderer
rounding fix (MLB-123, the same class fixed here for CBS) lands alongside,
keeping both books' rounding consistent. On the current two-season ESPN data
it changes nothing visible — a preventive hardening that pays off as the
league accumulates seasons.

## Toward 2.0

1.5.1 hardens the CBS record book that 2.0's portability core will ship to
strangers. The more correct and deterministic the reconstruction, the more
trustworthy the "clone it and run it against your own league" promise at the
center of the 2.0 story.
