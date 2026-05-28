# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each entry links to the corresponding `Phase X.Y Documentation.md` in the
repository root for the architectural detail behind the change.

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
  https://kyledawson24.github.io/fantasy-league-front-page/.

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
