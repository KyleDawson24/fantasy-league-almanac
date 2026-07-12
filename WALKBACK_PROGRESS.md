# MLB-63 Walk-Back — Live Progress Log

Watch this file for real-time state:
```powershell
Get-Content "C:\Users\kyled\projects\espn-league-manager\.claude\worktrees\modest-montalcini-3af8c4\WALKBACK_PROGRESS.md" -Wait -Tail 30
```
It is committed at every checkpoint, so the branch on GitHub mirrors it.

## Plan (three blocks)

- **A. Membership stints** — identity dictionary (name → CBS id from the
  league's own id-bearing rows), then stint assembly: acquisition edges
  (add/trade_in) + departure edges (drop/trade-out) + the year-end anchor
  closing each season. Players on the anchor with no in-season
  acquisition = the OPENING ROSTER, recovered implicitly (drafts were
  never logged; the anchor-backward design exists exactly for this).
- **B. Active state** — lineup-era activate/reserve/slot intervals within
  stints (2001-2003, 2021+); est_start_share weighting for 2004-2020;
  per-game attribution fact joining the calculated lens, with Kyle's
  per-row provenance flag (captured / reconstructed_day /
  estimated_startshare / year_end_anchor / ambiguous_identity).
- **C. The reconciliation** — reconstructed team season totals vs the
  OFFICIAL final standings points (25 seasons × 16 teams of ground
  truth): the whole reconstruction graded in one mart. Known systematic
  delta: CBS's sparse pre-2023 IRSTR under-counts the platform side.

## Checkpoints

- [x] A1: identity dictionary model (`int_cbs__player_name_ids` + the
      shared `cbs_name_key` macro; 114 ambiguous-name stints flagged —
      the Will Smith / Luis Garcia class)
- [x] A2: roster stints model (`int_cbs__roster_stints`, 20,003 stints
      2001-2025; openings recovered implicitly; trades as paired
      in/out edges)
- [x] A3: membership self-audits — **100% ANCHOR COVERAGE, all 23
      anchored seasons** (every one of the 10,449 year-end states is
      reproduced by a season-end stint)
- [ ] B1: single-rostering truncation + active intervals (lineup eras)
- [ ] B2: per-game attribution fact w/ provenance
- [ ] C1: standings reconciliation mart + season grades
- [ ] Ship: tests, docs, catalog, Linear, handoff

## Questions / Issues for Kyle (collected as encountered)

1. **The log is lossy on DROPS, in every era** — ~8,000 stints end
   open without a departure record (2,859 players later show up
   acquired elsewhere while their old stint still runs). Verified NOT
   a filter artifact ('all' is a strict superset of 'all_but_lineup',
   87/87 on the dual-source window). Handling: open stints truncate at
   the player's next acquisition elsewhere (a player is on one roster
   at a time), the rest carry `missing_departure` provenance, and the
   Block-C standings reconciliation quantifies the residual. Nothing
   needed from you unless C says the distortion is material.
2. **COVID id discontinuities are a PATTERN**: franchises that sat out
   2020 returned under NEW ids — Foster's Folly 13→30 AND Kimball Drives
   22→28. Both need rows in the MLB-64 continuity-overrides seed
   (worth asking the commissioner if any OTHER 2020 sit-outs exist).
3. (resolved) The `/teams/{id}` link space matches the franchise-id
   space after all — the 2015 'mismatch' was Kimball Drives genuinely
   changing ids across 2020.

## Log

- START. Inputs verified: 52,369 normalized moves / 10,449 anchors /
  25 seasons of finishes.
- A1+A2 built. First audit looked catastrophic (0% coverage) — my
  hand-rolled audit regex was broken, not the model; the dbt-side
  audit (same macro both sides) shows 100% coverage everywhere.
- Missing-departure census: 273 (2001-03) / 4,904 (2004-20) / 2,830
  (2021+). Cross-team overlap: 7,918 stint pairs, 2,859 players →
  Block-B truncation rule.
- CHECKPOINT: Block A committed. Next: B1 truncation + active
  intervals.
