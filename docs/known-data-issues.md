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
