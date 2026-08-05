# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each entry links to the corresponding `Phase X.Y Documentation.md` in
`docs/archive/` for the architectural detail behind the change. (Those files
sat in the repository root until MLB-154 moved them, so entries below name
them by filename alone.)

## [Unreleased]

_In progress: the DuckDB engine port (MLB-10), so the project can run
without a cloud warehouse. Release notes are built from the commit range
at each cut rather than accumulated here, so this section staying short
is not a sign the repository is idle._

### Changed

- **Seeds split three ways, and a stranger no longer inherits our league**
  (MLB-114). One directory was doing three unrelated jobs: reference
  vocabulary, this league's configuration, and — by way of the anonymized
  twins — the only sample data the project had. Now
  `dbt_league/seeds/` is reference vocabulary (same for everyone, ships
  filled in), `dbt_league/league_config/` is user config (**blank
  templates**, documented file by file with a worked example each), and
  `demo/league_config/` is an explicitly named demo fixture. `seed-paths`
  reads both roots and `DBT_LEAGUE_CONFIG` selects the second, so the demo
  swaps one directory and every model follows; no model changed, because
  dbt resolves seeds by filename rather than path. `SETUP.md` gains the
  step that never existed — which files you actually have to fill in, and
  the honest minimum for an ESPN league, which is one.
- `owner_nicknames`' `column_types` **stops naming contact columns**. It
  typed `email`/`phone_number` so a six-column local copy would satisfy
  dbt-duckdb's supposed all-or-none rule, which put the names of two
  withheld columns into public config. Tested rather than assumed: a
  six-column file loads fine against a four-column map, so the keys were
  buying nothing. The template is now the schema.
- **The demo builds its own warehouse and refuses the real one.**
  Rendering reads marts, not seeds, so a fixture pointed at marts built
  from real config would still have produced a real-name book — which is
  the sample workbook published read-only.
- **The repository root is curated** (MLB-154). It held 40 tracked files,
  28 of which were session exhaust — handoffs, phase journals, progress
  notes and three variants of one release-notes file — burying the six
  documents a stranger actually wants. Those 28 moved to `docs/archive/`
  (24), `docs/decisions/` (2) and `docs/releases/` (2), each with an
  index; the root now carries the canon plus infrastructure. Nothing was
  deleted — the handoff pile is the record of how the project got here,
  and it reads as evidence once indexed and as clutter while it sits at
  the front door.
- `RELEASING.md` carries the **publish-the-GitHub-Release step** it had
  been missing, which is how v1.6.0 was tagged and published with no
  notes file ever landing in the repo. That file now exists too.

## [1.6.0] — 2026-07-30

The last stable point before the engine port -- MLB-10's rollback anchor,
and the reason this range gets a tag at all. Two halves: a polish pass
league members actually saw, and a determinism sweep they didn't.

The visible half finished the Total-Points vocabulary on both books and
moved scope text off the grid and into the section banners. The invisible
half found that the ESPN writers had been re-rendering on top of their own
previous output for as long as the layout had been row-stable enough to
hide it -- three separate faces of one gap, all surfaced by this release's
own layout changes and all caught in dev review.

Minor, not patch: the glossary rewrite and the banner conversion change
what the surfaces say and how they read.

### Added

- **An "Updated" stamp on both Home tabs.** A3 carries a render-time
  `Updated MMM d, yyyy kk:mm` (ET, 24-hour) in italic size 10, suppressed
  by `SUPPRESS_UPDATED_STAMP=1` so the byte-diff corpora stay stable.
- **Unrostered Points** joins the Points Glossary on both books, and CBS
  mirrors ESPN at six entries.

### Changed

- **The Points Glossary is rewritten to the settled Total-Points lenses**
  (MLB-141): Total = active + inactive + unrostered, and Wasted is
  restated to the canonical three-way with a footnote naming the current
  under-count until MLB-135 lands. CBS keeps Calculated Points for its
  platform-verification job and swaps Rostered Points for ESPN's Inactive
  Points vocabulary.
- **Advanced Standings converts to banner + italic-scope captions on both
  books** (MLB-142): era and scope text move into the navy section
  banners as italic white captions, and the separate era rows above
  Points by Lineup Slot, Production by Acquisition Channel, and the
  affinity chart fold in with them.
- **Every team-tab row is pinned to 21px on both books** (MLB-143) -- the
  values write had been auto-growing rows under wrapped cells with
  nothing to reset them.
- **The Wasted Points definition is trimmed and its merge widened to
  B:D** (MLB-141); the previous footnote clipped after three of seven
  wrapped lines.
- **The acquisition-channel lens explainers render as captions on both
  books** (MLB-161) -- size 10 italic rather than bold or unstyled.
- **The ESPN Draft Recap tab moves to sit directly before the first team
  tab** (MLB-162), so the league-wide surfaces run together.
- Home tab column A widens to 125px on both books.
- The README contact block gains a Ko-fi link, and the repo gains a
  Contributing note: issues and feedback welcome, code contributions not
  accepted for now while the licensing story settles.

### Fixed

- **The ESPN Trades tab dropped Date Executed cells and side sums on
  re-render.** `worksheet.clear()` drops values but keeps merges, and the
  Sheets API silently discards a value written into a non-anchor cell of
  a merged range -- so each render wrote new rows onto the previous
  render's merge lattice and lost whatever landed off-anchor. Unmerging
  now happens before the values write, across every ESPN writer that
  merges (Trades, Home, team tabs, Advanced Standings, Records, Draft)
  and in the CBS writer. The data and the builder were correct
  throughout; only the order was wrong.
- **ESPN tabs painted over the previous render's formatting.** `clear()`
  drops values but not cell formats, and unlike the CBS writer the ESPN
  writers had no reset -- invisible while the layout was row-stable, and
  exposed the moment this release's era-row deletions shifted blocks up
  one to three rows. Every ESPN tab writer now opens its format phase
  with a whole-sheet `userEnteredFormat` reset, mirroring CBS doctrine.
  One consequence worth naming: hand formatting on ESPN tabs no longer
  survives a re-render, which has always been true on CBS.
- **The banner gate no longer silently stops banding on a reworded
  title** -- it is prefix-matched, in the same commit as the renames.

### Internal

Groundwork for the MLB-10 port, and value-neutral by design: every site
was checked against the warehouse before it was touched, and each commit
in the sweep carries the same standing constraint -- engine-only, values
must not move. (The Home tab re-anchor in this release belongs to the
glossary rewrite above, not to any of this.)

- **The row-selection ordering sweep** (MLB-134): the 10 latest-load-wins
  dedups and every remaining row-selection window are now totally
  ordered, so no engine gets to choose which payload survives a tie.
  Keys are picked per source -- CBS raw leads with `captured_at` (the
  recency signal those models actually meant), ESPN raw falls back to a
  payload hash as a backstop behind real semantic keys, never as the
  semantics itself.
- **NULL placement is stated on 21 DESC row-selection keys across 16
  sites** (MLB-134). Snowflake defaults to NULLS FIRST on DESC and DuckDB
  to NULLS LAST, so an inherited engine default could otherwise pick a
  different row on each. A documented no-op today -- every key came back
  zero NULLs -- which is the point: it stops being luck and starts being
  stated.
- **New singular test `assert_label_payload_constant`**, which carries
  the half a tie-breaker cannot: pinning an order fixes which row is
  chosen, and this is what makes the value trustworthy. Invisible to the
  byte-diff goldens by construction, which is why it earns its place.
- `owner_nicknames` column_types completed against the six-column local
  seed (MLB-134), unblocked by the MLB-95 ruling that identity seeds
  carry no contact columns in git.
- The INITCAP site in `dim_owner` is named for the port, so a golden that
  moves there has a documented cause instead of reading as a data bug.
- The `mart_team_alltime` header now says outright that it is MLB-69's
  pre-built data layer with no readers by design, so the next
  zero-readers sweep gets a self-answering comment instead of proposing
  it for deletion.

### Documentation

- **The setup and dbt docs no longer lie about their own inventory**
  (MLB-153). A fresh clone was promised 112 pure tests, 4 seeds, 173 dbt
  tests and view-based staging; it gets 250, 18, 543 and tables. Every
  count now comes from the parsed manifest at HEAD. Command docs are
  restructured into three honestly-labeled tiers -- offline (any clone,
  touches nothing) / live read-only (needs credentials) / mutation
  (writes; deliberate ceremony) -- and the private golden corpora are
  named, with the note that the tests needing them *skip* rather than
  fail in a public clone.
- **A DAG boundary design draft** (`docs/dag-boundaries-DRAFT.md`,
  MLB-158 Phase A): every model mapped to a target layer, with the
  graph's backward edges catalogued and each one written up as options
  rather than decisions. Draft status is deliberate -- nothing has moved.

## [1.5.1] — 2026-07-25

A correctness pass on the CBS record book -- the story of this release,
and several days longer than it looked. Finishing the multi-league
identity work (routing every team's display through the franchise
dimension) surfaced the CBS almanac's first byte-diff golden. Within an
hour that golden caught a silent corruption: attribution had been
mis-crediting player-games across the league's entire 26-season history,
and the record book didn't even produce the same answer twice.

