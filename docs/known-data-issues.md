# Known data issues

The permanent log of source-data defects and irreducible discrepancies —
things that are **documented and bounded rather than fixed**, because the
defect lives in a source we don't control. Each entry states the issue,
the evidence, and the disposition the warehouse takes. Model-level detail
lives in the schema docs; this is the cross-referenced index.

Pipeline bugs don't belong here (they get fixed and recorded in the
CHANGELOG); questions still awaiting a human answer are marked OPEN.

---

## 1. CBS's season-grain IRSTR key disagrees with its own per-game data (±3, both signs)

**Status:** closed — root-caused to CBS's season aggregation, 2026-07-13.

**The issue.** The warehouse derives inherited-runners-stranded per game
as `IR − IRS` from the MLB Stats API components (`calculated_` lens).
CBS publishes a *direct* IRSTR key, but only at season grain — and for a
small tail of player-seasons it disagrees with the derivation by up to
±3.

**The evidence chain:**

1. **The derivation uses the sport's own accounting, not an estimate.**
   MLB publishes the pair, not a single "stranded" stat: mlb.com box
   scores print `Inherited runners-scored: Salas, F 2-1; Ondrusek 1-0`
   (e.g. gamePk 317816, STL@CIN 2012-04-11) and Baseball-Reference
   carries the same two components as IR / IRA. Stranded = the
   difference, in their presentation as in ours.
2. **Warehouse spot-check against that exact box score:** our per-game
   table reads Salas 2-1 and Ondrusek 1-0 — digit-for-digit the
   published line.
