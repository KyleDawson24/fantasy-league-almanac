# MLB-63 Walk-Back + Almanac v2 — Live Progress Log

## ALMANAC V2 (2026-07-13, per Kyle's re-sequenced approval — union first)

- [x] V2-A: **Fielding sweep** — `mlb_stats.py --fielding` (yearByYear
      group=fielding, one call/player over the 3,852-id crosswalk
      population, idempotent) launched DETACHED; log:
      `data/mlb_stats/fielding_extract_20260713.log`.
      **BONUS DISCOVERY that saved a second sweep: the gamelog files ON
      DISK already carry per-game `positionsPlayed`** (the loader just
      never projected it) — so the "10 games this year" achievement
      DATES need no re-fetch: new loader family `gamepos` re-walks the
      same files → RAW.MLB_GAME_POSITIONS (1,931,131 rows / 3,855
      players, loaded). Season-grain fielding remains the authority for
      totals + pre-league seasons (the 2001 season's "20 games last
      year" reads 2000).
- [x] V2-B: **Shared eligibility model** — `int_cbs__eligibility_windows`:
      the captured rule verbatim ("primary position, plus positions
      played 20 games last year or 10 games this year"; "Everyone is
      eligible at DH") as date-scoped AFTER-ACHIEVEMENT windows
      (primary + prior-year-20 from opening day; in-season-10 from the
      10th game's date, inclusive). Primary = DERIVED estimator
      (prior-season argmax fielding games, current-season fallback) —
      CBS serves no historic primary label; graded vs the 2026 captured
      per-day eligible_positions (grade below). DH-for-all = slot
      semantics, not stored windows; arrays floor to ['DH'] (matching
      CBS's own display for fieldless hitters — and the Ohtani canary:
      captured 900='DH' / 901='P', which the crosswalk scope guard
      reproduces exactly).
- [x] V2-C: **MLB-72 union layer LANDED** — `int_cbs__player_daily`
      (CBS day-grain: attribution × engine, game→day aggregation, the
      eligibility arrays, franchise names) UNION ALLs into
      `int_player_daily`; the shared fact family
      (fct_player_daily_performance → fct_player_position_pts) now
      serves both leagues. Shared columns added both branches:
      player_key (the cross-league grain — ui-only synthetics have no
      numeric id), game_date, active_weight, provenance. New lenses on
      fct_player_position_pts: weighted_active_pts (the CBS Best-Lineup
      axis; ≡ active_pts wherever state is known) + rostered_pts (the
      bench-ranking axis). Weekly facts take a matchup_period-is-not-
      null guard (weekly grain needs platform periods; no-op for ESPN).
      ESPN byte-neutrality verified: unit suite + almanac byte-diff
      goldens (results below).
- [x] V2-D: **Career-totals mart** — `mart_player_career_records`
      (MLB-69 accumulation axis over int_cbs__player_season_stats,
      top-10 per stat, seasons-span columns; sibling conventions of the
      season book).
- [ ] V2-E: **Renderer rebuild** (`output/cbs_almanac_sheets.py`) per the
      approved blueprint — nav-first Home (#gid links, glossary,
      All-League boards Season + All-Time), Records = best season ×
      career, Standings = 2026 arc + 25y finishes, team pages = Best
      Lineup current × all-time (slot template C/1B/2B/3B/SS/OF×3/DH/U/
      P×9) + bench by rostered points; ACTIVE-franchise tabs only;
      franchise_id-keyed with the aggregation isolated for the MLB-64
      owner re-key.
- [ ] V2-F: Dev render (`--league cbs-bsb`) for Kyle's eyeball — the
      merge gate.

### Eligibility grading (derived rule vs CBS's own 2026 captures)

22,996 player-days compared (every captured roster-day with a priced
game). First pass: 76.7% exact-set agreement — and the decomposition
showed the misses were nearly all ONE thing: CBS lists DH only when
it's a player's SOLE position (universal otherwise-unlisted), while we
listed earned-DH alongside other positions (4,011 of the 4,054
over-grant entries were exactly that). Fixed the array to CBS's display
semantics (DH-unless-other-positions). Ohtani canary exact: 900 →
['DH'], 901 → ['P'], matching CBS's cards. Post-fix re-grade: see the
number below (re-run after the DH fix rebuild). The residual
under-grant tail (~1,670 position-days: 1B 447, SS 362, OF 294, 2B 280,
3B 262, C 24 — CBS granted, we didn't) is the primary-estimator +
counting-timing class; SS 43 over-grants the reverse. Small enough that
Best Lineups are barely sensitive; listed as question #6.
**POST-FIX GRADE: 93.26% exact-set agreement** (21,446 / 22,996
player-days); the rest is the under-grant tail above.

### ESPN byte-neutrality (the MLB-72 gate)

Unit suite 210/210. Warehouse goldens: BBCode records/recap EXACT;
almanac byte-diff EXACT on every tab except TWO cells in Advanced
Standings' acquisition block — both VERIFIED dead-center rounding
boundaries (HANG traded-away-active raw = 382.75 exactly; CYCL
traded-away-rostered raw = 443.05 exactly): the mart rounds sums whose
float accumulation order changes on any table rebuild, so those two
cells coin-flip per rebuild — the same verified-ROUND-boundary class
the MLB-57 landing re-anchored. Fixture re-anchored (one line: CYCL);
byte-diff green against it.

### Almanac-v2 questions for Kyle (running list)

6. **Primary-position estimator**: using fielding-argmax (last season,
   else this season) for "primary position", NOT the year-end anchors'
   primary_pos label (name-keyed, year-END timing, ambiguity class) —
   the 2026 grading below measures how close that gets to CBS's actual
   grants. Happy to switch to anchor-primary (or blend) if the grade
   says otherwise.
7. **Estimated-era Best Lineups are weighted**: 2004-2020 lineup slots
   fill by weighted_active_pts = points × start-share estimator
   (est-membership rows with NO estimator contribute 0 — conservative).
   The team-page fidelity label states the era's provenance mix.
8. **Rules-capture oddity** (no action needed for the almanac): the
   2026-07-08 rules payload says add/drops DISABLED + trades NOT
   allowed + weekly lineups — while 1,325 moves happened this season.
   Likely a mid-break freeze snapshot (worth re-capturing at rollover
   so the archived rules read true).
9. **MLB-72's one unmet acceptance line** (Linear not updated from this
   session — comment/flip when you review): "fct_team_season_performance
   grows CBS team-season rows" is deliberately NOT met. That fact builds
   through the weekly-active chain, which is period-keyed — CBS history
   has no periods. CBS team-seasons live in
   mart_team_points_reconciliation (already graded vs official
   standings). If you want them in the shared spine too, the clean route
   is a day-grain branch in fct_team_season_performance parallel to the
   weekly one — a small follow-up ticket if you call for it.


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
- [x] D: COVERAGE EXTENSION — **LANDED. 2003-2019 collapsed from
      80-99% error to 5-13% mean absolute error** (2008: 5.1%, 2003:
      6.6%). 1.2M new gamelog rows; the record book is now era-complete
      (Bonds 73 HR '01 and Randy Johnson 372 K '01 take their thrones;
      Big Unit's 2002 = 1,142 is the all-time fantasy season).
- [x] Ship: schema docs + grain/enum tests for all six walk-back models
      (33/33 green), catalog, Linear, handoff

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
4. **(SUPERSEDED 2026-07-13 — my "two systematic residuals" story was
   half wrong, and your skepticism found the truth.)** Your questions
   forced a discipline-split decomposition (hitting vs pitching, each
   vs the UI standings' own split), which revealed:
   - The 2021-22 "overshoot from lossier logs" was mostly **MY BUG**:
     the stint-pairing lead() windowed over acquisitions only (the
     WHERE ran before the window), so NO logged drop ever closed a
     stint — every dropped player's FA-period games kept crediting
     the old team (streamed pitchers = the churn class, which is why
     it looked pitching-heavy). Fixed; the lineup era now grades
     2.4-4.7% mean abs (was 2.4-10.8), and the "lossy on drops"
     census collapsed from ~8,000 flags to **33 true missing
     departures** in 25 years. The log is nearly complete.
   - The residual that SURVIVES the fix: **2021-23 official PITCHING
     runs ~8-11% below reconstructed pitching while hitting tracks
     within ~3-5%, and both disciplines converge in 2024-25.** Our
     credited pitching is flat across years; the OFFICIAL side
     step-jumped +~550/team in 2024. That's the signature of a
     team-level pitching cap (max games/innings) that was removed
     for 2024 — the current rules show `max_total: "No Limit"` on
     every slot, so the knob exists. **QUESTION FOR YOU: did the
     league have a max-games/innings-pitched cap through 2023?**
     If you can confirm (or check the rules page's year switcher,
     like the rosters), we could even model it per-era and collapse
     the 2021-23 deltas.
   - Start-share era stands, with finer texture: roughly unbiased
     2005-2010, undershooting ~8-13% from 2011 on.
5. **The 33 + 18 residual flags decoded (2026-07-13): the dominant
   pattern is MIRROR-PAIR TRADES** — the two lists share names on
   swapped teams with matching dates (Hardy↔Hawpe 7/14/08,
   Kemp↔Bumgarner 8/16/10, Utley↔Morneau 6/9/14, Reed↔K-Rod
   7/28/03...). The 2003 case study shows the log carrying the SAME
   trade in BOTH directions with two effective dates (7/21 + 7/28) +
   double activations — a swap-and-swap-back (rental?) or re-done
   deal. The machine can't know which leg wins by date alone, so it
   flags rather than guesses. DO YOU REMEMBER these — were they
   vetoed/reversed trades, or one-week rentals? Your read decides the
   policy (e.g. void-detection when A→B and B→A legs coexist). The
   remainder are true log silences (Machado '12, Vlad Jr '23 class).
   Full lists: `missing_departure` / `anchor_reopen_needed` flags in
   int_cbs__roster_stints.

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
- D LANDED: 16,732 gamelog season-files swept; 1,203,556 rows loaded
  (MLB_GAMELOGS now 1.8M rows / 3,855 players). Crosswalk staging
  admits the UI population under synthetic 'ui-' ids (only mlbams the
  real crosswalk lacks); full downstream rebuilt 45/45 green.
- **THE FULL-ERA REPORT CARD** (mean absolute error vs official
  standings): 2003-2019 = **5-13%** (was 80-99% before D); 2021-2025 =
  2.4-10.8%; 2001-2002 = ~80% (no roster anchors exist — log-only,
  graded honestly); 2020 = 21% (COVID short season, thin log).
- Phantom-identity check on 2021 (error rose 7.5%→10.8% after D):
  NOT phantoms — the new contributors are Wainwright/Cruz/Posey-class
  stars who retired before 2026, invisible to the FA-only archive, so
  the UI population is their only route in. Legitimate coverage
  unmasked active-state generosity that missing coverage had been
  cancelling out.
- RECORD-BOOK FLOOR FIX: the season floor was the platform archive's
  min (2004) — a pre-UI-history proxy that walled off 2001-2003.
  Now floored by the league's own first season per the UI standings
  (2001). Bonds 73 HR / 867 hitting pts (2001) and Randy Johnson 372 K
  / 1,142 total pts (2002) now lead the book, as they should.
- SHIP: schema docs + tests for all six models (grain uniqueness,
  provenance/state enums, key not_nulls) — 33/33 green.
- **PAIRING FIX (2026-07-13, prompted by Kyle's error-decomposition
  questions)**: `paired`'s lead() now windows over BOTH event kinds
  before the acquisition filter — logged drops/trade_outs actually
  close stints (8,473 + 658 of them; close_type was previously never
  'drop'). Wheeler's "missing departure" was a logged 8/24 drop the
  old code ignored. Report card after: **2003-2019 = 5.1-13.5%,
  2021-25 = 2.4-4.7% mean abs**; anchor audit 99.83% (the 18
  uncovered = real anchor_reopen_needed log gaps, previously masked);
  missing_departure census 33 total. Remaining 2021-23 delta is
  official-side pitching suppression (suspected cap — see issue #4).
- **ROUND 2 OF KYLE'S DECOMPOSITION (2026-07-13): two more fixes.**
  (1) ANCHOR-ARBITRATED TRADE VOIDS — the mirror-pair flags were
  vetoed/reversed swaps the report still renders (2003 Reed/K-Rod: the
  same deal logged BOTH directions under two effective dates). Rule:
  the player's final trade leg is voided when the receiver's anchor
  lacks him and the sender's holds him; genuine rentals never match.
  (2) SUFFIX NORMALIZATION in cbs_name_key — the roster report drops
  Jr/IV where the transaction report keeps it, splitting one player
  into two identities: 2023 Vlad Jr's Meteors half attributed to
  NOBODY. Post-fix his 2023 reads true: Meteors 3/30→7/10
  (opening→trade_out), Kimball Drives 7/10→10/1 (trade_in→anchor). Also
  a phantom-departure guard (a derived trade_out can't synthesize an
  opening when the player demonstrably lived elsewhere earlier).
  **Census now: 22 missing departures + 9 anchor-reopens in 25 years;
  19,992 stints; lineup era grades 4.2/3.9/3.3/2.3/2.1% (2021→2025),
  +1.0 mean signed.** ui- ids renamed ui-only- (Kyle: clearer that the
  prefix marks identity-provenance). IRSTR wobble pattern for Kyle:
  all top 2023-25 diffs are relievers at +2/+3 with OUR side higher —
  initially suspected pass-through double-crediting — REFUTED
  2026-07-13 by CBS's own per-game IRPCT strand rates: Milner 2025
  agrees with our IR/IRS on all 30 inherited-runner games, so CBS's
  SEASON key disagrees with CBS's OWN gamelogs. The wobble is
  internal to their season aggregation; distribution 2023-25 =
  1,110 exact / 81 at +1 / 26 at +2 / 5 at +3 / 4 at −1.
- BONUS — MLB-54's dual-source verify, run NOW instead of at rollover
  (the API snapshot + UI capture already overlap): 2026 swept
  full-season via start_row (1,325 moves / 45 pages), API log
  full-outer-joined vs UI on (franchise, player id, move type,
  effective date) → **746/748 exact, zero UI-only**. The 2 misses are
  one pre-season trade the UI report structurally omits (Torkelson +
  Early, 3/25) — the walk-back's opening-recovery already absorbs that
  class. UI pipeline verified end-to-end; MLB-54 and MLB-63 both
  flipped Done in Linear with full result comments.