Pulling that thread ran the length of the release, each fix uncovering the
next. The golden's non-determinism traced to same-day roster stints
truncating each other; that to a transaction capture silently dropping
~408 rows at pagination seams across the whole history; a display headline
that disagreed with its own breakdown to record values being rounded
twice.

Then the two big ones. The roster walk-back had paired transactions in
effective-date order, silently assuming managers never reprocess a move --
so a queued lineup change, a retroactive drop, or a same-minute trade
flurry each mis-credited a franchise or stranded a real roster as free
agents. Rebuilt to resolve each day's state by the most-recently-executed
transaction effective by then, it now reconstructs the league's roster
history as the transaction log actually describes it. And player identity,
which returned nothing whenever a name had two live candidates, now
resolves each ambiguous name per-franchise from that franchise's own
position-and-club paperwork against the universal MLB stats spine -- so the
two Will Smiths and the three Luis Garcias land on the right rosters.

The measurable end: `attribution_contested` went from 0 (silently, because
the flag itself miscounted) to 0 (provably -- every player-game across 26
seasons credits exactly one franchise). Reconstruction accuracy, measured
against the platform's own published standings, improved across the
seasons the work touched (the recent captured era most visibly: 2023's
mean team error 4.4% -> 3.9%). The record book now produces the same
answer twice.

Patch, not minor: everything here corrects existing surfaces rather than
adding new ones.

### Fixed

- **The record book was non-deterministic.** Same-day roster stints
  truncated each other in a rebuild-order-dependent way, so career and
  season records changed between builds. Fixed at the root (a stint's end
  is capped only by *strictly later* acquisitions); five consecutive
  rebuilds now byte-identical.

- **The transaction capture silently lost rows.** The paginated sweep
  dropped entries at every page seam -- ~408 across the 26-year history,
  including trades that vanished from a franchise's record. Re-captured
  seam-free via `print_rows`; the walk-back now reads the complete log.
  Lance Lynn's 2022 trade, the case that exposed it, reconstructs exactly
  as the log describes.

- **Record values were rounded twice and rendered unstably.** Aggregates
  carried at one decimal, summed in Python, then rounded again for
  display -- inflating ~3% of career counting-stat records by a full unit
  and flipping boundary cells run-to-run. Now carried at full precision
  and rounded once, with a deterministic sort so genuine ties resolve the
  same way every time.

- **The ESPN almanac's team pages got the same rounding hardening.** The
  fix above had a sibling on the ESPN side: the per-team point aggregates
  were carried at one decimal, summed in Python, then rounded again for
  display, with ties that could resolve by row order. The team-history and
  Best-Seasons aggregates now carry full precision, round once, and sort on
  a stable key. The current two-season ESPN data lands no cell on a rounding
  boundary, so today's output is byte-identical -- this is a safeguard
  against boundary inflation as the league accumulates seasons, and it keeps
  both books' renderers symmetric.

- **The roster walk-back mis-credited reprocessed transactions.** It
  paired events in effective-date order, which breaks whenever a manager
  reprocesses a move -- a queued activation, a retroactive drop, a rapid
  trade flurry. Rebuilt to resolve each day's roster state by the
  most-recently-*executed* transaction effective by then; verified against
  the platform's published standings, every affected season moved toward
  the official numbers and every untouched season stayed identical. A
  companion inversion in the report's own row ordering (which had sent a
  trade behind its own bench line) is fixed in the same pass.

- **Player identity gave up on ambiguous names.** Two real players sharing
  a name (the Dodgers-catcher and reliever Will Smiths; three Luis
  Garcias) left every stint unidentified and every game credited to both
  franchises. Now resolved per-franchise from the franchise's own
  position/club paperwork against the MLB spine, with a human-owned
  override seed for the residue that genuinely cannot be decided from
  evidence.

- **A slugger's season displayed under a minor-leaguer's misspelled
  name.** A fuzzy crosswalk match collided two 2004 Gonzalezes onto one
  id, so Juan González's 33-game season rendered as "Jeremi Gonzalez."
  Fixed by a name-form override seed.

### Changed

- **Franchise identity resolves through one dimension, at season grain.**
  `dim_franchise` splits identity (the earliest id) from display (the
  latest seen); the lineage seed gains a season scope so a reused id can
  be *split*, not only merged; a platform-general holding pen handles
  synthesised franchises. Every team-display surface, and the last
  direct-from-RAW marts, route through it.

- **The CBS almanac gains a byte-diff golden.** Rendered from the real
  league (so it lives outside the public repo and skips when absent), it
  pins the 20-tab workbook byte-for-byte -- the test that caught
  everything above.

- **Identity is a reusable, platform-general resolver.** A split candidate
  pipeline, the season resolver, a franchise-context layer, and two
  human-owned override seeds (one name-form, one franchise-scoped) form a
  single id-first identity map keyed on MLBAM rather than names.

## [1.5.0] — 2026-07-21

The multi-league release, and the longest arc I've let build up between
public versions. The warehouse gains a league registry and a
`league_key` re-grain of every layer, and the first non-ESPN, non-H2H
league ships end-to-end over it: a 25-year almanac for a CBS points
league (`cbs-bsb`).

Historic player performance derived from a universal MLB stats layer,
rosters reconstructed using season-end states with transaction-log
walkbacks, and a record book verified against real MLB history --
through to a rendered workbook whose team pages share the ESPN almanac's
builder.

