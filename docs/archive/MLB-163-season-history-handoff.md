# MLB-163 handoff — CBS Season History + the history-tab move

Branch `mlb-163-season-history`, four commits, **not pushed**. Both books
dev-rendered. Landed 2026-08-05.

Lands in `docs/archive/` per the MLB-154 root-curation rule: session
handoffs get a `docs/` home, never a tracked root file.

---

## 1. What shipped

**New CBS tab: Season History.** ESPN's Matchup History, re-grained. That
tab is one row per team per matchup; this league scores season-long and
never plays one, so the row is a team-SEASON and the W-L pair becomes
**Outscored / Outscored By**. An F-class adaptation for the MLB-160
ledger — the analog exists, the grain differs by necessity.

395 rows, 25 closed seasons (2001–2025), newest first, ranked within
season. 39 columns:

```
Season | Franchise | Owner(s) | Finish | Margin | ·
H 2B 3B HR XBH TB R RBI SB B_BB | ·
W QS K SV HLD CG IP ER P_H P_BB | ·
Hitting Points | Pitching Points | Total Points | ·
Outscored | Outscored By | Ties | ·
League Avg Hitting | League Avg Pitching | League Avg Total
```

`·` marks the blank buffer columns. Stat blocks are the league's scored
10-and-10, in the Records tab's box-score order (`_HIT_ORDER`,
`_PIT_ORDER + _NEG_ORDER`), so a stat sits in the same place in both
tabs. A league that scores different categories renders different
columns from the same code.

**Both history tabs moved to the end of their books**, after the team
tabs, appendix style. CBS Season History renders at index 20; ESPN
Matchup History moved past the team block. Separate commits — the ESPN
move touches a shipped book and is revertable on its own.

---

## 2. Requirement #1 — the awarded lens

Kyle ruled the primacy question on 2026-08-01: reconstructed everywhere
except where it decides a canonical outcome, and *"which team had the
most points in their season"* is exactly that.

**Quantified before building, because the instruction reads like
pedantry and isn't.** Reconstructed vs awarded, over
`mart_team_points_reconciliation`:

| measure | value |
|---|---|
| team-seasons where the rank differs | **307 of 395** |
| seasons affected | **25 of 25** |
| champions who would *not* come out on top | **15 of 25** |
| mean absolute delta | 612 pts |
| max absolute delta | 3,349 pts |

A reconstructed Season History passes every invariant, renders
perfectly, and is simply not this league's past. Nothing in the build or
the goldens would have said a word — the same invisible-absence class as
the blank MLB-team column and the `####` baked into the goldens.

**The tab carries two lenses, deliberately** (Kyle, 08-05):

- **Awarded** — Hitting / Pitching / Total Points, Margin, Outscored,
  Outscored By, Ties, and the league averages. Straight from
  `stg_cbs__ui_standings` via `get_historic_finishes()`.
- **Reconstructed** — the stat blocks. No platform ever published a
  team-season stat line here; the year-end standings page carried points
  and nothing else. Not a breach of MLB-148: its exception is scoped to
  canonical outcomes, and a stat line is context.

### Reconciliation, shown

`TestAwardedLensReconciliation` (warehouse-marked, 7 tests). Rows are
matched by the builder's **documented ordering** — season descending,
then `standings_rank`, then `franchise_id` — deliberately *not* by the
Franchise label, since the builder disambiguates some labels (§5) and a
test that redid that resolution would assert its own copy of the logic.

- Every awarded cell on all 395 rows equals CBS's published value:
  Hitting Points, Pitching Points, Total Points, and Margin (the
  re-signed `points_behind`).
- Display rounding is lossless — asserted, not assumed. All 395 rows are
  integral in all four numeric columns, so whole-number display loses
  nothing.
- The awarded trio still adds up on every row with the reconstructed
  stat blocks present, so a future change to the stat aggregation can
  never silently move a points column.
- 2001–2003 stat cells are populated (§4).
- No season renders two identically-labelled rows (§5).

---

## 3. The invariant, including the ties case

**`Outscored + Outscored By + Ties = N − 1`** — never
`Outscored + Outscored By = N − 1`. Kyle called that trap before the
build existed, and **the data contains it**: 2024 has two teams on
exactly **9156** — Finger Lake Veronicas and Betty White Sox — and CBS
itself awarded them **joint rank 3**, with the next team at rank 5. Each
reads **12 – 2 with one tie**; 12 + 2 = 14, and only the tie makes 15.

Tests that pin it:

