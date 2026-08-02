# Phase 3.0 Handoff — ESPN Fantasy Baseball Front Page Generator

## What Changed Since Phase 2.1

Phase 3.0 introduces stat-level league records via an incremental dbt pattern, adds wide-format performance marts that combine counting stats with derived rates, and establishes a reusable rate-formula macro library. Two latent bugs from Phase 2.x are fixed as part of the build.

### New Capabilities

- League records at individual-stat level (most HRs, most Ks, most SBs, etc.) with top-10 leaderboards scoped to all-time and current season
- Consumer-facing wide marts combining counting stats and derived rates at both player and team grain
- Rate stats (AVG, OBP, SLG, OPS, ERA, WHIP, K/9, K/BB) defined once via grain-agnostic macros, applied at any grain the analyst needs

### Summary Output (Current)

No changes to the output script in 3.0 — all new capabilities are at the dbt layer. Output integration is deferred to Phase 3.1 ("record-set-this-week" callouts in the weekly recap).

---

## Project Structure (Current)

```
espn-league-manager/
├── .env                          # Secrets (gitignored)
├── .env.example                  # Template showing required env vars
├── .gitignore
├── requirements.txt              # Python 3.13, pinned dependencies
├── extract/
│   └── extract_box_scores.py     # ESPN API → Snowflake raw JSON
├── output/
│   ├── generate_summary.py       # Reads marts, prints BBCode summary
│   └── logs/                     # Timestamped .txt output files (gitignored)
└── dbt_league/
    ├── dbt_project.yml           # Explicit seed column types (incl. is_counting)
    ├── packages.yml              # dbt_utils dependency
    ├── macros/
    │   └── rate_stats.sql            # NEW: grain-agnostic rate-stat macros
    ├── seeds/
    │   ├── matchup_schedule.csv      # 2025 + 2026 seasons, is_abnormal/is_playoff
    │   ├── stat_classification.csv   # EXTENDED: is_counting column added; P_G_W/L and unknown IDs added
    │   ├── player_nicknames.csv
    │   └── owner_nicknames.csv       # Scaffolded, not yet wired in
    ├── models/
    │   ├── staging/
    │   │   ├── sources.yml
    │   │   ├── schema.yml
    │   │   ├── stg_box_scores.sql
    │   │   └── stg_player_stat_breakdowns.sql   # NEW
    │   ├── intermediate/
    │   │   ├── schema.yml
    │   │   ├── int_team_daily_scores.sql
    │   │   ├── int_player_daily_scores.sql      # BUG FIXED: missing comma
    │   │   └── int_player_weekly_stats.sql      # NEW
    │   └── marts/
    │       ├── schema.yml
    │       ├── fct_weekly_team_scores.sql
    │       ├── fct_weekly_player_scores.sql
    │       ├── fct_weekly_player_stats.sql          # NEW: incremental
    │       ├── fct_weekly_team_stats.sql            # NEW: incremental
    │       ├── mart_stat_leaderboard.sql            # NEW: view
    │       ├── mart_weekly_player_performance.sql   # NEW: view (Option D)
    │       └── mart_weekly_team_performance.sql     # NEW: view (Option D)
    └── profiles.yml → lives at C:\Users\kyled\.dbt\profiles.yml (not in repo)
```

---

## What Was Built in Phase 3.0

### New dbt Models

**`stg_player_stat_breakdowns`** — One row per (season, scoring_period, team, player, stat_name). Flattens the `breakdown` VARIANT from `stg_box_scores` into relational shape via `LATERAL FLATTEN`. No business filters applied — staging stays a pure reshape so Phase 4's wasted-points work can consume the same staging with a different filter.

**`int_player_weekly_stats`** — Player-weekly stat totals with business filters applied: active lineup slots only (bench/IL excluded) and counting stats only (rates excluded via `stat_classification.is_counting` join). Joins to the seed to carry `stat_category` through. Grain: one row per (season, matchup, team, player, stat_name).

**`fct_weekly_player_stats`** — Incremental player-weekly stat fact. Foundation for team rollups and future player-level analysis. `unique_key: [season_year, matchup_period, team_id, player_id, stat_name]`, `on_schema_change: 'fail'`. Incremental filter uses `(season_year * 100 + matchup_period) >= max in {{ this }}` — a composite scalar that preserves (season, matchup) ordering and re-processes the latest loaded period to handle in-progress weeks being re-extracted.

**`fct_weekly_team_stats`** — Incremental team-weekly stat fact. Rolled up from `fct_weekly_player_stats` using the same incremental pattern (mart-to-mart rollup). Team totals are defined by construction as `SUM(players)`, guaranteeing consistency with the player fact.