On the ESPN side the almanac gains Advanced Standings, Trades, Baseball
Reference links throughout and a reworked Draft Recap. The weekly recap
moves onto the calculated points lens; Transaction Records land on a new
roster-stint model; all-time team stats arrive; and a season-to-date
report joins the weekly recap (primarily to be deployed during all-star
breaks and seasons' ends, but executable at any point).

Under it all, a portfolio-readability pass over the dbt project --
layered DAG, enforced data-quality tests, accurate exposures -- held
byte-neutral for the three output surfaces.

Minor, not major: everything here is additive -- a second league, new
marts, new tabs, a new report. The records BBCode golden held
byte-identical across the whole arc, and every almanac golden move was a
reviewed re-anchor. (The weekly-recap golden is re-cut with each week's
data by design, so it evidences the current week rather than the arc.)

1.3.x/1.4.x were internal working labels during an unreleased stretch;
skipped to keep internal docs unambiguous.

### Added

- **League registry + `league_key` re-grain (the multi-league
  foundation).** `config/leagues.yml` + `config/league_registry.py` hold
  one entry per league; `espn-main` is entry #1 and the default
  everywhere, so the weekly runbook is unchanged. Extract stamps
  `league_key` into every RAW row (payloads stay verbatim), all staging
  emits it, every team/player join and uniqueness key widened, and the
  incremental facts run on per-league watermarks. Output scripts gain
  `--league` and a process-wide league context, and surfaces resolve
  their sinks from the registry -- `--prod` without a configured sink
  fails loudly. Migration gate: both BBCode goldens held byte-identical;
  the almanac TSVs moved on exactly 63 cells, all verified
  rounding-boundary re-rolls, re-anchored under review.

- **Dev/prod Sheet targeting.** Every Sheets surface takes an
  explicit `--prod`; the default target is the dev workbook. The
  destructive-by-default footgun -- a formatting experiment landing on
  the workbook the league actually reads -- stops being possible by
  accident. The registry work above later grew this into per-league sink
  resolution.

- **`dim_player_identity` -- one identity resolver, id-first joins
  everywhere.** Name matching is demoted from four separate join seams
  to a single platform-general dimension mapping every CBS name form
  onto the MLBAM spine, season-scoped so homonyms resolve by MLB game
  presence, with a `player_alias` seed carrying the hard renames
  (Carmona; Mike/Michael Stanton) that generated variants can't bridge.
  The games/stints, stints/lineup, stints/anchors and anchor-estimator
  seams equijoin on `mlbam_id` (+ discipline scope) and fall back to the
  name join only where a name is genuinely ambiguous that season -- a
  strict superset of the prior behaviour. Anchor CTEs re-aggregate to
  MLBAM grain so the LEFT JOINs can't silently fan out, guarded by a new
  `assert_cbs_attribution_no_fanout`. Effect: K-Rod's Angels peak goes
  6% → ~88% attributed (and becomes the single-season Saves record); the
  middle-initial class rises 36% → the league-wide ~70% norm; Ohtani's
  unified 2018–24 entries attribute with the two-way halves kept split;
  contested attributions read 0 of 977k rows -- which v1.5.1 later showed
  was a miscount by the flag itself rather than a real zero; it is 0
  provably as of that release.

- **Franchise and owner continuity -- a 25-year league told as
  lineages, not team ids.** A generated continuity mapping sheet lets
  the league historian declare which team ids are the same franchise and
  which owner names are the same person; `harvest_continuity_sheet.py`
  reads the filled sheet back into override-only seeds (union-find
  resolves mutual and chained "Same As" pointers to the earliest-id
  anchor; fuzzy header matching survives the historian retitling or
  adding columns). From those: `dim_franchise` collapses 34 team ids
  into 31 canonical franchises, and `int_cbs__team_owner_season`
  resolves owner names to canonical ids per season -- growing the CBS
  owner spine from 19 current-era owners to 46 all-time and
  `dim_team_owner` from 16 rows to 328 team-seasons, with the ESPN
  branch and every current display verified unchanged. Canonical
  franchise then threads through the almanac: Season Finishes roll up by
  lineage (34 → 31 rows) and record boards carry real per-lineage owner
  labels. The pipeline is platform-agnostic by construction -- nothing
  in it is CBS-specific.

- **Baseball Reference links throughout the almanac.** Standalone
  player-name cells -- record holders, All-League Team picks, every
  per-team roster, and the draft board -- render as hyperlinks to the
  player's Baseball Reference page. Visible text stays the almanac's
  display name (nickname-or-official) while the URL keys off the
  official name, so a nickname never breaks a link; multi-name
  contributor cells stay plain, since a Sheets cell carries one
  hyperlink. Tabs written as raw values emit clickable `HYPERLINK`
  formulas.

- **Advanced Standings (the tab itself).** A new almanac tab
  pairing a per-team standings block with a Points by Lineup Slot grid.
  It was reworked twice more inside this same release -- see *Changed*
  for the per-stat weekly-average rework, and the round-two rebuild
  below.

- **Advanced Standings round two, both books.** Thirteen live
  review rounds turned two static tables into the almanacs' densest
  pages: rank-by-period line charts on both books (self-contained hidden
  helper blocks, per-team checkboxes with an ALL master, n+1 flip so 1st
  plots on top -- ESPN's arc reconstructed from weekly results, since no
  snapshots exist, and matching the final standings order exactly);
  season-finish matrices with division columns, titles-then-average
  sorting, division-champion borders and per-year auto-scaled rank
  gradients; points-by-lineup-slot in season-total and all-time-pace
  form; and production by acquisition channel on both books. ESPN's
  finish block carries trophy-**and**-finish cells because the playoff
  champion and the regular-season leader genuinely differ -- the 2025
  crown came from 7th.

- **The canonical stat catalog.** One platform-neutral stat
  vocabulary with per-platform crosswalk seeds, plus the ESPN →
  canonical bridge, so a stat means the same thing on both books and the
  record boards can auto-track a stat rather than hardcode it.

- **The CBS preservation program** -- read-only, GET-only captures of a
  25-season league before the data rots (the museum rule), landed as raw
  envelopes and loaded into six `raw.cbs_*` tables:
  - `extract/cbs_capture.py` saves the perishable 2026 fantasy layer
    (per-date rosters with deployed slot and started/sat, period
    standings, transaction and config snapshots). Content-based
    verification caught the API's decoy parameters -- real roster
    history rides `point=YYYYMMDD`, real standings `period=N` -- and the
    sweep cross-checks 624/624 against transaction-log ground truth.
    Rides the weekly runbook as its last step.
  - `extract/cbs_backfill.py` archives the 20-year API history:
    per-season player universes and gamelogs for hitters and -- via the
    `position=P` universe key the documented toggles decoyed --
    pitchers, 677,151 per-game rows in all, with persistent-500
    player-seasons tombstoned with evidence. The sweep also surfaced
    that CBS models a two-way player as two rosterable pseudo-players
    (900 "Ohtani (Batter)" / 901 "(Pitcher)") invisible to both universe
    tables; the gamelog fetch handles them explicitly.
  - `extract/cbs_ui_capture.py` captures what the API denies: site-UI
    standings 2001+ (final standings derive all 26 champions),
    transaction reports 2001+, year-end roster reports 2003+, drafts
    2017+, and per-franchise overview pages -- 526 GETs, verdict PASS.

- **Universal MLB stats layer.** CBS's own stats history turned out to
  be free-agent-only -- every currently-rostered star was absent from
  all 20 "historical" years -- so player production now comes from the
  public MLB Stats API (complete, portable across platforms) joined to
  CBS's fantasy layer, and the CBS archive is recontextualized as
  reconciliation ground truth:
  - `extract/mlb_crosswalk.py` maps CBS ids → MLBAM ids (2,225 rows,
    99.7%), disambiguated by season overlap AND season-team agreement;
    the evidence bar caught silent same-name mismatches (Vladimir
    Guerrero Jr. had been mapped to his father) before they could poison
    the record book.
  - `extract/mlb_stats.py` + `mlb_load.py` sweep and land season plus
    per-game stats for every crosswalked player in two platform-neutral
    raw tables -- 595,918 gamelog rows, 1991–2026, zero failed fetches.
    `--discover` later closed the new-player blind spot by sweeping pool
    players the crosswalk never fetched (~151 on its first live run);
    with a surgical current-season re-fetch the weekly refresh is ~20
    minutes.
  - **The `calculated_` lens.** CBS serves no per-game fantasy points,
    so `int_cbs__player_game_points` prices every player-game from
    universal stats × the league's own scoring rules (QS and IRSTR
    derived per game; the two-way split as a crosswalk join predicate).
    The one scoring translation -- CBS lists INN but pays per out --
    verified 587/587 against season FPTS.
  - `mart_player_fpts_reconciliation` grades the recompute against CBS's
    own awarded totals: 0.0 residual on all 8,185 reconciled
    player-seasons, with every large delta traced to the platform's own
    sparse pre-2023 IRSTR feed -- the calculated lens is more accurate
    than the platform's -- and the delta report caught (and fixed) two
    more silent crosswalk mismatches on its first run.
  - **The 2022–23 pitcher note.** Calculated points run ~10–15%
    above CBS's own pages for those two years because QS (+4) and IRSTR
    (+2) entered the league's scoring in 2024. That is by design under
    current-rules re-scoring, and it is reader-visible, so it is stated
    rather than smoothed.

- **The CBS walk-back: 25 years of day-by-day rosters reconstructed from
  the transaction log, graded against the official standings.** The
  UI-history captures (52,369 player-actions 2001–2026, 10,449 year-end
  anchor states) drive a last-event-wins state machine:
  `int_cbs__roster_stints` (20,003 membership stints; every anchor state
  reproduced, 100%), `int_cbs__lineup_intervals` (daily active state,
  including the backward half so set-and-forget starters don't zero
  out), and `fct_cbs_player_game_attribution`, which
  franchise-attributes every priced player-game under a per-row fidelity
  flag (`captured` / `reconstructed_day` / `estimated_startshare` /
  `estimated_membership` -- estimates are labeled, never laundered; the
  no-lineup-log 2004–2020 era rides a Start%/Own% estimator).
  `mart_team_points_reconciliation` grades the reconstruction against 25
  seasons of official finishes: ~5–13.5% mean absolute error 2003–2019,
  2.1–4.2% 2021–2025, with the systematic residuals -- the estimator-era
  undershoot, and 2021–2023 official pitching running ~8–11% low (the
  signature of a since-removed team pitching cap) -- documented in the
  mart, not calibrated away. Only 22 true missing departures remain
  across 25 years.
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
  `weighted_active_pts` and `rostered_pts`. ESPN byte-neutrality held --
  BBCode goldens exact; the almanac drifted on exactly two dead-center
  rounding cells, verified and re-anchored.

- **CBS position eligibility as a shared, date-scoped model.** The
  league's own captured rule lands as after-achievement windows in
  `int_cbs__eligibility_windows`, fed by a career fielding sweep and a
  no-refetch discovery -- the gamelog files already carry per-game
  `positionsPlayed` (1.9M rows loaded). Graded against CBS's own 2026
  captured eligibility: 93.26% exact-set agreement, with the Ohtani
  pseudo-id canary exact.

- **The CBS record book, season × career, on the calculated lens.**
  `mart_player_season_records` ranks the top-10 single seasons per
  record-candidate stat over the universal layer;
  `mart_player_career_records` adds the accumulation axis (Pujols's 703
  HR and 11,824 points lead). With the 2001 floor, Bonds's 73 HR and
  Randy Johnson's 372 K lead their boards and Johnson's 2002 (1,142
  points) is the all-time fantasy season; archive-era marquees (Judge's
  62 HR in 2022, Cole's 326 K in 2019, Verlander's 1,010-point 2011)
  content-verified along the way. The platform lens is record-ineligible
  by design (population bias). No-hitters derive cleanly; perfect games
  are deliberately absent -- a pitcher's aggregate line can't
  distinguish a real perfecto from a bf = outs no-hitter, and 3 of the
  archive's 4 candidates would be false positives.

