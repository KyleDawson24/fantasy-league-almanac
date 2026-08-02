## [1.5.0] — 2026-07-XX

The multi-league release, and the longest arc I've let build up between
versions. The warehouse gains a league registry and a `league_key`
re-grain of every layer, and the first non-ESPN league ships end-to-end
over it: a 25-year almanac for the CBS "Box Score Baseball" points
league (`cbs-bsb`), built from a universal MLB stats layer, a
transaction-log roster walk-back with per-row fidelity grades, and a
record book verified against real MLB history — through to a rendered
workbook whose team pages share the ESPN almanac's builder. On the ESPN
side, the almanac's Advanced Standings tab is reworked into per-stat
weekly-average standings, Transaction Records land on a new
roster-stint model, all-time team stats arrive, and a season-to-date
report joins the weekly recap. Under it all, a portfolio-readability
pass over the dbt project — layered DAG, enforced data-quality tests,
accurate exposures — held byte-neutral for the three output surfaces.

Minor, not major: everything here is additive — a second league, new
marts, new tabs, a new report — with the recap and records BBCode
goldens held byte-identical throughout and every almanac golden move a
reviewed re-anchor.

1.3.x/1.4.x were internal working labels during an unreleased stretch;
skipped to keep internal docs unambiguous.

### Added

- **League registry + `league_key` re-grain (the multi-league
  foundation).** `config/leagues.yml` + `config/league_registry.py`
  hold one entry per league; `espn-main` is entry #1 and the default
  everywhere, so the weekly runbook is unchanged. Extract stamps
  `league_key` into every RAW row (payloads stay verbatim), all staging
  emits it, every team/player join and uniqueness key widened, and the
  incremental facts run on per-league watermarks. Output scripts gain
  `--league` and a process-wide league context, and surfaces resolve
  their sinks from the registry — `--prod` without a configured sink
  fails loudly. Migration gate: both BBCode goldens held
  byte-identical; the almanac TSVs moved on exactly 63 cells, all
  verified rounding-boundary re-rolls, re-anchored under review.

- **The CBS preservation program** — read-only, GET-only captures of a
  25-season league before the data rots (the museum rule), landed as
  raw envelopes and loaded into six `raw.cbs_*` tables:
  - `extract/cbs_capture.py` saves the perishable 2026 fantasy layer
    (per-date rosters with deployed slot and started/sat, period
    standings, transaction and config snapshots). Content-based
    verification caught the API's decoy parameters — real roster
    history rides `point=YYYYMMDD`, real standings `period=N` — and
    the sweep cross-checks 624/624 against transaction-log ground
    truth. Rides the weekly runbook as its last step.
  - `extract/cbs_backfill.py` archives the 20-year API history:
    per-season player universes and gamelogs for hitters and — via the
    `position=P` universe key the documented toggles decoyed —
    pitchers, 677,151 per-game rows in all, with persistent-500
    player-seasons tombstoned with evidence. The sweep also surfaced
    that CBS models a two-way player as two rosterable pseudo-players
    (900 "Ohtani (Batter)" / 901 "(Pitcher)") invisible to both
    universe tables; the gamelog fetch handles them explicitly.
  - `extract/cbs_ui_capture.py` captures what the API denies: site-UI
    standings 2001+ (final standings derive all 26 champions),
    transaction reports 2001+, year-end roster reports 2003+, drafts
    2017+, and per-franchise overview pages — 526 GETs, verdict PASS.