3. **The disagreement is a small two-sided tail, not a systematic
   rule.** 2023–2025 (the era where CBS's key is densely populated):
   1,110 of 1,226 player-seasons match exactly; 81 differ by +1, 26 by
   +2, 5 by +3 — and 4 by **−1** (CBS higher: Strzelecki 2023 4v5,
   Thornton 2024 17v18, Whitley 2024 0v1, Jorge López 2024 12v13).
   Whitley's is the tell: CBS credits a strand in a season where MLB
   shows zero inherited runners.
4. **The decisive test — CBS against CBS.** CBS gamelogs carry no
   per-game counts, only `IRPCT` (the per-game strand *rate*). For the
   largest 2025 discrepancy (Hoby Milner, ours 41 vs CBS 38), CBS's own
   per-game rates match our IR/IRS-implied rates on **all 30** of his
   inherited-runner games, including the fractional ones (3-2 → 33.33,
   3-1 → 66.67). CBS's own gamelogs therefore imply 41; their season
   key says 38. The season key disagrees with its own components.
5. **A definitional theory was tested and refuted.** Pass-through
   runners (two relievers each "stranding" the same runner under
   IR−IRS, a final-strander key counting him once) would show up in
   the per-game rates. It doesn't — see 4.
6. **Pre-2023 the direct key is sparse to missing**, and the platform's
   own FPTS arithmetic (MLB-62: 0.0 residual on all 8,185 archive
   player-seasons under current weights) proves the points CBS awarded
   in those years embedded the sparse values — the official standings
   side undercounts IRSTR before 2023.

**Disposition.** `calculated_` IRSTR (= IR − IRS) is the lens for the
record book and attribution — two independent sources (MLB's ledger,
CBS's own gamelog rates) side with it wherever the season key drifts.
CBS's direct season key anchors the platform-parity reconciliation
(`mart_player_fpts_reconciliation`) and nothing else. Maximum observed
drift: ±3 strands ≙ ±3 points per player-season.

---

## 2. The UI transaction report structurally omits pre-season trades

**Status:** closed — bounded by design, 2026-07-12.

The 2026 dual-source verify (API transaction log vs the UI capture,
the one year with two independent sources) matched **746 of 748**
player-moves on (franchise, player id, move type, effective date). The
two misses are one pre-season trade (Torkelson + Connelly Early,
effective 3/25) that the UI report never renders — the players' lineup
rows visibly flip franchises at that date, but the trade rows don't
exist. The walk-back absorbs the class: a pre-season acquisition
surfaces as an anchored-no-acquisition season-start opening on the
correct team; only the acquisition-channel label is coarser.

---

## 3. Suspected team-level pitching cap in official 2021–2023 standings

**Status:** OPEN — awaiting league validation (commissioner question).

Official pitching points per team run ~8–11% below reconstructed
pitching in 2021–2023 while hitting tracks within ~3–5%; both
disciplines converge in 2024–25. Our credited pitching volume is flat
across all five years; the official side step-jumped ~+550/team in
2024. Player-level scoring is provably unchanged (MLB-62's 0.0
residuals), so the suppression is team-level accounting — the signature
of a max-games/innings cap removed for 2024. The current rules show
`max_total: "No Limit"` on every position slot, so the knob exists.
If confirmed, the cap can be modeled per-era and the 2021–23
reconciliation deltas should largely collapse.

---

## 4. Residual walk-back flags: 22 missing departures + 9 anchor-reopens (25 years)

**Status:** closed as flags — each row carries provenance; a handful may
resolve with league memory.

After the pairing fix, anchor-arbitrated trade voids (vetoed/reversed
swaps the report still renders), and generational-suffix normalization
(the roster report drops Jr/IV where the transaction report keeps it —
the 2023 Vlad Jr split), the log is nearly complete: 8,473 drops close
their stints. What remains: 22 stints whose departure was genuinely
never logged (the Machado-2012 class) and 9 anchored players whose last
logged word was a departure (including a 2008-09-29 season-boundary
trio). Flags: `missing_departure` / `anchor_reopen_needed` in
`int_cbs__roster_stints`.

---

## 5. Era coverage floors (structural, graded honestly)

- **2001–2002:** no roster-report pages exist, so no year-end anchors —
  membership rides the transaction log alone. Team-level reconciliation
  grades those seasons at ~12-15% error, the same range as 2004-2020 —
  the real cost is coverage, not accuracy: never-transacted players
  (held all season, so invisible to a log-only walk-back) currently
  leave roughly a quarter to a third of true production unassigned,
  parked in the placeholder franchise rather than pretending otherwise.
- **2004–2020:** CBS logged no lineup moves, so daily active state
  rides the Start%/Own% conditional estimator (`estimated_startshare`
  provenance) — roughly unbiased 2005–2010, undershooting ~8–13% from
  2011 on.
- **2020:** COVID short season, 12 teams, thin log (~20% error).
- **COVID franchise-id discontinuities:** franchises that sat out 2020
  returned under new ids (Foster's Folly 13→30, Kimball Drives 22→28) —
  handled by the MLB-64 continuity-overrides design.

---

## 6. ESPN player records carry only a CURRENT MLB club (ESPN side)

**Status:** open — the silent-omission half closed 2026-08-03 (MLB-159);
the mis-attribution half is gated on ESPN identity resolution.

**The issue.** ESPN's box-score payload stamps each player row with the
club on ESPN's *player record* — whichever club he belongs to when the
extract runs — and it applies that stamp **per matchup period**, not per
game. Two consequences follow, at very different scales.

**Forward, it is period-accurate rather than day-accurate.** A live season
tracks club changes to within a scoring week, but the week a player moves
is stamped entirely with his *new* club, so games he played for the old
one are credited to the new one. In 2026, 66 of 1,208 player-seasons
change club and the 75 transition periods carry 161 units of active-slot
weight — **0.12%** of the season.

Even that overstates how fresh the live season's stamps are, and the
correction is worth carrying because it is easy to assume otherwise: a
period is not stamped when it happens, it is stamped by the **last**
lookback pass that touched it. The weekly run re-extracts every completed
period inside its 21-day window, last write wins, so each 2026 period
carries the clubs of a date **0–28 days after it ended** (measured
2026-08-04 across all 17 periods; matchup period 1 ended 04-05 and was
last written 05-03). The 0.19% of 2026 weight where the stored club
disagrees with the club-of-game field is trades that happened inside that
window.

**Backward, a season pulled in one pass gets a single stamp for the whole
year** — the club as of that pull, which is not necessarily the player's
club today. In 2025, **0 of 1,236 player-seasons carry more than one
club**, against 66 in the live season: not one in-season trade is
represented, league-wide. Every mid-season move that year is mis-filed.

ESPN history here is only those two seasons, so the serious defect is
bounded to one of them — real, but not systemic.

**The evidence.** Two impossible rows, found against a hand-built
validation pivot and code-confirmed the same day: Tyler Anderson labelled
`FA` for all 24 of his 2025 starts — a free agent does not face twenty-odd
batters every sixth day, and those were Angels innings — and Sonny Gray's
2025 batters-faced credited to Boston, a club he did not join until the
following offseason. The `FA` bucket is players who were between contracts
when the snapshot was taken, which is the extract moment showing through
rather than a coincidence.

The period-level stamping was then pinned exactly against Baseball
Reference. Curtis Mead moved to Boston late in 2026 and has **1 game, 2
plate appearances** there; the warehouse credits Boston with **20**. The
missing 18 are the games he played for Washington during the *same
matchup period* as the move, relabelled wholesale. The other side
reconciles to the plate appearance: Baseball Reference has him at 327 PA
for Washington, the warehouse at 309 — a difference of exactly 18.

Mead is also the counter-example worth keeping attached: his club changes
are otherwise represented **correctly**, because they happened during the
live season. A spot check that comes back clean is not evidence the column
is sound — it may only mean the player moved in the season that gets
tracked.

**Disposition.** Two defects fall out of one cause, and only one is
closed:

- **Silent omission — CLOSED.** The roster-affinity chart filtered
  `pro_team <> 'FA'`, so production belonging to anyone who happened to be
  a free agent *on extract day* was dropped from the chart rather than
  mis-filed. Measured in the chart's own unit (active-slot plate
  appearances + batters faced): **23,749 of 202,547 in 2025, or 11.7%**,
  against 30 of 129,456 in 2026 (0.02%). That production now renders in a
  visible `Unattributed` band. The label is deliberate — the honest claim
  is that the club is unknown, not that these players were free agents
  when they played.
- **Mis-attribution — OPEN in the data, but now measured, and the fix is
  cheaper than it looked.** Gray's Cardinals innings still sit under
  Boston. What changed on 2026-08-03 (MLB-129 spike) is that ESPN turns
  out to send the right answer already: `proTeamId` sits on each
  per-scoring-period split — the club of *that game* — inside the loop
  `fetch_all_player_stats` already walks. No identity crosswalk, and no
  `scoring_period → date` derivation, is required to read it.

  Against that field, the defect is no longer a floor and a shrug:
  **45,059 of 202,547 units of 2025 active-slot weight — 22.25% — are
  filed under the wrong club.** The `Unattributed` band above is 11.73%
  of the season; the other ~10.5% is silent mis-attribution. Coverage of
  the new field is 100% on both seasons.

  The superseded proxy — 2025 weight whose stamp differs from that
  player's 2026 stamp — gave **13.5%** (27,338 of 202,547) and was
  correctly quoted as a floor. It is worth recording *why* it was one: it
  can only see players who appear in both seasons *and* whose stamp
  moved, so a player mislabelled **consistently** is invisible to it.
  **Gray was not in the 13.5%** — he reads `Bos` in both years while
  having pitched 2025 in St. Louis. The metric systematically excluded
  exactly the cases that are stably wrong. The measured 22.25% is 1.6× it.

  `extract.py` captures the field as `clubOfGame` as of 2026-08-04, but
  **no RAW row carries it yet** — it arrives on 2025 and 2026 only when
  the backfill runs, and the affinity chart does not read it until the
  wave-end flip. Until both happen, everything above describes the data
  as it currently stands.

Note that the chart *looked* wrong before this change and now looks
tidy — the `FA` rows were the tell. The band is what remains of that
tell, which is why its wording matters.

**The hazard this creates (MLB-188).** Because the stamp is whatever ESPN
reports at fetch time, **re-extracting an already-loaded matchup period
overwrites its stored per-day clubs with the clubs of the day you re-ran
it**, and ESPN cannot serve the originals again. There is no earlier copy
in RAW to restore from — 2025 is the proof, not the warning: all 195 of
its rows were written in one ten-minute pass and carry one date's clubs.
Any ordinary reason to re-pull — a gap fill, a corrupted week, a schema
migration — silently converts a live-captured season into a second 2025.

Two things make this survivable, and the distinction matters:

- Re-running **dbt** is always safe. RAW is immutable input and the models
  are deterministic; nothing downstream can destroy a stamp.
- Re-running the **extract** against a settled period is the destructive
  act. `extract.py` refuses it: a period that already holds rows and ended
  more than `LIVE_CAPTURE_WINDOW_DAYS` (21) ago fails the whole invocation
  loudly, naming every offending period, and proceeds only behind
  `--overwrite-day-accurate-history`. Periods inside that window are
  exempt — the weekly run revisits them on purpose, which is how the
  stamps get captured at all, and a guard the routine path had to bypass
  would be bypassed permanently within a fortnight.

To add a **new field** to settled periods, use `--backfill-club-of-game`,
which updates rows in place, assigns only the new key, and preserves
`loaded_at` — the only surviving evidence of when each period was stamped.

**These RAW payloads are temporally irreplaceable** and should be backed
up accordingly (MLB-131). Every other input in this warehouse can be
re-fetched from its source; this one cannot.