- **The CBS almanac.** `output/cbs_almanac_sheets.py` mirrors the ESPN
  almanac's architecture on the unified facts: nav-first Home (live
  `#gid` links, two-pass write) with Season-to-Date + All-Time
  All-League boards; Records = best season × best career side by side;
  Standings = the 2026 period arc (over the new platform-neutral
  `mart_period_standings`) + a 25-season finish matrix with champions
  marked; and one page per active franchise with Best Lineup (current
  season × all-time), ranked bench blocks, and a provenance sentence on
  every page. The generator dispatches by data presence (a points league
  is a league with period standings), never a platform check. No
  wall-clock cells -- previews are deterministic and golden-able from
  day one. Franchise scoping sits behind one seam (`_entity_where`).
  - **The Records page, in full.** Auto-cataloged from the stat
    vocabulary and rebuilt to mirror ESPN's shape across five review
    rounds: two-scope (season × career) format with an active-only
    career lens, polarity-split boards in box-score order, a **Hall of
    Fame** with years-of-service stint lists and slash/stat lines, a
    **Wasted Hall of Shame** ranked by points left on the bench and
    split Pitchers | Hitters, and a **Lineup Slot Records** section
    crediting each player's actual deployed slot (so a utility player
    ranks on his own slot rather than against the whole hitter pool).
    Owner labels resolve through the franchise-continuity bridge to real
    per-lineage owners.
  - **The Home page, in full.** Five review rounds added a Team of
    the Week board, a **running Team of the Month** (rolling over on the
    8th, with month-deviation callouts), bench blocks ordered by total
    points, a stat-sources table, and an all-time roster split into
    active and retired.
  - **Team pages, unified.** The CBS team tabs render through the shared
    ESPN builder -- one row contract, one format source
    (`almanac_render.team_tab_format_specs`) -- so the two writers
    cannot drift. The team-sheet overhaul ships on both leagues: a Total
    | Active | Inactive points trio under a merged banner, the
    gold-standard header with inline glossary (plus a CBS-only
    era-provenance block), Years of Service, the Other section capped at
    100 with the franchise futility chair pinned last, and a Best
    Individual Seasons by Lineup Slot block. ESPN almanac goldens
    re-anchored to the new shape.

- **Draft Recap, both books.** CBS gains the tab, mirroring the
  ESPN layout: the 2026 board with Value and Bust leaderboards, all-time
  draft-slot boards (pace-adjusted for part-seasons; single-pick
  Top-Pick values never scaled), and year-by-year Draft Classes under an
  honest coverage line -- true pick order exists for 2025–26 only,
  classes 2011–23 are orderless, and nothing survives pre-2011. ESPN's
  existing tab is overhauled to match: Top-Pick boards, season pacing,
  and a keeper "K" row ranked per team rather than league-wide. Both
  books share one house width grid (25/40/40/125/75/40 + 100) and one
  value-definition note.

- **The CBS draft data pipeline.** Every CBS draft surface swept
  and parsed into draft rows joined to the priced player layer. The
  provider currently reads the parsed NDJSON and an intermediate model
  directly rather than a mart -- a deliberate, loudly-flagged stopgap;
  the dbt-ification is tracked and deferred, not forgotten.

- **The Trades tab (ESPN).** The live trade block plus per-team
  interest counts, sourced from the live API rather than the warehouse,
  reporting counts rather than identities. Its two warehouse queries
  were league-scoped during the merge -- they had been written
  pre-scoping, the exact cross-league leak class that briefly wrecked
  the dev sheet.

- **Transaction Records: production by acquisition channel on Advanced
  Standings.** Team rankings by how each player's production was
  acquired:
  - The durable ESPN transaction log was found by content on the league
    message board (`kona_league_communication` topics -- 3,028 verified,
    draft day → today; `mTransactions2` is a current-period decoy).
    Extract gains `--include-transactions` / `--transactions-only`,
    landing verbatim topics in an append-only `RAW.TRANSACTIONS`, and
    skips gracefully on seasons the endpoint won't serve. Current season
    only for now -- the prior-season endpoints reject the topics filter,
    a documented follow-up.
  - `stg_transactions` decodes the messageTypeId vocabulary into a
    platform-neutral directed-event shape; `fct_roster_stints` builds
    one row per contiguous roster window, tagged how it opened (KEEPER /
    DRAFT / TRADE / FA_ADD) and closed (DROPPED / TRADED_AWAY), on the
    dense lineup shell so an unloaded All-Star gap doesn't split a stint
    while a real off-roster gap does.
  - `mart_team_acquisition_channels` reports two lenses -- ACTIVE and
    ROSTERED -- with FA and Trade Net deltas, reconciling exactly to
    each team's own production.

- **All-time ESPN team stats.** `fct_team_season_performance` is the
  season-grain team spine both platforms can feed: the stat rollup is
  format-agnostic (it sums the player-active fact), while the W-L /
  platform-total overlay is format-conditional by data presence, never a
  platform check. `mart_team_alltime` rolls it into franchise records --
  all-time accumulation + best single season -- with league-wide
  all-time wins == losses (282-282) confirming the overlay.

- **Season-to-date report** (`output/generate_season_report.py`) -- the
  milestone-summary entry point, built for the All-Star break post and
  extendable to an end-of-season edition. Deliberately
  calendar-agnostic: run any week, it reports "Through Week N." Sections
  mirror the weekly recap's BBCode idiom: best/worst team callouts on
  the per-gameplay-week lens, season Top Scorer/Hitter/Pitcher cards,
  Season Superlatives (including Game and Loss of the Week as weekly
  team awards and draft-value superlatives), the season-to-date
  All-League Team, all-time records set or tied this season, and Top
  Wasted Performances. Occasion flavor comes from new optional
  header/footer note files (`output/note_files.py`, gitignored), printed
  verbatim on every summary -- blank or missing files contribute
  nothing, so output is byte-identical until the commissioner writes
  one.

- **Recap: all-time records set in the most recent week are called
  out** in the weekly post, so a record set this week is visible where
  the league reads it rather than only in the records report.

- **`docs/known-data-issues.md`** -- the permanent log of source-data
  defects that are documented and bounded rather than fixed (the
  warehouse doesn't control the source): the IRSTR season-key
  disagreement, the UI transaction report's structural pre-season-trade
  omission, the suspected 2021–23 team pitching cap, the residual
  walk-back flags, and the era coverage floors -- each entry with its
  full evidence chain.

- **Project hygiene for readers:** no-warehouse CI (unit suite + `dbt
  parse` on every push; warehouse goldens stay local by design), source
  freshness on the settings-style raw tables, shared stat-column doc
  blocks replacing 62 definitions the two wide facts repeated verbatim,
  a "Reading the DAG" README section on the three deliberate cross-layer
  edges, and the missing seed/grain column docs.

- **Public-readiness guard rails.** The five owner-identity seeds
  are tracked in anonymized form while the pipeline continues to run on
  real data locally, so the published repo carries no real league member
  or team names. Two guards keep it that way: a gitleaks workflow on
  every push and pull request, and a local pre-push hook that fails the
  push if the tree reintroduces a real-league string.

- **Platform adapter contract v1.** The written contract a second
  platform must satisfy -- proposed, reviewed, and accepted inside this
  release -- plus the verified CBS capability manifest recording what
  that platform can and cannot serve.

### Changed

- **Advanced Standings tab reworked.** The standings block now shows
  each scored stat individually -- the same seed-driven stat set and
  order as Matchup History, plus the Offense / Defense / Total / Against
  points columns -- with every value a per-standard-matchup average over
  gameplay days (the 14-calendar-day All-Star week counts its ~11 game
  days; the standard week length is derived per season, so a
  2-week-matchup league would normalize per-14 with no code change). Raw
  season totals left the block; the weekly shape is how the league
  actually reads scores. The Points by Lineup Slot grid keeps season
  totals but drops BE / IL, ordered from `dim_roster_slot_counts`
  instead of a hardcoded map. Gradients are polarity-aware per stat and
  positioned structurally; the tab is clear-and-rewrite, so manually
  hidden columns survive reruns. Both blocks read two new reporting
  marts -- `mart_team_season_standings` and `mart_team_slot_production`
  (tables, grain-tested, on the `league_almanac` exposure) -- and the
  almanac's two inline standings aggregations were deleted from
  `output/almanac_data.py`, the same lift that created
  `mart_team_matchup` in v1.1.1. Intentionally output-changing; the
  almanac goldens were re-anchored under review.

- **The weekly recap moved to the calculated points lens.** Every
  superlative -- Top Scorer, Top Hitter, Top Pitcher, the All-League
  Team -- now reads the calculated lens rather than the platform's own
  attribution, and the platform point stats were untracked as record
  candidates entirely. The platform lens exists to mirror the host
  site's award, and where the two disagree on two-way production the
  calculated lens is the one that reconciles against the settled score.

- **New-record presentation in the recap.** Records broken before
  a tie are handled correctly, the redundant "New" prefix is dropped,
  the prior record is stated inline, and the abbreviation suffix is
  gone. A first-ever record set by several teams at once is framed as a
  new record rather than a tie.

- **Team-name introduction in recap prose.** A team is introduced
  by full name and abbreviated on repeat mention, rather than
  abbreviated throughout.

- **marts/ re-layered and facts renamed.** The contract layer (4 dims, 7
  facts) now lives under `marts/core/`, the consumer marts under
  `marts/reporting/`; every fact reads entity-first
  (`fct_player_weekly_...`, `fct_team_weekly_...`, with a `_slot_`
  marker fixing one grain-misleading name), and the two
  consumer-contract intermediates are promoted
  (`fct_player_position_pts`, `dim_team_owner`). The seven pre-rename
  relations are left standing so un-merged checkouts keep working; drop
  them once a post-release `dbt build` has run.

- **"Only staging reads sources" is now absolute.** The matchup-grain
  extraction inside the team fact moved into `stg_matchup_scores` +
  `stg_matchup_pairs`, and the roster-settings flatten into
  `stg_roster_settings` -- proven equivalent by symmetric EXCEPT and
  pre/post row hashes. Season-grain float sums are frozen as tables so
  those layers' .x5-boundary values can't flip between two reads with no
  data change. (Partial, as v1.5.1 established: several reporting marts
  still carry unfrozen sums and do re-roll a boundary cell on rebuild.)

- **Checks promoted, exposures trued, docs refreshed.** The
  `analyses/check_*.sql` assertions now run on every `dbt build` as
  singular tests, plus a severity-warn data canary; `league_almanac`
  declares its real reads; the dbt README is rewritten as a
  layer-by-layer architecture narrative and stale claims across the docs
  were fixed. Byte-neutral for all three output surfaces.

### Fixed

- **Quota retry during formula reapply turned into a hard 400.**
  gspread's `batch_update` rewrites each payload entry's `range` in
  place, so a Sheets-quota retry resent already-prefixed ranges
  (`'HH'!'HH'!C7` → "Unable to parse range"), killing a run a 70-second
  wait should have saved. The reapply now hands gspread fresh dicts on
  every attempt; regression-tested against the in-place mutation. Latent
  since the bref-links pass earlier in this release.

- **Team totals were rounded per component, not once.** Calculated
  team totals summed already-rounded round-level values, so a team score
  could differ from the sum of its parts. Totals now round once, at team
  grain.

- **Two-way production misbucketed between hitting and pitching.**
  `platform_points` split all-or-nothing by lineup slot, so a two-way
  player's pitching dumped into `platform_hitting_pts` on a DH or UTIL
  day -- which mislabeled him as Top Hitter on a combined hit+pitch
  total and suppressed the two-way Top Scorer line. The split now
  follows each day's per-category stat contribution; single-role days
  collapse to the prior behaviour. (A separate slot-awareness defect in
  the same lens remains open and is documented; the recap no longer
  reads this lens.)

- **Optimal-team attribution for multi-team players.** A player
  who changed fantasy teams mid-season could be credited to the wrong
  team in the optimal-lineup calculation. Owner display names are also
  pre-resolved rather than resolved per row.

- **Google OAuth token expiry killed the run.** An expired Sheets
  token now triggers a graceful re-consent instead of an unhandled
  failure mid-render.

- **Sheets per-minute write quota.** The almanac writers survive
  the per-minute quota ceiling, adopting the backoff the ESPN writer
  already used.

- **The recap golden depended on gitignored local state.** The
  commissioner's optional note files are read from disk at render time,
  so a baseline captured on a machine that had them could never match a
  run on a machine that didn't. `SUPPRESS_LEAGUE_NOTES=1` now makes
  every note read empty, and the golden harness sets it -- so the
  fixture pins the recap engine rather than whatever flavor is in play
  that week.

- **Logged drops didn't close roster stints.** The stint-pairing
  window missed drops recorded in the transaction log, leaving stints
  open past their real end and over-attributing production.

- **Season-report rate stats.** Rate statistics were computed on
  the wrong denominator in the season-to-date report.

### Verification

Full `dbt build` green -- 552 nodes, PASS=548 / WARN=0 / ERROR=0 /
SKIP=0, including the new marts' grain tests and the promoted singular
tests. Unit suite 250 passed; warehouse suite 16 passed. The records
BBCode golden held byte-identical. The ESPN almanac goldens re-anchored
on 3 of 19 tabs (10 lines total, anchor 2026 Week 7): four
points/points-per-game cells at float-summation boundaries, one of which
re-sorted a single row inside a ranked overflow block -- the documented
`.x5` residual class, no structural or identity change. CBS output is
content-verified against real MLB history and reconciles against the
platform's own totals (594/594 exact on the 2025 pitcher recompute; 0.0
residual on all 8,185 reconciled player-seasons).

