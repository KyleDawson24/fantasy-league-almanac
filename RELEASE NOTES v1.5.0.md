## [1.5.0] — 2026-07-21

### Highlights

The short version, for people who read the almanacs rather than build them.

- **Two platforms, two league formats.** The pipeline runs end to end for
  both a head-to-head ESPN league and a season-long points league on CBS,
  including a full almanac for the CBS league reaching back to its first
  season in 2001.

- **Player production is priced from real MLB box scores.** Every rostered
  player is matched to his official MLB id, and each game is scored from
  MLB's own stats under your league's scoring rules. A canonical stat
  catalog sits alongside it, so a stat means the same thing on both books
  and the record boards track it automatically instead of by a hardcoded
  list. The practical effect: the record book covers players the fantasy
  platform itself no longer carried — Bonds, Randy Johnson, anyone who
  retired before the platform's own archive began.

- **Player-card links throughout the almanac.** Record holders, All-League
  Team picks, every team roster, and the draft board render player names as
  links to their Baseball Reference pages.

- **Advanced Standings.** A new tab on both books: every scored stat as a
  per-week average rather than a raw season total, points by lineup slot,
  rank-by-period charts, and a season-finish matrix with champions marked.

- **Production by acquisition channel.** How much of each team's output
  came from the draft, from keepers, from trades, and from free agency.

- **Draft Recap on both leagues.** The current board with value and bust
  leaderboards, all-time boards by draft slot, and year-by-year draft
  classes under an honest note about which years have true pick order.

- **ESPN's trading block, collected in one place.** The live block plus
  per-team interest counts, pulled into a single view instead of checked
  team by team.

- **Overrides you control.** Preferred owner names, player nicknames, and
  franchise continuity — declare that two team ids are the same franchise
  and a quarter-century of history reads as one team, however many times
  the platform's internal ids changed underneath it.

- **A season-to-date report,** built for the All-Star break and the end of
  the season but runnable any week, plus new record surfaces: a Hall of
  Fame with years of service, a Wasted Hall of Shame ranked by points left
  on the bench, and records by lineup slot so a utility player ranks
  against his own slot rather than the whole hitter pool.

### Overview

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
  contested attributions hold at 0 of 977k rows.

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
  .x5-boundary values can't flip between two reads with no data change.

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