**`mart_stat_leaderboard`** — Top-10 leaderboard per stat per scope (`all_time`, `current_season`). View materialization — rankings are retroactively mutable so incremental would be fragile. Excludes abnormal matchup periods (opening week, All-Star break) via `matchup_schedule.is_abnormal = false`. Playoffs are included. Ties broken by recency (newer season, then newer matchup_period) so the long tail of repeated values surfaces the most recent occurrences first.

**`mart_weekly_player_performance`** (Option D) — Wide-format consumer mart at player-weekly grain. Pivots `fct_weekly_player_stats` into one column per counting stat, then applies rate macros for derived rates (AVG, OBP, SLG, OPS, ERA, WHIP, K/9, K/BB). Joins `player_nicknames` for `display_name`. View materialization — always fresh, derived from the long fact.

**`mart_weekly_team_performance`** (Option D) — Wide-format consumer mart at team-weekly grain. Same structure as the player mart but sources from `fct_weekly_team_stats`. Uses the same rate macros, applied to team-level counting sums. Consistency-by-construction with player mart verified — team rates computed here equal rates derived from re-aggregating player-level counting columns (proven with a diff=0 data check across all 14 teams).

### New Macros

**`macros/rate_stats.sql`** — Eight grain-agnostic rate-stat macros: `batting_avg`, `on_base_pct`, `slugging_pct`, `ops`, `era`, `whip`, `k_per_9`, `k_per_bb`. Each takes column-name parameters with sensible defaults. All apply `NULLIF(denom, 0)` to return NULL rather than divide by zero. The `* 1.0 / ...` pattern forces float division in Snowflake.

`ops` composes `on_base_pct` and `slugging_pct` so there's still only one definition of each underlying formula — change OBP once, every caller updates automatically.

### Seed Changes

**`stat_classification.csv`** — Extended with an `is_counting` boolean column. Counting stats (HR, RBI, AB, H, etc.) are `true`; rate stats (AVG, ERA, OPS, etc.) are `false`. Added `P_G_W` and `P_G_L` (Pitcher Games Won/Lost — analogous to the batter versions already present) as `pitching, is_counting=true`. Added ESPN internal numeric stat IDs (`22`, `30`, `61`, `64`, `78`, `79`, `80`) as `unknown, is_counting=false` — these were flagged in the Phase 1 doc but had been dropped from the seed. Classifying them stops the `relationships` test from failing on 174,640 unclassified rows; the `is_counting=false` flag keeps them out of the facts.

### Bug Fixes from Earlier Phases

1. **`int_player_daily_scores.sql`** — Missing comma between `owner_name` and `team_id` caused SQL to parse as `owner_name AS team_id`, silently aliasing owner-name strings into the `team_id` column of the downstream fact. `fct_weekly_player_scores` still worked because the output script joins on `team_name`, but `team_id` was a latent data-quality bug. Fixed.

2. **`dbt_project.yml`** — Seed `stat_classification` declared `category: varchar` but the CSV column is actually `stat_category`. The declaration was unreachable. Fixed to match the CSV (`stat_category`) and added `stat_description` for completeness. Re-running `dbt seed --full-refresh` applies the explicit types.

---

## Key Technical Decisions

### 1. Incremental pattern as the portfolio centerpiece

Both new fact tables (`fct_weekly_player_stats`, `fct_weekly_team_stats`) use dbt's `incremental` materialization with `unique_key` merge logic and `on_schema_change: 'fail'`. This is the pattern that shows up constantly in production analytics work — new matchup periods arrive weekly, and rebuilding history from scratch every run is wasteful at scale.

The incremental filter uses a composite scalar: `(season_year * 100 + matchup_period) >= max in {{ this }}`. This preserves (season, matchup) ordering and re-processes the latest loaded period on every run — matching the real workflow, where in-progress weeks get re-extracted with new scoring periods as they arrive. The `unique_key` handles the merge: existing rows for that period are replaced, new ones appended. For historical corrections, `dbt run --full-refresh` is documented in model comments.

Enterprise framing: facts are incremental as a rule. Even though the team rollup could full-refresh cheaply at this scale, consistent pattern-application across all fact tables ("facts merge by unique_key, dims rebuild fresh") is lower cognitive overhead than per-model optimization. Same reason BigCorp data teams run `dbt build` per PR and need per-partition resilience.

### 2. Option D (wide performance marts) over Option A (separate counting and rates marts)

Considered: two marts at each grain — one counting (long), one rates-only (wide). Chose instead: one wide consumer mart per grain that combines pivoted counting columns with derived rates from macros.

