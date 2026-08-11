# Roadmap

This project shipped v1.0.0 on 2026-05-13, v1.0.1 on 2026-05-18
(polish + flavor expansion), v1.0.2 on 2026-05-19 (DAG hygiene and
dbt-architecture cleanup; refactor-only), and v1.1.0 on 2026-05-22
(Google Sheets league almanac). It has since shipped through v1.5.0
(the multi-league foundation and a 25-year CBS almanac), v1.5.1
(a correctness pass over the CBS record book, 2026-07-25), and v1.6.0
(2026-07-30, the pre-port anchor release: Total-Points vocabulary,
Advanced Standings banners, a re-render hygiene fix, and a determinism
sweep). Most recently, v1.8.0 (2026-08-10) proved the warehouse-free
local journey end to end: a fresh clone with league credentials and no
warehouse account of any kind reaches rendered preview files on disk --
ESPN only, not yet a shareable workbook, and not yet stranger-proof.
See CHANGELOG.md for the per-release entries.

**In flight right now:** the stranger's Google-workbook journey (MLB-209)
-- the service foundation is on `main`, and what remains is wiring it
into the full onboarding path -- and deriving the matchup schedule from
the platform's own settings instead of a hand-maintained seed (the "Data
wiring" item below).

The items below are what's still on deck, organized by
priority and ambition. v1.x = incremental polish on the current
architecture; v2.0 = structural change.

## Now (v1.x -- incremental polish)

Low-risk changes building on the current architecture. Most would ship in
a single afternoon.

### Almanac refactor (v1.1.1)

> **Overtaken by events (noted 2026-07-31, refreshed 2026-08-11).** This
> block was written when v1.1.0 was the current release; the project is
> now at v1.8.0 and the engine port has shipped. The items below are
> still wanted, but "the next release" no longer describes any of them.
> Re-prioritizing this section is its own pass.

- **Byte-identical almanac refactor.** v1.1.0 shipped the product surface
  first so the league can review it. This would be a pure
  refactor against `tests/fixtures/almanac_v1_1_0/`: no tab/column
  changes, only relocation and cleanup.
- **Build a wide `mart_team_matchup` view.** One row per team-week with
  the team's full active line, the opponent's full active line, margin,
  and combined matchup totals. Team-week surfaces read it directly;
  matchup-record consumers dedupe the two opponent-swapped rows when
  ranking a matchup as a single game.
- **Start the player/team/slot performance layer.** Build the pure
  performance fact needed for career-with-team and team active-stats
  views: season/team/player/slot counting stats with rates derived
  downstream. Keep acquisition, transactions, and callout counts as
  separate future joins rather than nullable columns on this fact.
- **Split `output/almanac_sheets.py`.** Mirror the Phase 7 records split:
  data reads, selection logic, rendering/formatting, and Sheets-write
  orchestration should be distinct modules.
- **Keep the selection logic in Python.** All-league and per-team roster
  filling should share one configurable roster-fill function; do not add
  an all-league-candidates mart unless a second consumer needs it.

### Data wiring

- **Wire `owner_nicknames` seed into models.** The seed exists but isn't
  joined; output scripts read it ad-hoc. Joining into the staging /
  intermediate layer would let the mart carry owner display names
  directly.
- **Identify playoff-contention teams during playoff weeks.** The
  calendar layer correctly flags playoff weeks (`is_playoff=true` on
  `matchup_schedule` rows), but during those weeks all teams still play
  -- some in the actual playoff bracket, some in the consolation
  tournament. The records output today can't distinguish a Finals MVP
  performance from a 13th-place consolation week. Identifying which
  `(team, season, matchup_period)` tuples represent actual playoff
  contention would let records filter or annotate accordingly. Scoped
  as v1.x for now; may slide to v2.0 depending on what playoff-bracket
  data the ESPN API exposes (discovery TBD).
