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
- [x] B1: single-rostering truncation (departure-day exclusive; receiver
      owns the effective date) + lineup intervals WITH THE BACKWARD HALF
      (state before a player's first lineup event = the INVERSE of that
      event; zero-event players hold their anchor status)
- [x] B2: `fct_cbs_player_game_attribution` — every priced game
      franchise-attributed with per-row provenance (captured /
      reconstructed_day / estimated_startshare / estimated_membership) +
      state_source + ambiguity/contested/inferred-end flags; one
      attribution per game guaranteed (position-aware tie-break)
- [x] C1: `mart_team_points_reconciliation` — reconstructed vs OFFICIAL
      standings, 25 seasons × 16 teams
- [ ] D: COVERAGE EXTENSION (new critical path — see log) — identity +
      gamelog extract for the UI-history population, then re-measure
      2003-2020
- [ ] Ship: schema docs + grain tests for the five new models, catalog,
      Linear, handoff (deferred to post-D — the models churn again)

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
- B+C built. FIRST report card looked terrible (-30% to -99% per team)
  and decomposed into TWO separate causes via the rostered-lens column:
  (1) modern era = WEIGHT loss — set-and-forget starters have no lineup
  events, my forward-only intervals defaulted them to reserve; (2)
  pre-2016 = COVERAGE starvation — the universal gamelog layer only
  holds crosswalked players (the 2004+ archive population), so
  early-era rostered players who retired before ~2015 have NO games.
- FIX (modern): the backward state half — prior-inverse intervals +
  anchor-hold. **Modern era now reconciles at 2.4–7.5% mean absolute
  error** (2025: 2.4%, official 8,690 vs reconstructed 8,750). The
  walk-back is VALIDATED where coverage exists.
- NEW CRITICAL PATH (D): extend identity + gamelogs to the UI-history
  population (~thousands of pre-2015 players from the 10,449 anchors +
  moves) via the proven name+season+team machinery, re-run the engine,
  re-measure 2003-2020. This is another extract sweep (detached).
- ISSUE #4 for Kyle: none of this needs you — but note the 2021 mean
  error (7.5%) runs slightly hotter than 2025 (2.4%): older lineup
  logs are a touch noisier (retroactive edits, the truncation class).
  The per-season grades will carry it honestly.
- D IN FLIGHT: the UI-history identity pass matched **2,736 of 2,753
  year-end-roster names (99.4%)** to MLBAM ids (index extended to 2001;
  the 17 stragglers are the rename class — Fausto Carmona, Leo Núñez,
  Melvin Upton — flagged, not guessed). The gamelog extract for the
  extended population (3,852 total ids; ~1,600 genuinely new) is
  RUNNING DETACHED:
  `Get-Content C:\Users\kyled\projects\espn-league-manager\data\mlb_stats\ui_extract_20260712.log -Wait -Tail 5`
  When it lands: mlb_load → rebuild the engine chain → re-measure
  2003-2020 in mart_team_points_reconciliation.
- D PACING FIX: the first hour showed ~10h projected — early-era
  veterans carry 15+ seasons and most calls were PRE-LEAGUE games that
  can never attribute. mlb_stats gained --min-season 2001; sweep
  restarted (idempotent — the hour already landed stays), now fetching
  only league-era seasons per player. Projection ~2-3h.
