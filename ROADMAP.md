# Roadmap

This project shipped v1.0.0 on 2026-05-13, v1.0.1 on 2026-05-18
(polish + flavor expansion), and v1.0.2 on 2026-05-19 (DAG hygiene
+ dbt-architecture cleanup; refactor-only). See CHANGELOG.md for
the per-release entries. The items below are what's still on deck,
organized by priority and ambition. v1.x = incremental polish on
the current architecture; v2.0 = structural change.

## Now (v1.x — incremental polish)

Low-risk changes building on the current architecture. Most would ship in
a single afternoon.

### Data wiring

- **Wire `owner_nicknames` seed into models.** The seed exists but isn't
  joined; output scripts read it ad-hoc. Joining into the staging /
  intermediate layer would let the mart carry owner display names
  directly.
- **Identify playoff-contention teams during playoff weeks.** The
  calendar layer correctly flags playoff weeks (`is_playoff=true` on
  `matchup_schedule` rows), but during those weeks all teams still play
  — some in the actual playoff bracket, some in the consolation
  tournament. The records output today can't distinguish a Finals MVP
  performance from a 13th-place consolation week. Identifying which
  `(team, season, matchup_period)` tuples represent actual playoff
  contention would let records filter or annotate accordingly. Scoped
  as v1.x for now; may slide to v2.0 depending on what playoff-bracket
  data the ESPN API exposes (discovery TBD).
- **Auto-populate `matchup_schedule` from ESPN settings API.** Currently
  `matchup_schedule.csv` is a hand-maintained seed (~25 rows per season:
  date ranges, `is_playoff`, `playoff_round`, `is_abnormal`). Most fields
  are derivable from the espn-api wrapper's `league.settings`:
  - `season_year`, `matchup_period`, `start_date`, `end_date` from
    `settings.matchup_periods` (dict of mp → scoring_period list)
  - `is_playoff`: anything past `settings.regular_season_count`
  - `playoff_round`: derived from `settings.playoff_team_count` plus
    position of mp within the playoff range

  `is_abnormal` (All-Star break, weather-shortened weeks, commissioner-
  declared anomalies) is the only genuinely manual field — no ESPN API
  concept for it.

  Proposed shape: `extract.py` writes a new `raw.matchup_schedule`
  (append-only with `extracted_at`); a new `stg_matchup_schedule` model
  takes the latest snapshot and joins to a tiny
  `matchup_schedule_overrides.csv` seed carrying only `is_abnormal`
  patches (default false; commissioner adds rows when needed). Reduces
  new-user setup friction from "populate 25 rows per season" to
  "occasionally flag a weird week." Estimated effort: 1-2 days.

  Before starting: maintainer has additional automation heuristics in
  mind (particularly around `is_abnormal` detection and the override-
  seed shape). Confirm scope before writing code.

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
  in Next). Framed as a v1.x "proof of progress" — even partial
  scaffolding ships value (consumer simplification, lineage clarity)
  and lays foundation for full player-profile analytics post-v1.0.
- **`fct_team_career_stats` mart.** Career-aggregate equivalent of
  `fct_weekly_team_active_performance` — "who has the most points scored
  for their team in league history," etc. Team-side counterpart to
  `fct_player_career` above. Especially fun for keeper-league framing.
  Data's already there; this is a new aggregation layer.

## Next (v2.0 — substantive features)

Larger changes that would re-shape parts of the project.

> **Scoping note:** Yahoo eligibility and DuckDB target are both v2.0
> candidates, but realistically only one would ship per major version —
> they're roughly equivalent in scope and effort. Final pick TBD.

### Cross-platform portability

- **Yahoo / Sleeper extract paths.** The current `extract/` layer is
  ESPN-specific (cookies + `espn-api` wrapper). A new extract per
  platform would share the dbt + output layers if the raw shape can be
  normalized at the staging boundary.
- **Tracked-stats config externalized.** The Phase 6.3.3 stat list is
  hardcoded into mart UNPIVOT lists. Externalizing the stat-mapping to a
  YAML or seed would let other leagues with different scoring settings
  reuse the project without forking.

### Warehouse / target flexibility

- **dbt-bigquery target.** Snowflake is the only configured target. SQL
  is fairly portable; bigquery should require minimal model changes.
- **DuckDB target with parquet-on-disk extract.** A `dbt-duckdb` target
  plus parquet artifacts in `extract/` would let the project run without
  a cloud warehouse, lowering the bar for new users.

### New data sources

- **Draft position integration.** Linking each player to their league
  draft round (and ESPN ADP) enables analyses like "average points per
  season per draft round" and quick value-vs-ADP checks during the
  season. Requires a new extract path for draft data.

### Metrics framework

- **MetricFlow / dbt Semantic Layer integration.** Would formalize
  user-defined metrics ("wasted points," "calculated points") as
  declared metrics rather than column outputs. Targeted as a deliberate
  v2.0 learning exercise — pending a fit assessment for the project's
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
  (a) full mirror — extend inactive facts to match the active schema and
  document accordingly; (b) intentional asymmetry — keep inactive terse,
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
  a service for other leagues. Deferred indefinitely — different product
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
  complaint, but dropped at v1.0.1 — the upcoming Sheets surface
  redesign supersedes the in-place-update logic. Will rebuild from
  scratch when the new layout lands.
- **`output/_setup.py` boilerplate factoring.** v1.x Handoff item;
  most of what was named (UTF-8 stdout reconfig, `load_dotenv`,
  Snowflake config) already shipped in Phase 7 via `output/db.py::
  init()`. Remainder (the single-line schedule_lookup load per
  consumer) wasn't worth the additional indirection.

---

This roadmap is a snapshot, not a contract. "Now" reflects the most
realistic next steps; items get less concrete the further out you go.
