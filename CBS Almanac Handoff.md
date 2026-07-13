# CBS Almanac — Universal-Stats Pivot Handoff (updated 2026-07-12, post walk-back)

Branch: `claude/modest-montalcini-3af8c4` (worktree `modest-montalcini-3af8c4`) —
**the integration line**: main + everything CBS, one straight line (main is its
ancestor). Pushed to origin; branch pushes are routine backups. The finish:
`git merge --ff-only` into main **when the almanac renders** (that QA bar governs
the merge, not branch pushes). Anything landing on main in the meantime gets
merged into this branch immediately to keep the fast-forward property.
⚠ **New sessions spawn fresh worktrees on fresh stub branches.** Do the work in
THIS worktree / commit to THIS branch explicitly (`git -C <this worktree> …`);
`git symbolic-ref -q HEAD` before every commit. If the worktree directory is
missing (cleanup prunes it), recreate:
`git worktree add <path> claude/modest-montalcini-3af8c4` — the branch is the truth.
Run everything **from the worktree**; the raw data lives in the **main checkout**
(`C:\Users\kyled\projects\espn-league-manager\data\...`) — pass `--data-dir`.
Tooling: the `.venv` in the **main checkout** (`...\espn-league-manager\.venv\Scripts\`).

**COURSE CORRECTION (2026-07-12, Kyle's review of the v1 render): the v1
almanac layout was wrong-shaped — REBUILD to mirror the ESPN workbook's
ARCHITECTURE, and the team pages need HISTORIC rosters first.**
Kyle: "if we don't have historic data we don't have anything worth
rendering yet." The plumbing all stands (MLB-62 recompute, record book,
MLB-58 registry sinks, data-presence dispatch, quota-hardened writer);
what changes is the CONTENT assembly and its prerequisite.

**START HERE → parse the captured UI history (the new critical path).
Kyle confirmed 2026-07-12: year-end rosters are the ANCHORS, not the
deliverable — the full chain is MLB-55 (anchors) → MLB-54 (transaction
logs incl. activate/reserve verbs, both filters captured on disk) →
MLB-63 (walk BACKWARDS from each anchor through the season's moves →
day-to-day rosters AND daily active/reserve state, per-season fidelity
grades; contradictions vs the anchor = the log missed something = the
grade says so on the sheet). Historic ACTIVE-lens team pages ride that;
2026 stays capture-based.**
**BOTH CAPTURE GAPS CLOSED 2026-07-12 (later same day):**
- **Early rosters**: the live re-probe showed the ORIGINAL roster-report
  URLs now render league-wide for 2003-2011 (CBS behavior change);
  144-page force re-sweep + parser upgrade (consume the contiguous run
  of sibling data tables — early renders split teams into separate
  tables) → RAW.CBS_UI_ROSTERS = **10,449 rows, ALL teams every season
  2003-2025**. Full anchor set.
- **Transactions**: Kyle found the real pager — `?start_row=N` (bare URL
  = start_row=1, newest first; rows/page varies by year; the old ?page=
  guess is ignored — hence the last-30 tails). `cbs_ui_capture.py
  --transactions-sweep` walks the full history (row detection era-proof:
  ids two-part 2021 / three-part 2015 / ABSENT 2001-2008 → class-based +
  content-hash clamp). 2001 validated: 4,198 moves / 140 pages. **Full
  2001-2025 sweep of the `all` filter RUNNING DETACHED** (log:
  `data/cbs_raw/bsb/history/ui/txn_sweep_20260712.log`); resumable/
  idempotent — if dead, relaunch the same command. NO API alternative
  exists (transaction-list/log = current rolling window only).
- **Franchise provenance design (Kyle)**: franchise_id = the spine
  (standings map is authority); owner-lineage overlay later; curated
  continuity-overrides seed for the traps (Foster's Folly id 13→30
  across 2020; Aching Hippos id 1 → renamed Mesa Javelinas 2023; id 1 sat
  out 2020 while Scott co-ran a temp team w/ Patrick Lahie). Recorded
  on MLB-64.
- **TRANSACTIONS PARSED + LANDED 2026-07-12 (98e95f0)**:
  RAW.CBS_UI_TRANSACTIONS = 52,369 player-actions / 24,388 transactions
  / 2001-2026, full-season spans, zero missing action phrases, 33.5k
  with real CBS player ids. Integrity traps handled in code: bgFan rows
  are REAL transactions (exclusion-based row matching — include-list
  zebra classes caused every boundary overlap); CBS double-renders rows
  adjacently (identity = cell-content hash, class ignored); end-of-season
  offsets clamp to a shifted window (walker window-merges prefix-onto-
  tail; 678 dupes dropped). Trades = one txn id across two per-side rows
  (NOT dupes). **THE FIDELITY MAP IS EMPIRICAL: 2001-2003 + 2021+ have
  full lineup logs (day-grain active sets); 2004-2020 membership moves
  only (adds/drops/trades — active approximated via Start%). 2001's 572
  'Signed' = likely the founding roster construction.**
- **MLB-63 WALK-BACK: COMPLETE + LANDED 2026-07-12 (3b88e95).** The
  full chain is live in dbt (progress ledger: `WALKBACK_PROGRESS.md`):
  `int_cbs__player_name_ids` (name→id dictionary, ambiguity flagged) →
  `int_cbs__roster_stints` (20,003 stints 2001-2025; last-event-wins;
  synthetic season-start openings; **100% of the 10,449 anchor states
  reproduced**) → `int_cbs__roster_stints_effective` (departure-day
  exclusive + single-rostering truncation) → `int_cbs__lineup_intervals`
  (activate/reserve intervals INCLUDING the backward prior-inverse
  half) → `fct_cbs_player_game_attribution` (every priced game
  franchise-attributed, per-row provenance: captured /
  reconstructed_day / estimated_startshare / estimated_membership) →
  `mart_team_points_reconciliation` (THE REPORT CARD vs official
  standings). **Grades: 5-13% mean abs error 2003-2019, 2.4-10.8%
  2021-2025; 2001-02 ~80% (no anchors, log-only); 2020 21% (COVID).**
  Coverage extension landed: UI-population crosswalk (2,736/2,753
  names, 99.4%) + 1.2M gamelog rows under synthetic `ui-<mlbam>` ids;
  record-book floor moved to the league's true 2001 start (Bonds 73 HR
  / Big Unit 372 K + the 1,142-pt all-time season now lead). Two
  SYSTEMATIC residuals documented, not calibrated: start-share era
  undershoots ~10-13% (global estimator conservative); 2021-22
  overshoot ~8-10% fading to 0 by 2024-25 (lossier early lineup logs).
- **MLB-54 DUAL-SOURCE VERIFY: DONE 2026-07-12 (closed the ticket).**
  2026 was start_row-swept full-season (1,325 moves / 45 pages; raw
  now 55,980 player-actions), then the API log (197 txns / 748 moves)
  full-outer-joined vs the UI rows on (franchise, player id, move
  type, effective date): **746/748 exact, zero UI-only.** The 2 misses
  = one PRE-SEASON trade (Torkelson + Connelly Early, 3/25) the UI
  report STRUCTURALLY OMITS — a known-limitation class the walk-back
  absorbs (pre-season acquisitions surface as season-start openings on
  the correct team; only the channel label is coarser). Recorded in
  sources.yml.
- NEXT: **almanac v2 build, re-sequenced per Kyle 2026-07-13 — the
  UNION comes FIRST.** His calls, verbatim intent:
  (1) NO CBS-only eligibility silo: eligibility lands in the SAME
  shape ESPN's flows through, league-keyed — sources differ by
  necessity (ESPN: platform-served daily eligible_slots; CBS: derived
  from MLB fielding games-by-position + the captured rule 'primary +
  20 games last yr or 10 this yr + DH-for-all'), the consuming shape
  converges. Semantics = ESPN's: points count toward a position only
  AFTER eligibility is achieved (date-scoped windows: primary from
  opening day; earned positions from the 10th game this year / day
  one if 20 last year). Needs the fielding sweep (statsapi
  group=fielding yearByYear, ~4k calls, detached+idempotent).
  (2) MLB-72 (CBS day-grain → int_player_daily → the shared fact
  family) is now IN the v2 critical path — 'the point at which we
  produce an output is the point to marry the data'. Build team pages
  on the unified fact, not CBS-specific assembly.
  (3) Team pages: only CURRENTLY-ACTIVE franchises get tabs; key
  everything by franchise_id but design for a later re-key to OWNER
  (MLB-64 chain-of-custody) — 'what people want is performance by
  OWNERS'.
  (4) Provenance/estimator disclaimers: land them somewhere sensible
  on-sheet; Kyle will tweak placement after seeing it.

1. **MLB-55 — year-end rosters: PARSED + LANDED 2026-07-12**
   (`extract/cbs_ui_parse.py --families rosters`, RAW.CBS_UI_ROSTERS,
   **10,449 rows, ALL 16 teams every season 2003-2025** after the
   same-day force re-sweep closed the early-era gap; Ohtani ownership
   trail matches Kyle's ground truth). Era lessons baked into the
   parser: label-driven columns (modern Own%/Start% vs early Eligible),
   pages are LEAGUE-WIDE (title rows delimit teams; early renders split
   teams into contiguous sibling tables — the parser consumes the run;
   sibling files deduped after verified identical), franchise ids are
   ALL name-resolved (join the MLB-53 name→id map — the filename ids
   are lies).
2. **MLB-53 — standings: PARSED + LANDED 2026-07-12**
   (RAW.CBS_UI_STANDINGS, 395 rows, 25 completed seasons 2001-2025,
   34 franchise ids, zero unresolved). THE CHAMPIONS LIST EXISTS:
   Hardball Hackers ×5, Bayside Bombers ×4, Aching Hippos ×2, 13
   distinct champs. Team names DRIFT across eras ('Junk Drawer All-Stars'
   2008 vs 'Junk Drawer All-Stars' now) — the name→id map is (season, name)
   keyed. League shape: 16 teams except 15 (2002) and 12 (2020).
   2026's Final Standings card is legitimately empty (in progress).

2b. **(RESOLVED 2026-07-12) The "windowed transaction log" was a pager
   illusion** — Kyle found the real pagination (`?start_row=N`; bare
   URL = newest-first page 1). The full 2001-2026 history was swept,
   parsed, staged, and consumed by the walk-back (see the landed
   MLB-63 block above). Kept for the record: transaction rows carry
   real CBS player ids (modern eras), team ids, clean verbs, and a
   separate EFFECTIVE date — all of which the stint machine uses.
3. **Almanac v2 per the approved blueprint** (Kyle's sign-offs
   2026-07-12, ESPN fixtures = ground truth for architecture —
   `tests/fixtures/almanac_v1_1_0/*.tsv`):
   - **Home** = navigation-first (Navigate table w/ live #gid links,
     two-pass write like ESPN's), points glossary, All-League Team
     boards: **Season-to-Date + All-Time ONLY** (no Team-of-the-Period).
   - **Records tab** = PLAYER records only: Best Season (existing mart)
     side-by-side with Best Career Totals (new accumulation model over
     int_cbs__player_season_stats — the MLB-69 axis). No weekly/period
     records (period boundaries unavailable historically + irrelevant).
   - **Standings tab** (replaces Matchup History): 2026 period-by-period
     arc + historic season finishes once MLB-53 parses.
   - **Team pages** (the meat): Best Lineup slot-filled by ACTIVE points
     per eligible position (slot template C/1B/2B/3B/SS/OF×3/U/DH+P×9),
     RosterDays/Games/Active/Bench/ppg + stat columns, bench ranked by
     total rostered points — current-season × ALL-TIME side by side;
     all-time pool = year-end-roster membership (fidelity-labeled).
   - **Advanced Standings: skipped** for CBS v1. **Draft Recap: skipped**
     (MLB-56 unparsed; offline-draft noise) — nav placeholder.
   - **The 2026 active lens = a dbt FACT model** (Kyle's call): daily
     rosters (stg_cbs__rosters) × per-game points
     (int_cbs__player_game_points) on (player_id, date) → player-team-day
     grain w/ active/bench split. Feeds team pages + the Season board +
     MLB-63 later.
   v1 renderer content in `output/cbs_almanac_sheets.py` gets rebuilt;
   keep its write layer (quota-hardened 2026-07-12: one style
   batch_update per tab + 70s quota backoff mirroring almanac_write).

The merge-to-main gate stays: Kyle eyeballs the rendered Sheet. OAuth
token was re-minted during his 2026-07-12 dev run (dev sheet now holds
the half-written v1 output; the v2 rewrite overwrites it).

---

## TL;DR

The CBS almanac's **stats source pivoted** this session. The hard half — acquiring
and loading the data — is **done and verified in Snowflake**. What remains is pure
dbt modeling: recompute → rebuild the record book → render an almanac output.

- **Why the pivot:** CBS's `league/stats` API is **free-agent-only** — every
  currently-rostered player is absent from all 20 years of "history" (0/480). The
  first CBS record book was silently wrong (JD Martinez's 45 HR "record"; no Cole,
  Judge, Trout, Ohtani). Kyle caught it.
- **The fix:** source the **baseball layer** (what a player did, per game) from the
  free public **MLB Stats API** (`statsapi.mlb.com`), joined to CBS's **fantasy
  layer** (who was rostered/active when + scoring rules). Portable across platforms;
  complete (all MLB players). Spike-proven.

---

## The architecture (read this first)

**Two layers, cleanly split:**
- **Fantasy layer** (platform-specific): membership (who was rostered), active vs
  reserve, scoring rules. Only the platform knows this. Source: CBS.
- **Baseball layer** (universal): what each player did per game. One true answer.
  Source: MLB Stats API. Reused across CBS/Yahoo/Fantrax.

**Two scoring lenses** (the ESPN `platform_`/`calculated_` pattern, per Kyle):
- `calculated_` = universal stats × current CBS scoring rules. **The default.** The
  only per-game-capable lens (needed for active/reserve slicing + best-games).
- `platform_` = CBS's own awarded season FPTS. The reconciliation **anchor**
  (season-grain; CBS serves no per-game FPTS).
- The **delta is a feature** — surfaced and called out. It catches era-rule changes
  AND CBS data gaps (spike: Kirby Yates 2019 — CBS under-tracks inherited-runners-
  stranded, so `calculated_` is *more* accurate than CBS's own feed).

**Active-only is the default** (Kyle, explicit): production counts **only while
rostered AND active**. Cole striking out 326 but benched for a 10-K game = 316 in
this league. Requires **per-game stats × per-day active status**. Per-game we have;
active-status-per-day is 2026 (roster capture) + history (MLB-63 reconstruction).

---

## Data inventory — all in Snowflake `RAW` (`ESPN_FANTASY.RAW`)

| Table | Rows | Role |
|---|---|---|
| `MLB_GAMELOGS` | 595,918 | **The stats spine.** Per-game, universal, 2,227 players |
| `MLB_SEASON_STATS` | 16,957 | Season aggregates (yearByYear); carries season `team` |
| `CBS_MLBAM_CROSSWALK` | 2,225 | CBS player id → MLBAM id (99.7%; team-aware, **0 collisions**) |
| `CBS_GAMELOGS` | 677,151 | CBS's own per-game (FA-only) — **reconciliation ground-truth** now |
| `CBS_SEASON_STATS` | 44 files | CBS season FPTS = the `platform_` anchor (FA-only) |
| `CBS_ROSTERS` | 105 | 2026 daily rosters (`roster_status` A/RS = active/reserve) |
| `CBS_STANDINGS` | 16 | 2026 period standings (drives the arc) |
| `CBS_CONFIG` | 15 | scoring_rules (the 16 scored categories + weights) |
| `CBS_TRANSACTIONS` | 3 | 2026 rolling window |

Seeds (`ANALYTICS`): `canonical_stats`, `cbs_stat_map`, `stat_classification`
(now carries a `canonical_key` bridge + an `IRSTR` row).

**No more extraction/loading is needed for the current scope.** (Exception: the UI
historical rosters, MLB-47 — captured as HTML under `data/cbs_raw/bsb/history/ui/`,
not yet parsed — feed departed-players + ownership, a later phase.)

---

## Extract scripts (`extract/`)

| Script | What | Notes |
|---|---|---|
| `mlb_crosswalk.py` | CBS player → MLBAM id | name+season+team; lands `CBS_MLBAM_CROSSWALK` |
| `mlb_stats.py` | universal per-player game logs | statsapi; idempotent/resumable; lands `data/mlb_stats/` |
| `mlb_load.py` | files → `MLB_*` raw tables | NDJSON+COPY, `--data-dir <main>/data/mlb_stats` |
| `cbs_backfill.py` | CBS season+gamelog archive | museum rule; done (now ground-truth) |
| `cbs_load.py` | CBS files → `raw.cbs_*` | `--data-dir <main>/data/cbs_raw` |

**Long sweeps MUST be launched DETACHED** (`Start-Process ... -WindowStyle Hidden`),
not via a Bash background shell — those die when the Claude Code process exits (it
killed the MLB extract once at 87/2214). Always make sweeps idempotent + log to disk.

---

## dbt models built this session (`dbt_league/models/`)

**Good / keep:**
- `staging/stg_cbs__standings` → `marts/reporting/mart_period_standings` — the 2026
  standings arc. Real, verified (Baltic White Sox's pennant race). Platform-neutral.
- `marts/core/fct_team_season_performance` → `marts/reporting/mart_team_alltime` —
  the shared, **format-modular** team-stats spine + all-time ESPN team stats (W-L a
  format-conditional overlay). Verified vs `mart_team_season_standings`.
- The **vocab bridge**: `stat_classification.canonical_key` (67 ESPN stats mapped) +
  an `IRSTR` row; `dim_stat` exposes `canonical_key`. Byte-neutral for ESPN.

**Re-scoped (2026-07-11, both now good):**
- `staging/stg_cbs__player_season_stats` — the FA-only platform archive,
  recontextualized as the `platform_` reconciliation anchor (feeds
  `mart_player_fpts_reconciliation`). NOT a record source.
- `marts/reporting/mart_player_season_records` — REBUILT on the
  `calculated_` lens via `int_cbs__player_season_stats` (universal
  population). PG deliberately absent (bf=outs means faced-the-MINIMUM,
  not perfect — erased-runner no-hitters are indistinguishable from
  perfectos in a pitcher's aggregate line); NH derives cleanly.

---

## What's left — the chain to a working almanac output

1. **Collision-fix — DONE 2026-07-10.** The crosswalk rebuild is team-aware:
   CBS's per-season `TM` column (NOT `pro_team`, which is current-team stamped on
   every row) scored against the statsapi season listings' `currentTeam`, with the
   CBS team-code map learned from unique-name co-occurrence. All 21 flags resolved
   PLUS three silent mismatches caught (Vladdy Jr was mapped to his FATHER; Eury
   Pérez and Juan Morillo to older same-name players). 13 reassigned ids extracted,
   loaded, content-verified (Vladdy Jr 2021 HR=48, catcher Smith 2024 HR=20).

2. **Recompute (MLB-62) — DONE 2026-07-11.** The chain:
   `mlb_stat_map` seed → `stg_mlb__player_game` (long canonical per-game rows)
   → `stg_cbs__scoring_settings` (feed-read weights; INN lands as
   outs_recorded @ 1/out) + `stg_cbs__mlbam_crosswalk` (scope column routes
   the 900/901 split) → `int_cbs__player_game_points` (the priced per-game
   engine; QS + IRSTR derived) → `mart_player_fpts_reconciliation` (the delta
   report). All content-verified: Cole 2019 326 K / 26 QS / 959 fpts; Ohtani
   two-way split leak-free; Yates 2019 +18 = 9 untracked strands × 2.
   Findings: platform_identity_residual = 0.0 on all 8,185 rows (no era-rule
   changes in the archive; CBS totals self-consistent under current weights);
   2023-25 ~97% exact; ALL large deltas = CBS's sparse pre-2023 IRSTR feed
   (calculated_ is more accurate than the platform). **Bonus: the delta
   report's first run caught two silent crosswalk mismatches** (Michael
   Taylor → the real "Michael A. Taylor" 572191; Jose Hernandez → the LHP
   669796) — the crosswalk build now evidence-guards unique exact-name
   matches too; post-fix ZERO real-production platform rows are missing
   from reconciliation. Original spec follows for reference:
   - **Two-way join note (MLB-68, decided):** CBS's Ohtani pseudo-ids (900
     Batter / 901 Pitcher) both map to MLBAM 660271 — the one sanctioned
     shared crosswalk pair. The statsapi `stat_group` column splits the
     disciplines cleanly: hitting game rows feed 900, pitching rows feed 901
     (reported as two players).
   - `stg_mlb__player_game`: `MLB_GAMELOGS` → tidy per-game stat rows. Map statsapi
     keys (`strikeOuts`, `homeRuns`, `earnedRuns`, `inheritedRunners`,
     `inheritedRunnersScored`, `outs`, `wins`, `saves`, `holds`, `completeGames`,
     `hits`, `baseOnBalls`…) to the shared vocabulary (build an `mlb_stat_map` seed
     like `cbs_stat_map`, or map straight to `canonical_key`).
   - Per-game `calculated_` FPTS × CBS scoring rules (`stg_cbs__scoring_settings` from
     `cbs_stat_map.points_2026`). **Gotchas baked into the spike:** INN is scored at
     **out-granularity** (+1/out via `outs`, NOT +3/inning); **QS** is derived
     per-start (`gamesStarted=1 AND IP≥6 AND ER≤3`); **IRSTR** =
     `inheritedRunners − inheritedRunnersScored`.
   - Reconcile season-sum(`calculated_`) vs CBS `platform_` (season universe FPTS,
     where available) → the delta report (era-rule + data-gap detection).

3. **Rebuild the record book — DONE 2026-07-11.**
   `int_cbs__player_season_stats` (player-season LONG on calculated_: bridged
   counting stats + derived 1B/XBH + engine QS/IRSTR/NH +
   CALCULATED_POINTS/_HITTING/_PITCHING; archive-era floor 2004, data-driven)
   → `mart_player_season_records` rebuilt (42 stats × top-10). Verified: Cole
   2019 326 K #1, Judge 2022 62 HR #1 (Raleigh 2025 60 #2), Verlander 2011 =
   1010 top calculated season (matches the old platform marquee exactly),
   Ohtani-Batter 2024 = 815 top hitting season, Scherzer 2015 = 2 NH.
   platform_ lens record-ineligible (reconciliation-only); PG underivable
   (documented in the engine); total lens labeled until MLB-63.

4. **Render (MLB-66 / MLB-58)**: the almanac Sheet (home + team tabs) via the
   registry-resolved sink into Kyle's Sheet (MLB-49, created). Reuse the ESPN almanac
   write/format plumbing; content assembly is format-conditional.

---

## Tickets (Linear team `fantasy-league-almanac`, project Multi-Platform Support)

- **MLB-70** — universal stats layer: **COMPLETE 2026-07-10** (team-aware
  crosswalk with 0 collisions, extract, load — content-verified end-to-end).
- **MLB-62** — per-game FPTS recompute: **COMPLETE 2026-07-11** (the
  calculated_ engine + reconciliation mart, content-verified end-to-end;
  caught + fixed two crosswalk mismatches on the way).
- **MLB-61** — CBS staging. Standings arc + FA-only record book landed; pivot to
  universal for the record book.
- **MLB-63** — ownership/active-set reconstruction. **Now the spine** (production only
  counts while rostered/active). Fed by MLB-47 UI rosters + transactions.
- **MLB-65 / MLB-66** — marts / almanac v1 (the render).
- **MLB-45** — CBS backfill. Done; recontextualized as reconciliation ground-truth.
- **MLB-68** — Ohtani split: DECIDED (report as two players).
- **Kyle's side:** MLB-49 (Sheet — done), MLB-50 (recap scope), MLB-51 (lore), MLB-52
  (rollover ~9/20), MLB-57 (review checklist).

---

## Gotchas / lessons (don't re-learn these)

- **FA-only universe** is the whole reason for the pivot — don't trust CBS
  `league/stats` as a historical record. (`player_status=all&owned=all` unlocks the
  *current* pool but it's still "current pool viewed historically.")
- **Verify on-branch before every commit** — this session's second half was
  committed on a **detached HEAD** and had to be reclaimed by fast-forward. Run
  `git symbolic-ref -q HEAD`.
- **Detached sweeps** (above). Keep the laptop awake for long runs.
- **Crosswalk is name+season+team-based** (0 collisions since 2026-07-10); CBS's
  `TM` = season-team, `pro_team` = current-team-on-every-row (do not join on it).
- **statsapi shape:** `people/{id}/stats?stats=gameLog&group={hitting|pitching}&season=YYYY`;
  splits are games; season stats via `stats=yearByYear&group=hitting,pitching`;
  name→id via `people/search?names=`; all-players-in-a-season via
  `sports/1/players?season=YYYY`. Free, no key.
- **Reconciliation, not exact-match:** `calculated_` won't always equal `platform_`
  (CBS data gaps) — that's the point.