- **Universal MLB stats layer.** CBS's own stats history turned out to
  be free-agent-only — every currently-rostered star was absent from
  all 20 "historical" years — so player production now comes from the
  public MLB Stats API (complete, portable across platforms) joined to
  CBS's fantasy layer, and the CBS archive is recontextualized as
  reconciliation ground truth:
  - `extract/mlb_crosswalk.py` maps CBS ids → MLBAM ids (2,225 rows,
    99.7%), disambiguated by season overlap AND season-team agreement;
    the evidence bar caught silent same-name mismatches (Vladimir
    Guerrero Jr. had been mapped to his father) before they could
    poison the record book.
  - `extract/mlb_stats.py` + `mlb_load.py` sweep and land season plus
    per-game stats for every crosswalked player in two
    platform-neutral raw tables — 595,918 gamelog rows, 1991–2026,
    zero failed fetches.
  - **The `calculated_` lens.** CBS serves no per-game fantasy points,
    so `int_cbs__player_game_points` prices every player-game from
    universal stats × the league's own scoring rules (QS and IRSTR
    derived per game; the two-way split as a crosswalk join
    predicate). The one scoring translation — CBS lists INN but pays
    per out — verified 587/587 against season FPTS.
  - `mart_player_fpts_reconciliation` grades the recompute against
    CBS's own awarded totals: 0.0 residual on all 8,185 reconciled
    player-seasons, with every large delta traced to the platform's
    own sparse pre-2023 IRSTR feed — the calculated lens is more
    accurate than the platform's — and the delta report caught (and
    fixed) two more silent crosswalk mismatches on its first run.

- **The CBS walk-back: 25 years of day-by-day rosters reconstructed
  from the transaction log, graded against the official standings.**
  The UI-history captures (52,369 player-actions 2001–2026, 10,449
  year-end anchor states) drive a last-event-wins state machine:
  `int_cbs__roster_stints` (20,003 membership stints; every anchor
  state reproduced, 100%), `int_cbs__lineup_intervals` (daily active
  state, including the backward half so set-and-forget starters don't
  zero out), and `fct_cbs_player_game_attribution`, which
  franchise-attributes every priced player-game under a per-row
  fidelity flag (`captured` / `reconstructed_day` /
  `estimated_startshare` / `estimated_membership` — estimates are
  labeled, never laundered; the no-lineup-log 2004–2020 era rides a
  Start%/Own% estimator). `mart_team_points_reconciliation` grades the
  reconstruction against 25 seasons of official finishes: ~5–13.5%
  mean absolute error 2003–2019, 2.1–4.2% 2021–2025, with the
  systematic residuals — the estimator-era undershoot, and 2021–2023
  official pitching running ~8–11% low (the signature of a
  since-removed team pitching cap) — documented in the mart, not
  calibrated away. Only 22 true missing departures remain across 25
  years.
  - **Coverage extension:** year-end-roster names the FA-only archive
    never held (Bonds, Randy Johnson, every star who retired before
    2026) enter via a name→MLBAM crosswalk (99.4%) and synthetic ids;
    1.2M more gamelog rows priced, and the record book's floor moves
    from the archive's 2004 to the league's true first season, 2001.

- **The union layer: CBS day-grain production flows through the ESPN
  fact family.** `int_cbs__player_daily` re-expresses walk-back
  attribution × the priced engine in `int_player_daily`'s exact column
  contract, so `fct_player_daily_performance` →
  `fct_player_position_pts` serve both leagues from one shape. Shared
  columns both branches fill: `player_key` (the cross-league player
  grain), `active_weight`, `provenance`; new lenses
  `weighted_active_pts` and `rostered_pts`. ESPN byte-neutrality held —
  BBCode goldens exact; the almanac drifted on exactly two dead-center
  rounding cells, verified and re-anchored.

- **CBS position eligibility as a shared, date-scoped model.** The
  league's own captured rule lands as after-achievement windows in
  `int_cbs__eligibility_windows`, fed by a career fielding sweep and a
  no-refetch discovery — the gamelog files already carry per-game
  `positionsPlayed` (1.9M rows loaded). Graded against CBS's own 2026
  captured eligibility: 93.26% exact-set agreement, with the Ohtani
  pseudo-id canary exact.

- **The CBS record book, season × career, on the calculated lens.**
  `mart_player_season_records` ranks the top-10 single seasons per
  record-candidate stat over the universal layer;
  `mart_player_career_records` adds the accumulation axis (Pujols's
  703 HR and 11,824 points lead). With the 2001 floor, Bonds's 73 HR
  and Randy Johnson's 372 K lead their boards and Johnson's 2002
  (1,142 points) is the all-time fantasy season; archive-era marquees
  (Judge's 62 HR in 2022, Cole's 326 K in 2019, Verlander's
  1,010-point 2011) content-verified along the way. The platform lens
  is record-ineligible by design (population bias). No-hitters derive
  cleanly; perfect games are deliberately absent — a pitcher's
  aggregate line can't distinguish a real perfecto from a bf = outs
  no-hitter, and 3 of the archive's 4 candidates would be false
  positives.

