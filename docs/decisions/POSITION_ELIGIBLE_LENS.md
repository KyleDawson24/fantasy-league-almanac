# The position-eligible active-points lens (ruling 2026-08-14, MLB-243)

**Status: RULED. Foregrounded in v1.9, required for 2.0.**

## The ruling

**Home, Records, the best-team boards, and ordinary player-by-position
leaderboards all use ACTIVE POINTS WITH ELIGIBILITY AT THE NAMED
POSITION.**

**Actual deployed slot matters only for explicitly slot-based analysis** --
Points by Lineup Slot, Score by Slot, and anything else whose subject *is*
where a manager put somebody.

## What went wrong that produced the ruling

The first stranger workbook named two different players as the league's
best catcher on two different tabs, and both numbers were right.

| | started at 1B | started at C | C-eligible active |
|---|---|---|---|
| Ben Rice | **311** | −4 | **322** |
| Liam Hicks | 6 | **292** | 304 |

* **Home → Team of the Season** ranks `fct_player_position_pts`: the best
  player *eligible* at C. That is Ben Rice.
* **Records → the slot section** ranks
  `fct_player_weekly_slot_performance`: the most points scored *while
  actually started* at C. That is Liam Hicks.

It surfaced only at catcher, and the reason is instructive: the optimizer
put Rice at C precisely *because* Bryce Harper outproduces him at 1B
(341 > 311). Everywhere else the best-eligible player and the actually-
deployed player are the same person, so the two lenses agree and the
disagreement stays invisible until a manager plays someone out of
position.

## Why the lens is the eligible one

A record book answers "who was the best catcher this year". A manager
choosing to bat his catcher at first base is a fact about the manager, not
about the player's season. Ranking by deployment lets a roster decision
silently rewrite the league's history, and it punishes exactly the players
whose flexibility was most useful.

Deployment is still a real and interesting question. It is just a
different one, and it belongs on a tab that says so.

## v1.9: stated once, not unified

Rewriting the data path was judged too risky for a release already cut.
Instead the points book states the distinction **in one caption**
(`almanac_data.LINEUP_SLOT_LENS_CAPTION`), sitting between the section
heading and the column header so a reader meets it before the first name.
The write layer paints it with the house explainer token, so it reads as
the caption it is rather than as a stray record row.

**The section keeps its normal name in both books.** A first pass renamed
it to "Production by Actual Lineup Slot" and suffixed every row label with
"(as started)"; Kyle reverted both on 2026-08-15. One section of a shared
record book should not be titled two different ways depending on which
league is reading it, and eighteen parenthetical row labels shout a caveat
the reader needs once. What survives is the caption, and it is carried by
the points book only -- the head-to-head record book is pinned byte for
byte by `tests/fixtures/almanac_v1_1_0/Records.tsv`.

The same clarification is owed to the H2H book and should land with the
unification rather than as a second unreviewed byte change.

## 2.0: the required change

Unify every non-slot-based surface onto position-eligible active points:

1. Audit each consumer of `fct_player_weekly_slot_performance` and decide,
   per surface, whether its subject is the PLAYER (→ eligibility) or the
   SLOT (→ deployment). The slot-based keepers are Points by Lineup Slot
   and Score by Slot.
2. Re-point the record-book slot section at `fct_player_position_pts`,
   which already carries per-position active points at the right grain.
3. Delete `LINEUP_SLOT_LENS_CAPTION` once the surfaces agree -- it exists
   to explain a discrepancy that should no longer exist.
4. Re-anchor the ESPN H2H goldens deliberately, with the diff reviewed:
   this WILL move record holders in the pinned corpus, and that movement
   is the point rather than a regression.

Related: [RIVALRY_MATRIX_CONTRACT.md](RIVALRY_MATRIX_CONTRACT.md) for the
other place a format-conditional section states its own limits.