---

## [1.2.0] — 2026-05-30

First product-feature release after the v1.1.x refactor line. Two surfaces:
the Home tab becomes a navigation-hub dashboard, and a net-new Draft Recap
tab adds the draft board + draft-value analysis (a new ESPN extract through
dbt to the Sheet). Reviewed tab-by-tab against the live Sheet; the almanac
byte-diff fixtures were re-baselined for both. No separate `Phase X.Y
Documentation.md` — the retrospective lives in `v1.x Handoff.md` "Status at
v1.2.0 ship," consistent with the v1.1.x releases.

### Added

- **Home two-band redesign.** Left navigation band (links to Records,
  Matchup History, the per-team pages, and Draft Recap; a points glossary;
  an all-time All-League Team) beside a right band with the All-League Team
  of the Week and Season-to-Date — each carrying two player-only "Total-Pts
  Best (incl. bench & FA)" deviation columns that surface where a bench / FA
  player out-produced the active pick at a slot. Nav links are live in-sheet
  `#gid` hyperlinks resolved at write time (a two-pass write: build the
  tabs, read their gids, render Home last), so they work on a brand-new
  sheet with no hardcoded URLs.
- **Draft Recap tab.** A new ESPN draft extract (`league.draft` →
  `RAW.DRAFT_PICKS`, folded into `--settings-only`) feeds `stg_draft` →
  `mart_draft_board`, joining every pick to its drafting team and the
  player's total season production. Side-by-side Best Value / Biggest Busts
  leaderboards (value = where a player was drafted vs. how they produced)
  over a round × team draft board with per-round Min / Median / Max and a
  production-keyed color scale. Keepers are flagged and ordered / valued by
  production (this is a keeper snake draft).
- **Owner display names** propagate through the mart (nickname > proper
  name), surfaced on the almanac Home, Records, and the recap.

### Changed

- **"Team Weeks" tab renamed to "Matchup History."**
- **Home All-League slash line** reads `.294/.390/.559`; the boxscore is a
  hyperlink on the Points cell; fantasy teams show their abbreviation.

### Fixed

- **Live-write formatting that the byte-diff never exercised.** The
  `--no-sheets` preview path skips the Sheets formatting code, so several
  bugs there were invisible: a `len(HOME_HEADER)` NameError on the
  new-Home-tab path; missing `almanac_write` imports that had silently
  skipped all per-team-tab and Matchup History conditional formatting since
  the v1.1.1 module split; and team-tab header rows mis-right-aligning the
  points glossary in column Q.

Verification: dbt build PASS=9 on the new draft models + tests; pytest
warehouse 16 passed (almanac byte-diff + recap / records goldens); pytest
default 144 passed (5 preexisting `test_almanac_sheets.py` failures
unrelated to this release). Open design calls (snake-draft presentation,
pick trading, a cross-session float-summation follow-up) are in
`v1.x Handoff.md`.

## [1.1.2] — 2026-05-27

Team-tab polish on the v1.1.1 almanac. No dbt changes and no
selection-logic changes — just rendering fixes and explanatory copy on
the per-team "Best Lineup" tabs, after QA'ing them in the live Sheet.

### Fixed

- **Points-per-game formats to two decimals.** `ppg` was rendering raw
  float precision (`3.232727…`, `1.0214`); now reads `#.##`.
- **Pitcher decision line shows W-L-Sv.** Was "decisions or saves,
  whichever is larger," which dropped W-L for closers and saves for
  swingmen who had both. Now `6-4` when there are no saves, `2-1-15`
  when the pitcher recorded saves.

### Added

- **Slot-fill explanation + points glossary on every team tab.** A short
  header note explains that the starting lineup is filled by Active
  Points at each eligible position and bench / IL / other by Total
  Points while rostered; a Total / Active / Inactive Points glossary
  sits over the all-time side; and a callout notes that points use
  current-season scoring (with an invite to request points as they were
  awarded at the time).

Verification: dbt unchanged; pytest warehouse green (16 passed,
including the regenerated almanac byte-diff); pytest default 144 passed
(5 preexisting `test_almanac_sheets.py` failures unrelated to this
release).

## [1.1.1] — 2026-05-27

Almanac refactor + optimal-team reframe. This release was scoped as a
refactor-only pass against the v1.1.0 golden TSV snapshot, and the first
half delivered exactly that: reusable analytical SQL moved into dbt
contracts, `output/almanac_sheets.py` split along data/logic/rendering
lines, output held byte-identical. The second half intentionally broke
byte-identity — a new optimal-team primitive reframes the All-League
Team and the per-team tabs from "who was actually slotted" to "the best
lineup the team could have fielded," scored on the calculated
(cross-season-normalized) points lens.

So this is the rare patch release that intentionally shifts product
output. The shift was reviewed tab-by-tab against the live Sheet before
the fixtures were re-baselined.

### Added

- **`get_optimal_team` primitive.** Parameterized "best lineup for any
  (timespan, scope, points_type)" dispatcher over the new
  `int_player_position_pts` model (per-position points via
  `LATERAL FLATTEN` of `eligible_slots`). Gap-based selection — fill the
  slot where the second-best option hurts most — with a disjoint-stat-
  categories rule so two-way players (Shohei) can fill one hitting and
  one pitching slot without double-counting.
- **`int_player_position_pts`** — per-position points accumulation at
  matchup grain, calculated-points lens.
- **`mart_team_matchup`** — wide matchup-grain view carrying opponent
  line, head-to-head margin, combined totals, and league-wide per-week
  averages. First consumer: the Team Weeks tab.