- **The CBS almanac.** `output/cbs_almanac_sheets.py` mirrors the ESPN
  almanac's architecture on the unified facts: nav-first Home (live
  `#gid` links, two-pass write) with Season-to-Date + All-Time
  All-League boards; Records = best season × best career side by side;
  Standings = the 2026 period arc (over the new platform-neutral
  `mart_period_standings`) + a 25-season finish matrix with champions
  marked; and one page per active franchise with Best Lineup (current
  season × all-time), ranked bench blocks, and a provenance sentence
  on every page. The generator dispatches by data presence (a points
  league is a league with period standings), never a platform check.
  No wall-clock cells — previews are deterministic and golden-able
  from day one. Franchise scoping sits behind one seam
  (`_entity_where`) for a later owner re-key.
  - **Team pages, unified.** The CBS team tabs render through the
    shared ESPN builder — one row contract, one format source
    (`almanac_render.team_tab_format_specs`) — so the two writers
    cannot drift. The team-sheet overhaul ships on both leagues: a
    Total | Active | Inactive points trio under a merged banner, the
    gold-standard header with inline glossary (plus a CBS-only
    era-provenance block), Years of Service, the Other section capped
    at 100 with the franchise futility chair pinned last, and a Best
    Individual Seasons by Lineup Slot block (the optimal lineup over
    player-season candidates). ESPN almanac goldens re-anchored to the
    new shape.

- **Transaction Records: production by acquisition channel on Advanced
  Standings.** Team rankings by how each player's production was
  acquired:
  - The durable ESPN transaction log was found by content on the
    league message board (`kona_league_communication` topics — 3,028
    verified, draft day → today; `mTransactions2` is a current-period
    decoy). Extract gains `--include-transactions` /
    `--transactions-only`, landing verbatim topics in an append-only
    `RAW.TRANSACTIONS`. Current season only for now — the prior-season
    endpoints reject the topics filter, a documented follow-up.
  - `stg_transactions` decodes the messageTypeId vocabulary into a
    platform-neutral directed-event shape; `fct_roster_stints` builds
    one row per contiguous roster window, tagged how it opened
    (KEEPER / DRAFT / TRADE / FA_ADD) and closed (DROPPED /
    TRADED_AWAY), on the dense lineup shell so an unloaded All-Star
    gap doesn't split a stint while a real off-roster gap does.
  - `mart_team_acquisition_channels` reports two lenses — ACTIVE and
    ROSTERED — with FA and Trade Net deltas, reconciling exactly to
    each team's own production. Advanced Standings grows two stacked
    channel blocks with polarity-aware gradients. The almanac golden
    re-anchored on `Advanced-Standings.tsv` only — a pure append; the
    BBCode goldens held byte-identical.

- **All-time ESPN team stats.** `fct_team_season_performance` is the
  season-grain team spine both platforms can feed: the stat rollup is
  format-agnostic (it sums the player-active fact), while the W-L /
  platform-total overlay is format-conditional by data presence, never
  a platform check. `mart_team_alltime` rolls it into franchise
  records — all-time accumulation + best single season — with
  league-wide all-time wins == losses (282-282) confirming the
  overlay.

- **Season-to-date report** (`output/generate_season_report.py`) — the
  milestone-summary entry point, built for the All-Star break post and
  extendable to an end-of-season edition. Deliberately
  calendar-agnostic: run any week, it reports "Through Week N."
  Sections mirror the weekly recap's BBCode idiom: best/worst team
  callouts on the per-gameplay-week lens, season Top
  Scorer/Hitter/Pitcher cards, Season Superlatives, the season-to-date
  All-League Team, all-time records set or tied this season, and Top
  Wasted Performances. Occasion flavor comes from new optional
  header/footer note files (`output/note_files.py`, gitignored),
  printed verbatim on every summary — blank or missing files
  contribute nothing, so output is byte-identical until the
  commissioner writes one.