Reasons:
- **Baseball domain couples counting and rates.** Triple Crown is two counts and a rate. Asking "what's my team's HR, RBI, AVG" is one thought, not three. Forcing it into two queries adds friction for no semantic benefit.
- **Kimball dimensional modeling favors wide consumer surfaces.** Long-format facts (`fct_weekly_*_stats`) live in the observation layer for flexibility and testability; wide marts surface for consumption.
- **BI tool compatibility.** Looker, Tableau, PowerBI all work more naturally against a single wide table than against joins.
- **Semantic layer readiness.** When MetricFlow arrives in 3.2, a wide performance mart is the natural `semantic_model: model:` anchor with measures and dimensions defined on its columns.

The long-format facts stay — they still power the leaderboard's `stat_name` filtering and give us schema flexibility (new stat = new row, not new column). The wide marts sit above them as the consumer-facing layer.

### 3. Tie-breaking by recency in the leaderboard

Initial ROW_NUMBER ordering was `PARTITION BY stat_name ORDER BY stat_value DESC` — ties broken arbitrarily by Snowflake's physical row order. Observed: with a long tail of 16-HR matchups, random ordering surfaces old results ahead of recent ones, which is the wrong UX for a league records page.

Fix: `ORDER BY stat_value DESC, season_year DESC, matchup_period DESC`. Tied values now surface the most recent occurrence first.

### 4. Macros over formulas-as-data (for now)

Considered: define rate formulas in a seed (`numerator_column`, `denominator_column`), then compile via Jinja templating. This is the "metrics as data" pattern used by dbt Semantic Layer / MetricFlow / Looker.

Chose instead: dbt macros as grain-agnostic formulas. Reasons:
- Simple rates (AVG, SLG) fit seed-driven templating, but complex rates (OBP has 4 terms in numerator, 4 in denominator; OPS is a composite of two rates) don't fit neatly into `numerator|denominator` columns. Delimited lists or formula strings in CSVs get fragile.
- Macros live in SQL files — testable, readable, version-controlled like any code.
- 8–10 rate stats at current scope don't justify the overhead of a formula engine.

When/if metric count justifies a real semantic layer, MetricFlow is the escape hatch (Phase 3.2 candidate). The macros aren't wasted work — they remain a SQL-native query path alongside any future semantic layer.

### 5. Leaderboard as view, facts as incremental tables

The leaderboard is a pure derivation — a window function over the team fact with an `is_abnormal` filter. Rankings are retroactively mutable (a week-25 result reshuffles ranks set in week 3). Incremental merge semantics assume rows don't change once written, which a records table fundamentally violates. View = always fresh, zero storage, no fragile rebuild logic. Good portfolio talking point: "recognized this as a case where incremental is the wrong tool."

### 6. Player-grain fact built first, team rolls up from it

`fct_weekly_team_stats` reads from `fct_weekly_player_stats`, not from the intermediate. This is a mart-to-mart rollup, which is an unconventional dbt pattern but earns its place here: team totals are defined by construction as `SUM(players)`, eliminating any chance of team-vs-player inconsistency. An analyst asking "why doesn't team HR match player HR sum?" gets a definitional answer: team is literally the player sum.

### 7. Abnormal weeks excluded from records, playoffs included

Leaderboard filters `matchup_schedule.is_abnormal = false`, which excludes opening week (12–13 days) and the All-Star break (14 days). Those weeks inflate counting stats because of the extra days. Playoffs remain included — they're 7-day matchup periods like any other week, and playoff records are legitimate league history.

---

## What's in Snowflake (Current)

- **Database**: `ESPN_FANTASY`
- **Raw schema**: `RAW` — `BOX_SCORES` (includes `season_year` and owner fields in JSON)
- **Analytics schema**: `ANALYTICS`:
  - `STG_BOX_SCORES` (view)
  - `STG_PLAYER_STAT_BREAKDOWNS` (view) — NEW
  - `INT_TEAM_DAILY_SCORES` (view)
  - `INT_PLAYER_DAILY_SCORES` (view)
  - `INT_PLAYER_WEEKLY_STATS` (view) — NEW
  - `FCT_WEEKLY_TEAM_SCORES` (table)
  - `FCT_WEEKLY_PLAYER_SCORES` (table)
  - `FCT_WEEKLY_PLAYER_STATS` (incremental table) — NEW
  - `FCT_WEEKLY_TEAM_STATS` (incremental table) — NEW
  - `MART_STAT_LEADERBOARD` (view) — NEW
  - `MART_WEEKLY_PLAYER_PERFORMANCE` (view) — NEW
  - `MART_WEEKLY_TEAM_PERFORMANCE` (view) — NEW
  - `MATCHUP_SCHEDULE` (seed table)
  - `STAT_CLASSIFICATION` (seed table) — extended with `is_counting`
  - `PLAYER_NICKNAMES` (seed table)
  - `OWNER_NICKNAMES` (seed table) — scaffolded, not yet wired in