- **`fct_player_season_performance`** — slot-bearing season-grain rollup
  of the weekly player fact; foundation brick of the player-profile
  layer, carrying both calculated and platform point lenses.
- **Almanac byte-diff regression.** `tests/test_almanac_byte_diff.py`
  diffs generated TSVs against `tests/fixtures/almanac_v1_1_0/`,
  refreshable via `REGENERATE_BASELINES=1`.

### Changed

- **All-League Team + per-team Starters are now optimal-team selected.**
  The Home tab and every per-team tab fill their lineups by best-possible
  production at each position rather than by who was actually slotted
  there most often.
- **Per-team tabs reframed as "Best Lineup — current season + all-time."**
  Side-by-side current-season and franchise-history best lineups; Bench /
  IL / Other ranked by total rostered production (active + bench/IL
  points) to surface "could have helped but was blocked or benched"; an
  asterisk in the team column marks players still on the franchise.
- **Optimal teams scored on the calculated points lens** rather than
  platform points, so historical lineups answer "who would have done
  well under the league's current scoring" instead of "who scored well
  under whatever ESPN config was live at the time."
- **Rate-record qualifiers are seed-driven.** `mart_stat_leaderboard`
  rate-stat thresholds (`qualifier_stat` / `qualifier_min`) moved from
  hardcoded Python constants into `stat_classification.csv` / `dim_stat`.

### Fixed

- **Live-write crashes masked by the `--no-sheets` preview path.** Two
  separate breakages in `output/almanac_write.py` — missing
  `os`/`re`/`records` imports, and a dangling
  `get_all_league_team_season_to_date` call left behind by the
  dispatcher consolidation — surfaced only on the live Sheets write,
  which the byte-diff regression doesn't exercise. Both fixed.
- **Optimal-team rows render in canonical slot order.** The gap-based
  selector returns picks in fill order; it now sorts to baseball-card
  order (C, 1B, 2B, 3B, SS, IF, LF, CF, RF, OF, DH, UTIL, SP\*, RP\*)
  before returning, so hitters and pitchers no longer interleave on any
  tab.

### Internal

- **`output/almanac_sheets.py` split** into `almanac_data` (SQL + data
  shaping), `almanac_logic` (selection + tab-row building),
  `almanac_render` (formatters), and `almanac_write` (Sheets API).
  `almanac_sheets.py` is now a thin facade re-exporting every public
  name, so existing import sites keep working unchanged.
- **New dbt contracts route the almanac through the mart layer** instead
  of reaching past it into intermediates.

Verification: dbt build clean (PASS=161 / WARN=0 / ERROR=0 / NO-OP=4);
pytest warehouse green (16 passed, including the almanac byte-diff and
the records + recap BBCode goldens); pytest default 144 passed (5
preexisting `test_almanac_sheets.py` failures unrelated to this release).
Per-team and Home tabs visually QA'd against the live Sheet.

## [1.1.0] — 2026-05-22

League almanac release. This is the first v1.x product expansion after
the stable BBCode/records foundation: a browsable Google Sheets workbook
for league members, with a Home tab, curated Records tab, Team Weeks
archive, and one active-stats tab per fantasy team.

This ships intentionally before the almanac internals are fully refactored.
The output is product-ready enough to collect league feedback; the known
architectural debt is that several almanac analytical queries still live
inside `output/almanac_sheets.py` instead of dbt contracts. v1.1.1 is
reserved for a refactor-only pass against the golden TSV snapshot captured
in this release.

### Added

- **League almanac Google Sheets surface.** New
  `output/generate_almanac_sheet.py` entry point and
  `output/almanac_sheets.py` writer build a multi-tab workbook:
  `Home`, `Records`, `Team Weeks`, and one team active-stats tab per
  fantasy team.
- **Home tab.** Surfaces an all-league team of the week and
  season-to-date all-league lineup, filled from the league's configured
  roster shape instead of hardcoded lineup assumptions.
- **Curated Records tab.** Side-by-side current-season and all-time
  record book covering score records, team hitting/pitching records,
  rate records, and lineup-slot records. Period cells link to ESPN
  matchup-view boxscores; rate records use the documented 225 AB /
  50 IP thresholds at output time.
- **Team Active Stats tabs.** Each team gets a browsable current-season
  and all-time roster-history view: likely starting lineup by active
  usage, bench/IL/other sections, current fantasy-team abbreviations,
  rostered days, games, active points, bench/IL points, PPG, and compact
  hitter/pitcher stat lines.
- **Team Weeks tab.** Wide team-week archive with scored hitting and
  pitching stats, calculated hitting/pitching/total points, margin,
  matchup totals, league averages, matchup links, color scales, hidden
  helper columns, and record emphasis for standard-length weeks.
- **Roster-settings extraction.** `extract/extract.py` now persists the
  ESPN `rosterSettings` payload into `raw.roster_settings`.
- **Roster-setting marts.** New `dim_roster_slot_counts` exposes lineup
  slot counts and position maximums; new `mart_daily_roster_snapshot`
  gives roster-history consumers a shell that includes zero-stat
  rostered players.
- **Golden almanac snapshot.** `tests/fixtures/almanac_v1_1_0/` captures
  the v1.1.0 TSV output as the byte-diff baseline for the planned
  v1.1.1 refactor.

### Changed

- **Boxscore links now target matchup view.** Almanac links use ESPN's
  `view=matchup` URL shape so they open the full matchup view rather
  than the final scoring day.
- **Generated preview artifacts are ignored.** `.gitignore` now excludes
  almanac preview directories and ad-hoc TSV reports under `output/`.
- **dbt catalog metadata.** Exposures and overview docs now declare the
  almanac as a first-class downstream consumer alongside the recap and
  records report.

### Internal

- **Almanac unit coverage.** `tests/test_almanac_sheets.py` covers the
  roster-fill logic, record-tab shaping, team-week shaping, and formatting
  helpers that are most likely to drift during the v1.1.1 refactor.
- **Known v1.1.1 refactor target.** Move reusable analytical SQL out of
  `output/almanac_sheets.py` into dbt contracts where appropriate
  (`mart_team_matchup`, player/team/slot history), split data/logic/
  rendering modules, and keep the generated TSV snapshot byte-identical.

Verification: dbt build clean (PASS=140 / WARN=0 / ERROR=0 / NO-OP=4,
including 120 data tests and 4 exposures); dbt static docs regenerated;
pytest default green (148 passed, 15 deselected); pytest warehouse green
(15 passed, 148 deselected).

## [1.0.2] — 2026-05-19

DAG hygiene + dbt-architecture cleanup release. No consumer-visible
behavior change — the recap and records report render byte-identical
output pre- vs. post-refactor against the golden BBCode regression.
What changed is internal: the dbt DAG got a contract-layer cleanup
that separates "config seeds" from "data marts," promotes a real
weekly fact out of the intermediate layer, and adds a daily fact that
gives output scripts a mart-layer entry point instead of reaching
back into intermediates.

Strictly per semver this is arguably a 1.1.0 (new public models
shipped). Per the maintainer's reading, v1.x stays reserved for the
`dim_player` flagship inflection where consumer-visible behavior will
shift; 1.0.x stays "polish + refactor" releases. Treat 1.0.2 as
"the v1.0 architecture, redrawn."

### Added

- **`dim_stat`** — mart-layer dimension over the `stat_classification`
  seed. Adds a `leaderboard_name` column (seed-name post translation:
  `1B` → `SINGLES`, `30` → `CYC`, `64` → `SHO`, etc.) and carries all
  other seed columns through unchanged. Single source of truth for the
  seed → leaderboard name translation; `output/stat_catalog.py` and
  the `mart_stat_leaderboard` compile-time loop both read from here.
- **`dim_matchup_period`** — mart-layer dimension over the
  `matchup_schedule` seed. Carries calendar metadata
  (`is_abnormal` / `is_playoff` / `playoff_round` / start/end dates)
  for consumer-side reads.
- **`fct_player_daily_performance`** — mart-layer fact over
  `int_player_daily`. Exposes per-day data (counting stats, point
  contributions, per-day platform totals, per-day metadata) to
  consumers via a contract layer. Adds `performance_status` and
  `wasted_bucket` columns derived centrally from `lineup_slot` and
  inherited up through the weekly facts.
- **Schedule columns on the four weekly facts.** `is_abnormal`,
  `is_playoff`, and `playoff_round` denormalized from
  `dim_matchup_period` onto `fct_weekly_player_active_performance`,
  `fct_weekly_player_inactive_performance`,
  `fct_weekly_team_active_performance`, and
  `fct_weekly_team_inactive_performance`. Consumers can filter abnormal
  weeks and render week labels directly off fact rows without a
  schedule-lookup dict.

### Changed

- **`int_player_weekly_performance` promoted to
  `fct_weekly_player_performance`.** Renamed and moved from
  `dbt_league/models/intermediate/` to `dbt_league/models/marts/`.
  Re-sourced from `fct_player_daily_performance` so the
  daily-to-weekly DAG edge is load-bearing rather than a side branch
  off the int layer. Materialized as a table (was a view) since two
  downstream facts (active, inactive) read from it on every
  `mart_stat_leaderboard` query.