- **`docs/known-data-issues.md`** — the permanent log of source-data
  defects that are documented and bounded rather than fixed (the
  warehouse doesn't control the source): the IRSTR season-key
  disagreement, the UI transaction report's structural
  pre-season-trade omission, the suspected 2021–23 team pitching cap,
  the residual walk-back flags, and the era coverage floors — each
  entry with its full evidence chain.

- **Project hygiene for readers:** no-warehouse CI (unit suite + `dbt
  parse` on every push; warehouse goldens stay local by design),
  source freshness on the settings-style raw tables, shared
  stat-column doc blocks replacing 62 definitions the two wide facts
  repeated verbatim, a "Reading the DAG" README section on the three
  deliberate cross-layer edges, and the missing seed/grain column
  docs.

### Changed

- **Advanced Standings tab reworked.** The standings block now shows
  each scored stat individually — the same seed-driven stat set and
  order as Matchup History, plus the Offense / Defense / Total /
  Against points columns — with every value a per-standard-matchup
  average over gameplay days (the 14-calendar-day All-Star week counts
  its ~11 game days; the standard week length is derived per season,
  so a 2-week-matchup league would normalize per-14 with no code
  change). Raw season totals left the block; the weekly shape is how
  the league actually reads scores. The Points by Lineup Slot grid
  keeps season totals but drops BE / IL, ordered from
  `dim_roster_slot_counts` instead of a hardcoded map. Gradients are
  polarity-aware per stat and positioned structurally; the tab is
  clear-and-rewrite, so manually hidden columns survive reruns. Both
  blocks read two new reporting marts — `mart_team_season_standings`
  and `mart_team_slot_production` (tables, grain-tested, on the
  `league_almanac` exposure) — and the almanac's two inline standings
  aggregations were deleted from `output/almanac_data.py`, the same
  lift that created `mart_team_matchup` in v1.1.1. Intentionally
  output-changing; the almanac goldens were re-anchored under review.

- **marts/ re-layered and facts renamed.** The contract layer (4 dims,
  7 facts) now lives under `marts/core/`, the consumer marts under
  `marts/reporting/`; every fact reads entity-first
  (`fct_player_weekly_...`, `fct_team_weekly_...`, with a `_slot_`
  marker fixing one grain-misleading name), and the two
  consumer-contract intermediates are promoted
  (`fct_player_position_pts`, `dim_team_owner`). The seven pre-rename
  relations are left standing so un-merged checkouts keep working;
  drop them once a post-release `dbt build` has run.

- **"Only staging reads sources" is now absolute.** The matchup-grain
  extraction inside the team fact moved into `stg_matchup_scores` +
  `stg_matchup_pairs`, and the roster-settings flatten into
  `stg_roster_settings` — proven equivalent by symmetric EXCEPT and
  pre/post row hashes. Season-grain float sums are frozen as tables so
  .x5-boundary values can't flip between two reads with no data
  change.

- **Checks promoted, exposures trued, docs refreshed.** The
  `analyses/check_*.sql` assertions now run on every `dbt build` as
  singular tests, plus a severity-warn data canary; `league_almanac`
  declares its real reads; the dbt README is rewritten as a
  layer-by-layer architecture narrative and stale claims across the
  docs were fixed. Byte-neutral for all three output surfaces.

### Fixed

- **Quota retry during formula reapply turned into a hard 400.**
  gspread's `batch_update` rewrites each payload entry's `range` in
  place, so a Sheets-quota retry resent already-prefixed ranges
  (`'HH'!'HH'!C7` → "Unable to parse range"), killing a run a
  70-second wait should have saved. The reapply now hands gspread
  fresh dicts on every attempt; regression-tested against the in-place
  mutation. Latent since the v1.2 bref-links pass.

Verification: full `dbt build` green (337 nodes at last count,
including the new marts' grain tests); `dbt parse` with zero
deprecation warnings; all singular tests pass. The recap and records
BBCode goldens held byte-identical across the entire arc; almanac
goldens were re-anchored only under review (the Advanced Standings
rework, the team-tab overhaul, the Transaction Records pure append)
plus the documented .x5 float-summation residuals. CBS output is
content-verified against real MLB history and reconciles against the
platform's own totals (594/594 exact on the 2025 pitcher recompute;
0.0 residual on all 8,185 reconciled player-seasons).

---

# Curation notes

**The v2.0 wording fix (three spots).** The stale framing appeared in
the [Unreleased] intro ("the first v2.0 feature"), the Changed bullet
heading ("Advanced Standings tab reworked (v2.0 feature #1)"), and
inside `mart_team_slot_production` ("the v2.0 grid filters to
active"). All three reworded so Advanced Standings simply ships now;
no v2.0 reference survives inside the section.

**Forward-promises and stale forward-notes removed.** Nothing in
[Unreleased] literally promised a future version number, but several
entries carried "comes later" notes for work that landed later in the
same section — collapsed to the final state:
- "The parsed-UI half of the loader waits on the HTML parsers"
  (cbs_load entry) — stale; the walk-back entry shows the UI history
  parsed and in the warehouse (52,369 player-actions, 10,449 anchors).
- "Adapter-shaped staging comes later with the format-abstraction
  work" (capture entry) — stale; `stg_cbs__*` models shipped.
- "Historical champions land from the parsed UI pages into the same
  mart shape" (first standings-mart entry) — the 25-season finish
  matrix with champions ships in the v2 renderer, so stated as done.
- "The calculated lens + best single games follow with the gamelog
  recompute" (platform-lens record-book entry) — the calculated lens
  landed in-release; entries merged (see below). Best-single-games is
  not claimed anywhere as done, so it is simply not mentioned.
- "CBS team stats drop in unchanged once the player-performance
  convergence lands" (team-season fact entry) — the union layer
  landed, but no entry confirms CBS team stats materialized, so the
  claim was dropped rather than asserted.
- Kept, deliberately: versionless scope-bounding notes that are honest
  today ("current season only for now — a documented follow-up" on
  transactions; "for a later owner re-key" on `_entity_where`;
  "extendable to an end-of-season edition"). These bound scope without
  promising a release.

**Superseded entries merged.** [Unreleased] accreted in layers, so
several entries describe intermediate states: the platform-lens CBS
record book (MLB-61 F1) was rebuilt on the calculated lens (MLB-65) —
merged into one record-book bullet reflecting the final state (2001
floor, Bonds/Johnson leading); the first CBS almanac render
(home + 16 team tabs) was superseded by the v2 renderer — merged, with
the registry-resolved-sinks half folded into the registry bullet; the
MLB-45 "reopened for pitching" narrative and its later "that sweep has
since landed" cross-reference collapsed into one backfill sub-bullet.

**Structural fixes.** [Unreleased] had two separate `### Added` blocks
with `### Changed` between them (an append-as-you-go artifact) —
merged into one Added. The two new standings marts sat ~200 lines away
from the Advanced Standings rework they exist to serve — folded into
that Changed bullet. The seven-statement `DROP TABLE/VIEW` orphan list
condensed to one sentence (the changelog is not a runbook; the exact
statements remain in git history at the old [Unreleased] revision).

**Pruned detail.** Linear ticket IDs (MLB-xx) removed throughout — no
released section in this file carries them. "Per Offline Chat
2026-07-09" citation dropped (meaningless to readers; semantics kept).
Deep evidence chains condensed: the walk-back's three correctness-pass
forensics to one clause (the 22-missing-departures census), the
crosswalk collision catalog to the Vlad Jr. example, the golden
re-anchor minutiae (six ppg flips, the 148.0→148.1 cell, 382.75/443.05
boundary values) to "documented .x5 float-summation residuals," and
most row/file counts, keeping only the marquee numbers.

**Stale verification footer rewritten.** The old footer reported
PASS=221, which predates later entries (the Transaction Records entry
reports a 337-node green build; the standings-mart entry a 194-test
suite). The new Verification paragraph uses only the latest numbers
the section itself documents — nothing new was invented — but the
final pre-tag `dbt build` / pytest counts should be re-run and
substituted at ship time, per the file's habit of exact counts.

**MINOR-bump rationale, phrased carefully.** The release plan's line
is "everything is additive; ESPN surfaces held byte-identical by
goldens." Strictly, only the BBCode surfaces held byte-identical; the
ESPN almanac goldens were re-anchored several times (Advanced
Standings rework, team-tab overhaul, Transaction Records append,
rounding residuals). The rationale sentence in the intro says exactly
that ("BBCode goldens byte-identical throughout; every almanac golden
move a reviewed re-anchor") to stay true to the file.

**Plan items I could not find in [Unreleased] (flagged, not
fabricated).**
- "Draft Recap tabs on both almanacs": no such entry exists. ESPN's
  Draft Recap shipped in v1.2.0; the CBS almanac v2 entry lists Home /
  Records / Standings / team pages only, and the only draft material
  in this arc is raw CBS draft-page captures (2017+) plus the
  DRAFT/KEEPER channel in `fct_roster_stints`. If a CBS Draft Recap
  tab actually shipped, it was never logged — confirm and add a bullet
  before tagging.
- "The player identity dimension": no `dim_player` (or similar) entry
  exists. The identity work actually logged is the `player_key`
  cross-league grain, the two MLBAM crosswalks, synthetic `ui-<mlbam>`
  ids, and the two-way pseudo-id handling — written up as such inside
  the union-layer and stats-layer bullets.
- "A format overhaul": interpreted as the format-modularity work
  (points-vs-H2H dispatch by data presence; the format-agnostic /
  format-conditional team-season fact) plus the team-sheet overhaul,
  both of which are in the section. If "format overhaul" meant
  something else, tell me and I'll re-cut.

**Kept but worth a maintainer sanity check.**
- The Fixed bullet's "Latent since the v1.2 bref-links pass" — the
  1.2.0 section never mentions a bref-links pass, so the reference is
  to unlogged v1.2-era work. Kept as written since the source asserts
  it.
- "Box Score Baseball" as the league's name comes from the release
  plan; the changelog itself only ever says `cbs-bsb`.
- The date is left as 2026-07-XX per the plan; fill at tag time.

---

# ⚠ MISSING FROM [Unreleased] ENTIRELY — write these bullets at cut time

The draft-recap arc (commits `9810c09..10889f8`, 2026-07-17→18) never
landed its changelog entries (the CHANGELOG's last update predates the
arc's later rounds). The cut section above therefore has NO entry for,
and needs bullets covering:

- **Draft Recap, both books.** CBS gets the tab (mirroring the ESPN
  layout): the 2026 board with Value/Bust leaderboards, all-time
  draft-slot boards (pace-adjusted part-seasons; single-pick Top-Pick
  values never scaled), and year-by-year Draft Classes with an honest
  coverage line (true order 2025–26 only; orderless classes 2011–23;
  nothing pre-2011). ESPN's existing tab is overhauled to match
  (Top-Pick boards, season pacing, keeper "K" row ranked per team).
- **The shared format overhaul** — house width grid
  (25/40/40/125/75/40 + 100) applied identically to both books.
- **The CBS draft data pipeline (stopgap).** Reads the draft NDJSON +
  an int model directly; the dbt-ification is deliberately deferred
  (phrase without promising a version number).
- **The 2022–23 pitcher-points note.** Calculated points run ~10–15%
  above CBS's page for those years because QS(+4)/IRSTR(+2) entered
  the scoring in 2024 — by design under current-rules re-scoring;
  worth one honest line since it's reader-visible.

Also at cut time: re-run `dbt build` + pytest and substitute the final
counts into the Verification paragraph (the drafted numbers are the
latest the old section documented, not tonight's).

---

# UPDATE 2026-07-20 ~2a — the merge LANDED tonight (main = f3f1eb3, pushed)

The reconcile happened same-night, not Tuesday. The cut is now just
changelog + tag on main. ADD these to the section alongside the
draft-recap bullets above:

- **Trades tab (ESPN almanac, MLB-103):** live trade block + interest
  counts; its two warehouse queries league-scoped during the merge
  (they were written pre-scoping — the exact cross-league leak class
  that briefly wrecked the ESPN dev sheet).
- **`mlb_stats.py --discover`:** sweeps pool players the crosswalk
  never fetched (~151 found on first live run); with the surgical
  current-season re-fetch, the weekly refresh recipe is now ~20 min.
- **Goldens regenerated from the merged code** against the stable
  warehouse — superseding both lines' interleaved re-anchors (worth one
  honest line since fixtures moved).
- Optional ops note: main-side extracts briefly landed NULL-league_key
  rows pre-merge (repaired by hand); the stamping now lives on main.

Version-rationale check: still MINOR-clean — everything above is
additive; goldens moved with reviewed cause (the merge).
