# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each entry links to the corresponding `Phase X.Y Documentation.md` in the
repository root for the architectural detail behind the change.

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