- ~~**Auto-populate `matchup_schedule` from ESPN settings API.**~~
  **DONE (MLB-235).** `matchup_schedule.csv` is no longer a required
  input for an ESPN league, and no longer supplies membership, length,
  abnormality, extraction selection or the ordinary calendar.

  What actually landed differs from the sketch above in two ways worth
  recording, because both were wrong turns the work had to find:

  - **`settings.matchupPeriods` does not carry membership.** It is a
    degenerate identity map (`{'1': [1], '2': [2], ...}`) in ESPN's own
    payload -- verified against RAW, with no wrapper in the path. The
    real membership is the KEYS of
    `schedule[].home/away.pointsByScoringPeriod` on the `mMatchupScore`
    view, which is what `RAW.MATCHUP_SCHEDULE` now stores and what the
    extract selects its weeks and scoring periods from.
  - **Dates were the hard part, and they are not in the payload at all.**
    ESPN serves scoring-period ids and no ISO dates. But the ids are
    daily, so one anchor produces the whole calendar: scoring period N is
    the season's first scoring date plus N-1 days, and a matchup period's
    start/end are its first and last scoring period. The anchor is MLB's
    own published `regularSeasonStartDate`
    (`statsapi.mlb.com/api/v1/seasons`), captured to
    `RAW.MLB_SEASON_CALENDAR`. Measured against the hand-maintained seed
    it reproduces all 44 closed periods of 2025 and 2026 exactly; a
    standing dbt test fails the build if the two ever disagree.

  `is_abnormal` is derived from the modal period length rather than
  typed, with `matchup_period_overrides.csv` remaining the sparse escape
  hatch for a genuinely odd week whose length looks ordinary. New-user
  setup friction went from "populate ~25 rows per season" to nothing.

  Still open, and deliberately not claimed: a live season-long
  points/rotisserie ESPN league. Zero- and one-matchup-period shapes are
  accepted without fabricating weeks, but that acquisition route is
  unproven until a real payload establishes it.

### New analytics surfaces (data already exists)

- **Player-entity foundation: `dim_player` + `fct_player_career`
  (v1.x flagship).** Build the player-as-entity layer the project
  hasn't had yet. `dim_player` (slowly-changing player dimension:
  current `pro_team` / `position` / `eligible_slots` / bio data) absorbs
  the per-day display metadata that consumer scripts currently look up
  ad-hoc. `fct_player_career` (career-aggregate facts: total fantasy
  teams played for, career stats rollups, transaction history derivable
  from box scores) unlocks "career milestone" callouts in the recap.
  Most data is already in the pipeline; bio/draft additions to the
  extract layer are a follow-on (see "Draft position integration"
  in Next). Framed as a v1.x "proof of progress" -- even partial
  scaffolding ships value (consumer simplification, lineage clarity)
  and lays foundation for full player-profile analytics post-v1.0.
- **`fct_team_career_stats` mart.** Career-aggregate equivalent of
  `fct_weekly_team_active_performance` -- "who has the most points scored
  for their team in league history," etc. Team-side counterpart to
  `fct_player_career` above. Especially fun for keeper-league framing.
  Data's already there; this is a new aggregation layer.

## Next (v2.0 -- substantive features)

Larger changes that would re-shape parts of the project.

> **Scoping note (resolved).** This read as a choice between Yahoo
> eligibility and the DuckDB target -- roughly equivalent in scope, and
> realistically only one per major version. The DuckDB half shipped
> early (the engine port in v1.7.0, parquet-on-disk RAW in v1.8.0), so
> Yahoo/Sleeper is what remains of the pair.

### Cross-platform portability

- **Yahoo / Sleeper extract paths.** The current `extract/` layer is
  ESPN-specific (cookies + `espn-api` wrapper). A new extract per
  platform would share the dbt + output layers if the raw shape can be
  normalized at the staging boundary.
- **Tracked-stats config externalized.** The Phase 6.3.3 stat list is
  hardcoded into mart UNPIVOT lists. Externalizing the stat-mapping to a
  YAML or seed would let other leagues with different scoring settings
  reuse the project without forking.
- **One draft builder for both leagues.** ESPN and CBS render their draft
  tabs through two entirely separate builders; the CBS one
  (`build_draft_recap_rows`) exists because CBS never joined the ESPN
  chain. Converging them retires the duplicate and stops a third platform
  from adding a third builder. The warehouse half already landed -- the
  CBS mart's columns are shaped on `mart_draft_board`'s contract -- so
  what remains is renderer work plus four contract seams (NULL
  `overall_pick` on most CBS seasons, varchar player ids, no `team_id` on
  CBS draft pages, and two different value contracts).

### Warehouse / target flexibility

- **dbt-bigquery target.** Snowflake and DuckDB are the configured
  targets. SQL is fairly portable; bigquery should require minimal model
  changes.
- **DuckDB target with parquet-on-disk extract -- shipped.** The
  `dbt-duckdb` target landed in v1.7.0 and `extract.py --raw-target
  local` in v1.8.0, so the project runs with no cloud warehouse at all
  on the ESPN path. Kept here as the record of a "Next" item that
  graduated, and because it is what makes the bigquery item above a
  third target rather than a second.

### New data sources

- **Draft position integration.** Linking each player to their league
  draft round (and ESPN ADP) enables analyses like "average points per
  season per draft round" and quick value-vs-ADP checks during the
  season. Requires a new extract path for draft data.

### Metrics framework