- **Consumer migration.** `output/generate_summary.py::get_wasted_
  points` and several `output/league_notes.py` callouts repointed from
  `int_player_daily` to `fct_player_daily_performance`.
  `output/records_data.py::load_schedule_lookup` repointed from
  `matchup_schedule` to `dim_matchup_period`. `output/stat_catalog.py`
  repointed from `stat_classification` to `dim_stat`, and helpers
  consolidated to read `leaderboard_name` directly from the dim
  (rather than re-applying the Python-side `SEED_TO_LEADERBOARD`
  mapping).
- **`mart_stat_leaderboard`:**
  - Four `INNER JOIN matchup_schedule ... WHERE s.is_abnormal = false`
    patterns (one per source CTE) simplified to
    `WHERE f.is_abnormal = false` against the denormalized column on
    each fact.
  - Compile-time `run_query` switched from `ref('stat_classification')`
    to `ref('dim_stat')`; duplicate CASE block deleted. The seed →
    leaderboard name translation now lives in exactly one place
    (`dim_stat.sql`).
  - `current_year` CTE switched from `source('raw', 'box_scores')` to
    `ref('fct_weekly_team_active_performance')`. One fewer raw-source
    edge in the catalog DAG.
- **`mart_league_weekly_benchmarks`:** dropped the
  `matchup_schedule` JOIN that existed purely for the `is_abnormal`
  filter; now uses the denormalized column on the weekly team fact.
- **`output/records_data.py::league_history_count`:** dropped the
  `matchup_schedule` JOIN for the same reason.
- **dbt exposures** (`dbt_league/models/exposures.yml`) routed through
  the new mart-layer contracts (`dim_stat`, `dim_matchup_period`,
  `fct_player_daily_performance`) instead of the old `int_player_daily`
  / `matchup_schedule` / `stat_classification` direct references.
  `weekly_recap` gains `mart_league_weekly_benchmarks` (added at v1.0.1
  but never declared on the exposure).
- **`tests/capture_row_counts.py`:** `MODELS` list updated for the
  rename and additions.

### Removed

- **`SEED_TO_LEADERBOARD` constant + `to_leaderboard_name` function**
  from `output/stat_catalog.py`. Replaced by `dim_stat.leaderboard_
  name`. Three corresponding tests in `TestToLeaderboardName` deleted;
  translation coverage retained transitively via
  `TestDisplayMap::test_translation_applied`.

### Internal

- `BRAINTHOUGHTS.md` private working-notes doc convention: four-section
  structure (Wishlist / Clarifications / Discussions and Tweaks /
  Interview Questions). Reviewed at every push; entries never deleted
  except under a narrow "CLARIFICATIONS doesn't need to preserve
  example-bound details about replaced architecture unless the
  framing is a teaching moment" carve-out.

Verification: dbt build clean (116 PASS / 0 ERROR with the new models
and tests); pytest tests/ green (113 passed); pytest tests/ -m
warehouse green (15 passed including byte-diff golden BBCode
regression — confirms the refactor is consumer-side transparent).

## [1.0.1] — 2026-05-18

A v1.0 polish release. Strictly speaking the changes here include
several new features that would justify a v1.1.0 under a strict
semver reading — record-surfacing for NEGATIVE_POINTS, the Hit-for-
the-Cycle stat, a league-wide benchmarks mart, an always-on "League
This Week" recap line, eight new league_notes callouts, and key-pair
Snowflake auth. The maintainer chose to land them as a 1.0.x patch
to keep the v1.x label reserved for a more meaningful structural
inflection (the player-entity flagship). Treat 1.0.1 as "polish that
grew."

### Added

- **New tracked records.** `NEGATIVE_POINTS` (gross-negative-production
  rollup, already on the four facts) and `CYC` (Hit for the Cycle, new
  wide column propagated through `int_player_daily`, `int_player_weekly_
  performance`, and all four facts) promoted to `is_record_candidate=
  true` and surfaced in the records report.