### Data Loaded
- **2025**: All 26 matchup periods (regular season + 3 playoff rounds)
- **2026**: Matchup periods 1–3 (through April 19, 2026)

### Test Coverage
47 data tests across staging, intermediate, and marts. `dbt test` runs in ~16 seconds. New tests added in 3.0:
- `unique_combination_of_columns` on both new incremental facts and both new performance marts
- `not_null` on grain columns for all new models
- `accepted_values` on `stat_category` (hitting/pitching/fielding) and `record_scope` (all_time/current_season)
- `relationships` on `stat_name` columns back to `stat_classification` — catches any ESPN stat missing from the seed

---

## Bookmarks for Future Work

### Phase 3.1 — Record-Set-This-Week Callouts (next)

Thread the new leaderboard into the weekly recap. Logic: for each stat where the current matchup's team observation matches the current rank=1 holder in `mart_stat_leaderboard`, call it out in the output. The rank=2 row provides the previous-record context ("broke OtherGuy's M of Stat X from Matchup N, YYYY"). The leaderboard view already stores top-10, so rank=2 is always available.

Candidate output additions:
- "Record Set" section between superlatives and records
- "Near Record" section for values within 5% of the current rank=1
- Conditional firing — only show section if at least one record was broken or approached

### Phase 3.2 — MetricFlow Semantic Layer

Demonstrate dbt's current-state metrics pattern. Define semantic models on `fct_weekly_team_stats` / `fct_weekly_player_stats`, define ratio metrics for AVG/OBP/SLG/ERA/etc. Consumer queries via `dbt sl query` or the MetricFlow Python client.

Portfolio value: this is a high-signal topic in analytics-engineering interviews right now. The existing macros can remain as a SQL-native fallback path; MetricFlow sits above them.

### Medium-term

- **Rate-stat leaderboards with PA/AB minimums** — Real baseball records always have sample-size thresholds (MLB batting title requires 3.1 PA × games). Current performance marts correctly compute OPS = 3.000 for a 1-for-1 week, which is mathematically right but not meaningful. A future rate-stat leaderboard needs `WHERE ab >= N` (or similar) to filter small-sample artifacts.
- **Wire `owner_nicknames` seed into models** — Same COALESCE pattern as `player_nicknames`. Requires extracting owner_id alongside owner_name in the extraction script.
- **`fct_team_career_stats` mart** — Roll up from `fct_weekly_team_scores` and `fct_weekly_team_stats` to team-level career totals. All-time wins/losses, GOTW counts, average weekly score, best/worst single-week performances.
- **Split `generate_summary.py`** — Weekly recap vs. records tracker. Different responsibilities, different query patterns. Deferred repeatedly; likely becomes relevant when 3.1 adds the record callouts.

### Long-term (unchanged from 2.1)

- **`fct_weekly_player_slots` mart** — One row per player per slot per team per matchup period. Enables bench productivity analysis and Phase 4 wasted points.
- **Phase 4: Wasted points** — Top scorers while benched or unrostered. Requires testing `league.free_agents()` API and potentially per-period free agent extraction.
- **Historical scoring normalization** — Apply current scoring weights to historical raw stat breakdowns from the `breakdown` column.

### Scale/productization bookmarks

The architecture was consciously designed to tolerate a `league_id` dimension being added. Every fact's unique_key accepts extension with `league_id` without structural change. `season_year * 100 + matchup_period` extends to `league_id * 10000000 + season_year * 100 + matchup_period` if multi-tenant partitioning is ever added. Rate macros already grain-agnostic.

---

## Git History (Commits through Phase 3.0)

1. Initial commit: add .gitignore
2. Add project scaffold: gitignore, requirements, env template
3. Add extraction script and matchup schedule config
4. Fix extraction: pass both matchup_period and scoring_period for historical player stats
5. Complete Phase 1: dbt pipeline, stat classification seed, weekly summary output
6. Add Phase 1 handoff document
7. Idempotent extraction with auto-detection of recent matchup periods
8. Phase 1 cleanup: matchup schedule seed, season_year grain, dbt tests
9. Phase 2.0 complete: player contributions, records, nickname seed, dbt docs, Ohtani lineup_slot fix
10. Add Phase 2.0 documentation, rename Phase 1 doc
11. Phase 2.1: consolidate team scores mart, owner names, player type refactor, txt logging, owner nicknames seed
12. Phase 3.0: incremental stat facts + leaderboard + wide performance marts + rate macros (pending merge from worktree)