- **MetricFlow / dbt Semantic Layer integration.** Would formalize
  user-defined metrics ("wasted points," "calculated points") as
  declared metrics rather than column outputs. Targeted as a deliberate
  v2.0 learning exercise -- pending a fit assessment for the project's
  data shape (worth doing if metrics-layer benefits land for analytical
  use; not worth forcing if it's a square-peg-round-hole).

### Operational features

- **Multi-sink output abstraction.** BBCode (stdout/log) and Google
  Sheets are the two current consumers. A clean sink interface would let
  Discord, email, or static-HTML sinks plug in by implementing one
  protocol rather than re-threading through both consumer scripts.
- **GitHub Actions CI on PRs.** `dbt compile` + `pytest tests/` + a smoke
  build of the BBCode summary on every PR.
- **Dynamic rate-stat thresholds from lineup-slot config.** Current
  thresholds (`HITTER_AB_THRESHOLD`, `PITCHER_IP_THRESHOLD`) are
  placeholder constants. Driving them off the league's roster
  configuration would make the project portable to leagues with
  different roster shapes.

### Extract performance

- **Multi-view single HTTP call.** Combine `?view=mMatchupScore` and
  `?view=kona_player_info` into one request per matchup period.
- **Batched kona requests.** Use `filterStatsForScoringPeriodIds` to
  fetch multiple periods in one call.
- **Parallel-fire wrapper calls.** Concurrent matchup-period fetches with
  modest concurrency caps.

Combined, a full-season backfill drops from ~30 minutes to ~3-5 minutes.
Not blocking weekly cadence; matters for re-extracting historical seasons.

## Later (speculative, not yet justified)

Ideas worth exploring if the project evolves in their direction.

- **Interactive frontend.** A Tableau (or similar) dashboard with filters
  letting league members slice data themselves. High value but high
  implementation cost; gated on an honest ROI assessment vs. continuing
  to invest in the recap/Sheets surfaces.
- **Discord webhook sink.** Posts the recap to a league Discord channel
  rather than (or in addition to) ESPN's front page. Requires deciding on
  a richer non-BBCode format.
- **Slot productivity mart.** Per-slot points-per-week tracking ("how
  much productivity comes out of the SP1 slot, league-wide"). Interesting
  league-color content; unclear consumer.
- **Inactive-fact column symmetry decision.** The
  `fct_weekly_*_inactive_performance` models currently surface only
  `calculated_*` totals plus grain dims, not the full counting and
  per-stat `*_pts` columns their active counterparts expose. Two paths:
  (a) full mirror -- extend inactive facts to match the active schema and
  document accordingly; (b) intentional asymmetry -- keep inactive terse,
  formally document the omissions. Worth deciding before adding a real
  consumer that needs inactive-grain stat detail.
- **Bucket-specific inactive leaderboard view.** `wasted_bucket` is
  carried as a column on inactive `mart_stat_leaderboard` rows but
  isn't part of the ranking partition. A bucket-specific downstream view
  could filter then re-rank to power "top FA-pool calculated_points by
  week"-style records. Not built today because no consumer needs it yet.
- **ESPN `pointsAdjustment` field investigation.** Would split
  `platform_calculated_delta` cleanly into commissioner adjustments vs
  derivation drift.
- **Stat ID 30 verification.** Single observed row across two seasons;
  worth confirming whether it's a real scored stat the project should
  track or a one-off artifact.
- **Hosted multi-tenant deployment ("Path C").** Running the pipeline as
  a service for other leagues. Deferred indefinitely -- different product
  class than a single-league tool, and the lift to get there (auth,
  per-league credentials management, hosting infrastructure) is far past
  the project's current scope.

## Decided Against (deliberate exclusions)

- **Frequency-table / "Notable Frequencies" tab.** Considered and rejected
  during Phase 6.3.3; the tie-collapse pattern in the existing records
  output handles the underlying need (showing common stat values) without
  a separate consumer surface.
- **Player-grain rate stats at the mart layer.** Phase 6.3.3 Path A
  intentionally dropped player-grain rate stats from
  `mart_stat_leaderboard` rather than gate them behind sample-size
  thresholds. Team-grain rates stay because natural denominator
  accumulation keeps them meaningful; player rates would need per-stat
  threshold tuning to avoid small-sample noise, with diminishing return.
- **Sheets sink formatting-preservation (`_replace_tab` in-place update).**
  Considered in v1.x as a fix for the "weekly run wipes formatting"
  complaint, but dropped at v1.0.1 -- the v1.1.0 almanac surface
  superseded the old 3-tab records-sheet layout. Any future work should
  target the almanac writer, not the legacy records-sheet sink.
- **`output/_setup.py` boilerplate factoring.** v1.x Handoff item;
  most of what was named (UTF-8 stdout reconfig, `load_dotenv`,
  Snowflake config) already shipped in Phase 7 via `output/db.py::
  init()`. Remainder (the single-line schedule_lookup load per
  consumer) wasn't worth the additional indirection.

---

This roadmap is a snapshot, not a contract. "Now" reflects the most
realistic next steps; items get less concrete the further out you go.