- **`League This Week:` always-on summary line.** First line of the
  weekly recap, surfaces the league's mean overall / hitting / pitching
  points alongside the historical ranking ("273.4 (2nd of 30) points
  overall …"). Renders every week regardless of whether anything
  noteworthy fired; foregrounds league-level context as a baseline.
- **Eight new league_notes callouts:**
  - `cycles` — per-player cycle announcement with cumulative history
    ordinal and "first of the season" flourish.
  - `no_quality_starts` — teams that started at least one SP but
    produced zero QS; cumulative 0-QS-with-starts ordinal.
  - `hr_streak_active` — teams whose ≥7-day HR streak is still alive
    at MP end; cites all-time league record for context.
  - `hr_streak_ended` — streaks of ≥10 consecutive HR-days that broke
    in the recap MP.
  - `hero` — second-banana-margin walk-off lens: a player whose
    individual outperformance vs. their #2 single-handedly closed the
    margin in a narrow win.
  - `scapegoat` — symmetric loss-attribution lens: a player whose
    negative output exceeded the loss margin.
  - `mismatch` — top vs. bottom scorer in the same head-to-head
    matchup; cumulative-margin-rank ordinal.
  - `no_negative_days` — teams where every active player-day had
    `platform_points ≥ 0`; "first of the season" flourish on first
    qualifying team.
  - `hot_week` / `cold_week` — league-level outlier callouts driven
    by the new benchmarks mart.
- **`mart_league_weekly_benchmarks`** — aggregate of league-week means
  + percentile rank within league history for overall / hitting /
  pitching points. Powers the always-on `League This Week:` line and
  the hot/cold-week callouts. Future surfaces (frontend, dashboard)
  read from one mart instead of recomputing.
- **`output/league_notes.py` registry pattern** is now the single home
  for all conditional flavor callouts (matchup-outcome lenses migrated
  in from inline `generate_summary.py` definitions). `render_callouts`
  inserts a blank-line separator between callouts that fire, preserving
  the prior inline rendering.
- **Snowflake key-pair authentication** support in `output/db.py` and
  `extract/extract.py`. Required after MFA enforcement on the account;
  password-based auth fails with an interactive MFA prompt the
  connector can't satisfy. `SNOWFLAKE_PRIVATE_KEY_PATH` + optional
  `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` env vars; full walkthrough in
  SETUP.md §4.
- **Recap polish:** "None recorded yet (across N team-weeks)" rendering
  for records with floor-zero values (no-hitters, perfect games,
  cycles) instead of the ambiguous "0 across N team-weeks" form;
  conditional Top Scorer line that suppresses when the overall winner
  duplicates Top Hitter or Top Pitcher.
- **Random seed determinism.** `random` seeded per-recap so varied-
  template callouts (e.g., `hr_drought`) pick the same phrasing across
  rebuilds of the same MP.
- **dbt docs catalog hosted via GitHub Pages** — initial publish at
  https://kyledawson24.github.io/fantasy-league-almanac/.

### Changed

- **`is_always_tracked` seed column → `auto_tracked`** with corresponding
  rename of `stat_catalog.get_always_tracked()` →
  `get_auto_tracked()` and the `always_tracked` parameter on
  `records_logic.should_track_record` → `auto_tracked`. The new name
  separates "tracked regardless of league scoring settings" cleanly
  from the implicit "tracked because the stat is scored" pathway.
- **Mislabeled `stat_name='CYC'` seed row** (ESPN stat ID 31, a
  non-cycle daily-achievement flag) renamed to `STAT_31` so the real
  cycle stat (id 30) can own the `CYC` leaderboard column.
  `stg_player_stat_breakdowns` now filters wrapper-emitted `'CYC'`
  rows so the seed FK invariant holds without the mislabel row.
- **Score totals rounded at the fact layer.** `calculated_*`,
  `platform_*_pts`, `platform_points`, and `negative_points` rounded
  to 1 decimal at the player-fact layer; team-fact totals inherit
  exactness from `SUM(NUMBER)` arithmetic so the team_total =
  SUM(players) invariant holds. Kills the cosmetic 126.9 ↔ 127.0
  wobble seen across `--full-refresh` rebuilds.
- **`records_logic` import-pure** with respect to the data layer. The
  only logic→data import (`count_value_occurrences`) replaced with a
  `count_fn` parameter injected by `records.get_records_with_
  contributors`. The saturated-tier branch is now unit-testable
  without a Snowflake round-trip; four new tests cover it.

### Fixed

- **`no_quality_starts` historical ordinal drift.** The cumulative
  count used `league_history_count('team', 'QS', 0)`, which includes
  team-MPs where no SP started at all — so the ordinal drifted upward
  from the trigger's actual definition. Fix mirrors the trigger's
  `lineup_slot='SP' AND games_played≥1` filter in the historical
  count.
- **`hr_streak_active` "new record" claim on tied streaks.** Said "a
  new league record" when the longest active streak matched the
  existing record. Two issues — (a) `record_len` included the active
  streak itself, and (b) the comparison was `>=` rather than `>`.
  Fix: exclude the current longest active run from the prior-record
  calculation and split into explicit new / tied / existing-stands
  branches.
- **Hero template trailing whitespace.** Two templates ended with a
  space inside the string (rendered into baselines); two more had
  trailing whitespace after the closing quote. Cleaned up.

### Removed

- Parked `_replace_tab` formatting-preservation change in
  `output/sheets_writer.py` discarded — pending Sheets surface
  redesign supersedes the in-place-update logic.

## [1.0.0] — 2026-05-13

First stable release. Phase 7 was a portfolio-prep rearchitect spanning
an 8-step dbt overhaul (Steps A–H), a three-way split of `records.py`,
repo hygiene, and public documentation. The active dbt DAG was reduced
from 9 to 7 business-logic models with a symmetric active/inactive
split at both grains, stat metadata moved from scattered Python
dictionaries into a seed-driven catalog, and the project gained tests
plus release tooling.

### Added
- Seed-driven `stat_classification.csv` (97 rows, 13 columns) as single
  source of truth for stat metadata: display names, abbreviations,
  polarity, record-candidate flags, derivation expressions. Adding a
  tracked stat is now one CSV row, not edits in five Python locations.
- `output/stat_catalog.py` — six `lru_cached` accessors over the seed
  (`get_display_map`, `get_abbrev_map`, `get_polarity_map`,
  `get_always_tracked`, `get_record_candidates`, `get_derived_exprs`).
- `output/records_data.py` + `output/records_logic.py` — `records.py`
  split into Snowflake-querying layer + pure consumer-side rules layer
  (was 930 lines / 22 functions; now three files with backward-compat
  re-exports so consumer scripts and tests didn't have to change).
- `output/db.py` — consolidated Snowflake connection wrapper.
- 112 pure pytest + 15 warehouse-marked tests, including byte-diff
  golden-output regression against pinned BBCode baselines for both the
  weekly recap and the all-time records report.
- `tools/regen_stat_classification.py` — idempotent seed-regen tool.
- MIT LICENSE, this CHANGELOG, README, SETUP, ROADMAP, and dbt docs
  (hosted via GitHub Pages).

### Changed
- Active facts renamed to `fct_weekly_{team,player}_active_performance`;
  new symmetric inactive facts `fct_weekly_{team,player}_inactive_performance`.
  Active = fantasy reality (what the manager actually played); inactive =
  MLB reality (what the player did regardless of fantasy rostering).
- `mart_stat_leaderboard` rebuilt via seed-driven Jinja UNPIVOT over all
  four facts; previously a hand-maintained UNION block in SQL.
- `performance_status` partition column added to the mart so consumers
  can filter active-vs-inactive symmetrically.
- requirements.txt converted UTF-16 LE → UTF-8 LF, re-sorted
  alphabetically, `pytest>=7.0` added.

### Removed
- Six dbt models retired during the rearchitect; the active DAG is now
  7 business-logic models (down from 9). Full list and per-model
  rationale in `Phase 7 Documentation.md`.
- Scattered Python polarity / always-tracked dicts in `records.py`;
  values now live in the seed.

See `Phase 7 Documentation.md` for architectural detail.

## [0.6.0] — 2026-05-08

### Added
- Google Sheets sink as an opt-in second consumer surface for the
  records report. Three-tab layout: rank-1 records, top-5 with
  contributors, full leaderboard dump.
- `output/records.py` — consolidated SQL and polarity-filter logic in
  one place so both consumer scripts (recap + records report) share a
  single data-access layer.
- Tracked-stats expansion to surface derived counters (PA, SB-CS, W-L,
  SV-BLSV) and rate stats (ERA, WHIP, K/9, K/BB, HR/9, BB/9) at team grain.
- Tie-collapse rule: when more entities tie than the visible top-N,
  collapse into a single "N tied at X" row, with inline holders preserved
  for small tiers (≤3 members).
- Bulk contributor fetches: one batched SELECT per grain rather than N
  round-trips.

### Changed
- Mart record-direction values renamed: `best`/`worst` → `most`/`fewest`.
- Records section displays owner names; recap section doesn't —
  separation of audience (recap is fast-glance; records reward attribution).

See `Phase 6.3.3 Documentation.md`.

## [0.5.0] — 2026-05-04

### Added
- "Records set this week" callouts in the recap, surfacing new or tied
  records broken during the just-played matchup.
- Polarity-aware record filtering: negative-stat "fewest" records
  suppressed where they'd trivially be zero; "always-tracked" override
  for stats that should surface regardless of polarity.
- Recap restructured around the player-card → team-card → records
  narrative.

### Changed
- League records keyed off `calculated_*` columns (project-owned,
  scoring-weight-derived) rather than `platform_*` (ESPN-reported,
  drift-prone across rule changes). Platform values retained for audit.
- Wasted-points scope extended to active players who scored negative
  points — the manager could have benched them rather than letting the
  negative drag the lineup. Generalizes "wasted" as points achievable
  via free lineup changes (start a bench player, sign a free agent,
  sit a negative scorer) that weren't made.

See `Phase 5.0 Documentation.md`.

## [0.4.0] — 2026-05-02

### Added
- Wasted-points concept introduced: per-player-per-matchup tracking
  of points the manager could have captured but didn't — initially
  scoped to bench-side waste (productive players left on the bench
  while their owner started someone else).
- Slot validity model distinguishing roster slot (e.g., "OF") from
  player eligibility (e.g., "OF, 1B") to handle Ohtani-class two-way
  and multi-eligible players correctly.
- Kona anti-join pattern: point-in-time roster status reconstruction
  for free-agent tracking without requiring a transaction log.

### Changed
- Player roster surface migrated onto the `kona` wrapper for richer
  per-player game-level data.

See `Phase 4.0 Documentation.md`.

## [0.3.4] — 2026-05-01

### Changed
- Raw-always extraction: simplified the doubleheader-fix code path from
  hybrid (capture extra splits only on detected DH days) to unconditional
  per-day-per-platform-level raw capture. Same downstream output, cleaner
  mental model.

## [0.3.3] — 2026-04-30

### Fixed
- Silent doubleheader stat-overwrite bug in `espn-api`'s `box_scores()`
  wrapper: the wrapper built a dict keyed by `scoringPeriodId` and
  silently dropped one game when ESPN returned multiple splits for the
  same period. Root-caused via raw-API inspection (preserved in
  `archive/phase_3.3_doubleheader_debug__turang_raw.json`); fix is to
  capture ESPN's pre-aggregation rows directly and aggregate ourselves.

See `Phase 3.3 Documentation.md`.

## [0.3.2] — 2026-04-29

### Added
- `calculated_points` columns derived from scoring-settings seed × stat
  counts, computed in dbt so the project owns the authoritative number
  rather than relying on `platform_points` reported by ESPN.
- `stg_scoring_settings` staging model parsing ESPN's settings JSON.

### Changed
- Output scripts surface `calculated_*` as the canonical "points" number;
  `platform_*` retained for ESPN-side audit comparison.

See `Phase 3.2 Documentation.md`.

## [0.3.1] — 2026-04-26

### Added
- "Wide convergence" facts: `fct_weekly_player_stats` and
  `fct_weekly_team_stats` combine counting stats with derived rates in
  one fact per grain. Resolves the prior counting-vs-rate cross-mart
  dependency flagged in Phase 2.0.

See `Phase 3.1 Documentation.md`.

## [0.3.0] — 2026-04-24

### Added
- Stat-level league records: most HRs, most Ks, most SBs, etc., with
  top-10 leaderboards scoped to all-time and current season.
- `mart_stat_leaderboard` view.
- Rate-stat macro library: AVG, OBP, SLG, OPS, ERA, WHIP, K/9, K/BB
  defined once, applied at any grain the analyst needs.
- Incremental dbt models for the weekly stat facts (composite
  `(season_year * 100 + matchup_period)` scalar; reprocesses the latest
  period to handle late-arriving stat corrections).

See `Phase 3.0 Documentation.md`.

## [0.2.1] — 2026-04-22

### Added
- Owner names end-to-end: player → owner mapping wired through staging,
  marts, and output script (recap + records sections).
- `owner_nicknames.csv` seed for preferred display names.

### Changed
- Mart consolidation: merged two-mart team-scores structure to resolve
  the cross-layer dependency flagged in 2.0.
- Player points model reworked at weekly grain to handle two-way
  players (Ohtani) correctly.

See `Phase 2.1 Documentation.md`.

## [0.2.0] — 2026-04-20

### Added
- Player-level contribution callouts in the weekly summary: top
  contributors per Best/Worst Hitting/Pitching team.
- Records section: current-season + all-time team records for
  Best/Worst Matchup Total, Hitting, Pitching.
- Footnotes for abnormal-week exclusions and scoring rule changes.

See `Phase 2.0 Documentation.md`.

## [0.1.0] — 2026-04-19

### Added
- Initial end-to-end pipeline: ESPN Fantasy API → Python extractor →
  Snowflake raw JSON → dbt staging/intermediate/mart → Python BBCode
  summary generator.
- Weekly summary: Best/Worst Overall, Hitting, Pitching with the
  conditional Tough Luck, Lucky Bastard, and Fair-and-Just-League
  callouts.
- dbt project scaffold: staging (`stg_box_scores`), intermediate
  (`int_team_daily_scores`, `int_weekly_matchups`), mart
  (`fct_weekly_team_scores`).

See `Phase 1.0 Documentation.md`.