| test | what it proves |
|---|---|
| `test_holds_without_ties` | invariant at N = 2, 4, 12, 15, 16 |
| `test_holds_with_ties` | invariant survives a tied season |
| `test_naive_check_would_fail_on_the_tie` | the naive check flags **exactly** the two tied rows and nothing else — the trap asserted as a trap |
| `test_holds_on_every_rendered_row` | invariant on the built tab, checked against each season's **actual field size** rather than a displayed column, so a row leaking across a season boundary fails |
| `test_the_invariant_holds_across_every_real_season` | all 395 real rows, 25 seasons |
| `test_the_2024_tie_is_present_and_counted_in_neither` | the real tie reads (12, 2, 1) at 9156 — if a future re-parse breaks it, the designed-for case doesn't vanish silently |

**Comparison is exact and order-independent by construction.** The values
are the platform's own single awarded total — no summation, so nothing
for float error to enter through and no tolerance to justify. Each team
is counted against the whole field rather than against neighbours in a
sort, so nothing inherits row order. `test_near_miss_is_not_a_tie` and
`test_counts_do_not_inherit_row_order` pin both. A null total raises
rather than silently counting as a loss.

---

## 4. The correction that reshaped the tab

I told Kyle this tab could not carry a stat line, because
`stg_cbs__player_season_stats` starts in 2004 and 2001–2003 "would be
blank regardless." **That inference was backwards** and he rejected it
from domain knowledge.

That table is the *start-share* source — the input to the shortcut
method — so its absence before 2004 is precisely why those seasons got
the real day-by-day reconstruction instead. The warehouse says so:

| seasons | `reconstructed_day` | `estimated_startshare` |
|---|---|---|
| 2001 / 2002 / 2003 | 69.7% / 62.3% / 99.6% | **0%** |
| 2004–2020 | 0% | 85–92% |
| 2021–2025 | 96.7–98.5% | 0% |

And the stat lines were never reconstructed at all — they are real MLB
game logs. Only the fantasy state around them is inferred.

`test_the_pre_2004_seasons_are_not_blank` now guards it, because that
regression would look like tidy missing data rather than a bug.

### Residual, surfaced rather than hidden

2001–2002 stat blocks run **~20–25% light against their own awarded
points**. Total bases per hitting point, by era:

| era | TB / hitting point |
|---|---|
| 2001 | 0.397 |
| 2002 | 0.412 |
| 2003 | 0.513 |
| 2004–2019 | 0.535–0.585 |
| 2020 (short) | 0.443 |
| 2021–2025 | 0.510–0.522 |

The mechanism is Kyle's: 2001–02 are the no-anchor era (year-end roster
snapshots begin in 2003), every stint is derived from the transaction
log, and **a change log cannot see a player who never changed**. Those
never-transacted stars land in the sentinel franchise, which is fenced
out of team aggregates exactly as the Records tab fences it. 2003 is
already in the normal band.

**Consequence for the reader:** under all-time grading, 2001–02 stat
cells paint red for a reconstruction limit rather than for anything the
teams did. The points columns beside them are unaffected. Worth a
sentence in the user guide if it bothers anyone.

---

## 5. The 14/17 fork — found while reconciling

Reconciliation failed at 390 rendered keys vs 395 source rows. Cause:
**"Bent Slides" is the canonical name of two genuinely distinct
franchises** (14 and 17), and both were in the league **2004–2008**, so
those five seasons rendered two identically-labelled rows.

| season | ids | published names that year |
|---|---|---|
| 2004 | 17 / 14 | Syracuse Stars \| Bent Slides |
| 2005–2008 | 17 / 14 | Hit-and-Rum \| Bent Slides |

Resolved by falling back to **that season's own published name** for the
clashing rows — real data, not a guess. Deliberately *not* fixed by
inventing a lineage row or a suffix: which canonical name each fork
should carry is the open historian call (the 14/17 + 26/31/32 question),
and this tab should not be what decides it. The fallback fires only on
the clashing season; elsewhere the franchise still reads canonically.

---

## 6. Highlighting (Kyle ruled from the first render)

Matchup History's rules verbatim, reusing the same helpers so a mark
means the same thing in both books.

- **Three-stop polarity scale** (min / median / max) on 29 columns:
  both stat blocks, the points trio, Outscored, Outscored By, and the
  three league averages.
- **Scaled all-time**, one rule per column across every season. Kyle's
  reasoning: this tab is the points-LEAGUE format surface, not a
  this-league surface, so the default has to be the one that extends to
  somebody else's points league. Era-aware scaling would be a
  league-specific tweak.
- **Margin** is two-stop **red → white** — every value is ≤ 0, so there
  is no positive half for a three-stop scale to describe.
- **Reversed** for fewer-is-better: Outscored By, and the negative
  pitching stats ER / P_H / P_BB.
- **Ties gets nothing**, by ruling — the one numeric column with no
  polarity.
- **Gold on all-time records**, ESPN's rule kept as-is: bold always,
  gold only for a sole holder. On real data that gilds 20 sole records
  and bolds the 23 rows sharing the Outscored ceiling of 15 without
  gilding any — the rule working, not failing.
- Never marked: Margin and Outscored By (best value is 0, every champion
  holds it — a record 25 rows wide is not a record) and the negative
  pitching stats (most earned runs allowed is not an achievement).

**Known artifact of all-time scaling on points**, since it was chosen
deliberately: totals aren't comparable across eras, so 7 cellar finishes
outrank the all-time median on points while one champion falls below it
— 8 rows of 395 paint against their finish. Per-season scaling is a
one-line change if it ever reads wrong.

---

## 7. FOR THE CEREMONY — declared list

Three declared deltas. **No golden re-anchor here; the ceremony owns
it.**

| file | delta |
|---|---|
| `tests/fixtures/cbs_almanac/Season-History.tsv` | **NEW** — 400 lines (5 header + 395 data), 39 columns |
| `tests/fixtures/cbs_almanac/Home.tsv` | +1 nav row: `Season History` / *Every team-season since 2001, as awarded.* |
| `tests/fixtures/almanac_v1_1_0/Home.tsv` | nav row `Matchup History` moved from position 3 to last |

### Known drift to record, not fix

The CBS byte-diff also reports every other tab differing. **None of it is
from this branch**: identical line counts throughout, values only, plus
one committed caption change (`Acquired = production in your lineup`)
that the local corpus predates. The CBS corpus is untracked and local-only
(MLB-95), so its staleness can't be dated from git.

### The byte-identical claim, checked

The addendum called tab order presentation and said values in every moved
tab must be byte-identical — a checkable claim. Checked by rendering the
2026 Week 7 anchor before and after the ESPN move:

- **18 of 19 tabs byte-identical**, `Matchup-History.tsv` included
  (md5 `88758618e0099653690775cb024c89f1` on both sides).
- The 19th is `Home.tsv`, and its change is a **pure reorder, not a
  rewrite**: right band byte-identical, left band the same set of rows in
  a different order.

### Docs pass — flagged, not edited

`docs/user-guide/01-reading-the-almanac.md` lists tabs in the old order
(Home, Records, Advanced Standings, Matchup History, Trades, Draft
Recap, Team Tabs) and has **no Season History section at all**. Both
books now end with their history tab. Resequence + add the section in
the ceremony's docs pass.

---

## 8. Gate

| check | result |
|---|---|
| `dbt parse` | clean |
| pure suite | **342 passed**, 2 failed |
| warehouse (`test_cbs_season_history_tab`) | **7 passed** |
| A/B on changed models | **n/a — no dbt models changed** (`git diff --name-only 156e080..HEAD -- dbt_league/models/` is empty) |
| MLB-179 DuckDB exit-139 flake | not encountered |

The 2 failures are `tests/test_records_report.py::test_zero_rare_event_record_renders_none_yet` and
`::test_zero_non_rare_event_record_keeps_count_collapse` — Kyle's
untracked WIP file, known-failing per CLAUDE.md.

Branch diff: `output/cbs_almanac_sheets.py`, `output/almanac_write.py`,
`output/almanac_logic.py`, `tests/test_cbs_season_history_tab.py`. No
dbt, no seeds, no goldens.

---

## 9. Open calls and follow-ups

1. **Owner(s) is blank before 2007.** `dim_team_owner` covers ~12% of
   team-seasons for 2001–2006 and 100% from 2007. A data floor, not a
   rendering choice. Backfilling it would fill 6 seasons of the column.
2. **In-flight season excluded**, ruled. Kyle's logged alternative —
   pace the partial season and asterisk it — is not built. Advanced
   Standings already computes season-equivalents from gameplay days, so
   the machinery exists if it's ever wanted.
3. **The 14/17 canonical-name call** stays open (§5). This tab works
   around it; it does not resolve it.
4. **Writer refactor, done in passing:** three separate *negated*
   dashboard-tab tuples decided freeze depth, column widths and unmerge.
   A new league-wide tab left out of them silently rendered with TEAM-tab
   treatment and still looked plausible. Collapsed into one
   `_LEAGUE_WIDE_TABS` list — add new tabs there.
5. **Per-season scaling** for the points columns, if the era artifact in
   §6 ever reads wrong.
