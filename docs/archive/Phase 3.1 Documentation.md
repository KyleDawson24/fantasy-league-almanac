# Phase 3.1 Handoff — ESPN Fantasy Baseball Front Page Generator

## What Changed Since Phase 3.0

Phase 3.1 is a re-architecture phase. Phase 3.0 shipped stat-level records via incremental long-format facts plus separate wide performance marts. The shape was correct but the layering was wrong: long facts at the consumer surface forced consumers to either pivot themselves or hop through a parallel mart, and the wide performance marts duplicated counting data that already lived in the long facts.

Phase 3.1 collapses that into a single wide convergence fact per grain. Counting stats, derived rate stats, and platform fantasy points all live together at the player-weekly and team-weekly grains. The leaderboard is extended to cover both player- and team-level rankings plus score-level columns. Two new output scripts — Top Hitter / Top Pitcher callouts in the weekly recap, and a standalone records report — exercise the new shape.

A future scoring-settings extraction (Phase 3.2) for `calculated_points` was scoped out and consciously deferred. The architecture was designed to slot it in cleanly without disrupting current consumers.

---

## Project Structure (Current)

```
espn-league-manager/
├── .env                          # Secrets (gitignored)
├── .env.example                  # Template showing required env vars
├── .gitignore                    # NEW entry: output/LeagueNote.txt
├── requirements.txt              # Python 3.13, pinned dependencies
├── extract/
│   └── extract_box_scores.py     # ESPN API → Snowflake raw JSON
├── output/
│   ├── generate_summary.py       # Reads marts, prints BBCode summary
│   ├── generate_records_report.py    # NEW: standalone records report
│   ├── LeagueNote.txt            # NEW: optional commissioner note (gitignored)
│   └── logs/                     # Timestamped .txt output files (gitignored)
└── dbt_league/
    ├── dbt_project.yml
    ├── packages.yml
    ├── macros/
    │   └── rate_stats.sql                # 8 grain-agnostic rate macros (unchanged from 3.0)
    ├── seeds/
    │   ├── matchup_schedule.csv
    │   ├── stat_classification.csv
    │   ├── player_nicknames.csv
    │   └── owner_nicknames.csv
    ├── models/
    │   ├── staging/
    │   │   ├── sources.yml
    │   │   ├── schema.yml
    │   │   ├── stg_box_scores.sql        # MODIFIED: nickname join moved here
    │   │   └── stg_player_stat_breakdowns.sql
    │   ├── intermediate/
    │   │   ├── schema.yml
    │   │   ├── int_team_daily_scores.sql
    │   │   ├── int_player_daily_scores.sql
    │   │   ├── int_player_daily_stats.sql    # NEW: slot-agnostic, long, is_counting filter
    │   │   └── int_player_weekly_stats.sql   # REPLACED: slot-preserved wide pivot
    │   └── marts/
    │       ├── schema.yml
    │       ├── fct_weekly_player_scores.sql      # unchanged (internal feeder for fct_weekly_player_stats)
    │       ├── fct_weekly_player_stats.sql       # REPLACED: wide convergence, active filter, rates+macros
    │       ├── fct_weekly_team_stats.sql         # REPLACED: wide convergence (absorbs team_scores)
    │       └── mart_stat_leaderboard.sql         # REPLACED: entity_grain, UNPIVOT, score-level rows
    └── profiles.yml → C:\Users\kyled\.dbt\profiles.yml (not in repo)
```

Net dbt model count went from 12 → 10 (consolidation).

---

## What Was Built in Phase 3.1

### dbt Architecture Changes

**Modified `stg_box_scores`** — added `LEFT JOIN player_nicknames` and exposed `display_name = COALESCE(nickname, player_name)` at staging. The nickname join was previously duplicated in two places (`fct_weekly_player_scores` and `mart_weekly_player_performance`). Pulling it earliest in the DAG means every downstream model has access to `display_name` automatically.

**New `int_player_daily_stats`** — slot-agnostic player-daily stat detail. Joins `stat_classification` to bring in `stat_category`, filters to `is_counting=true` (rates dropped here since they can't be summed). Long format. `lineup_slot` preserved through to enable both active-only and inactive-only consumers downstream.

**Replaced `int_player_weekly_stats`** — was long-format active-only counting. Now wide-format slot-preserved counting. Pivots stat_name into ~30 columns (counting stats only), grouped by `(season, matchup, team, player, lineup_slot)`. A player who occupied multiple slots in a matchup_period produces multiple rows here. Rates are NOT computed here because meaningful rate values require the slot filter to be applied first (a rate computed across all-slot sums mixes active production with bench production).

**Replaced `fct_weekly_player_stats`** — was long-format incremental fact. Now wide-format incremental convergence fact. Pipeline:
1. Read slot-preserved counting from `int_player_weekly_stats`
2. Filter to active slots (`lineup_slot NOT IN ('BE', 'IL', 'FA')`)
3. Aggregate counting columns across surviving slots (collapses slot dimension)
4. Compute rate stats via macros from the aggregated counting columns
5. Join `fct_weekly_player_scores` for `total_points` / `hitting_points` / `pitching_points`

Grain: one row per (season, matchup, team, player). The convergence: stats and scores live in the same row.

**Replaced `fct_weekly_team_stats`** — was long-format incremental fact. Now wide-format incremental convergence fact. Absorbs the responsibilities of the deleted `fct_weekly_team_scores`:
- Counting stats and rates (rolled up from `fct_weekly_player_stats`)
- Scoring totals (rolled up from player-level platform points)
- Matchup pairings (extracted from raw box scores, same pattern as old team_scores)
- W/L determination (based on platform `total_points`, the official ESPN arbiter)
- `days_in_period` (joined from `matchup_schedule`)

Single team-weekly contract. No JOIN required for consumers wanting "everything about a team's week."

**Replaced `mart_stat_leaderboard`** — was team-only top-10 by stat. Now extended:
- Player-level rankings ("most HRs by a single player in a week")
- Team-level rankings ("most team HRs in a week")
- Score-level rankings (`total_points`, `hitting_points`, `pitching_points` ranked alongside counting stats)

Implementation uses Snowflake `UNPIVOT` to fold wide stat columns from each convergence fact back into long format `(stat_name, stat_value)`, UNION the team and player streams, then `ROW_NUMBER()` over `(entity_grain, stat_name)`. Top-10 per `(entity_grain, stat_name, record_scope)`. Excludes abnormal weeks. Ties broken by recency.

### Deleted models

- `fct_weekly_team_scores` — absorbed into `fct_weekly_team_stats`
- `mart_weekly_player_performance` — absorbed into `fct_weekly_player_stats`
- `mart_weekly_team_performance` — absorbed into `fct_weekly_team_stats`

### Output script changes (`generate_summary.py`)

- Query targets pointed at the new convergence facts
- Owner names dropped from the weekly recap superlatives (kept in the records section)
- Tail comment block (about scoring-settings caveats) replaced with optional `LeagueNote.txt` content — print verbatim if file exists and non-empty
- New `Top Hitter` and `Top Pitcher` callouts added between the worst block and the conditional callouts:
  - Top Hitter: `pts by Player (Team) -- avg/obp/slg over AB. HR, RBI[, SB if >0]`
  - Top Pitcher: `pts by Player (Team) -- [Wins, ][Saves, ]ERA, WHIP. K : BB over IP`
- Helpers: baseball-style `.350` formatting (no leading zero), inning-fraction notation (`13.1`, `9.2`)

### New: `generate_records_report.py`

Standalone script for league records. Iterates the leaderboard for team-grain all-time records, formats one block per stat:
- Single record holder → team + week + top 3 contributors from `fct_weekly_player_stats`
- Tied record holders (≥2 teams at the same value) → list all tied teams + show second-place tier
- Sparse contributions (fewer than 3 non-zero) or large tie groups → switches to count format (`5 others with 4`, `24 others with 0`)

Independent of `generate_summary.py`. Two scripts, two outputs, one analytical surface.

---

## Key Technical Decisions

### 1. Option D: wide convergence facts at consumer grain

Considered (and rejected) Option A: keep counting facts long, add separate wide rates marts beside them. Both options were architecturally defensible. Option D won because:
- Baseball domain consumption tightly couples counting and rates (Triple Crown is two counts and a rate; ERA/W/K is two counts and a rate). Forcing a JOIN for "give me HR, RBI, AVG together" is friction without benefit.
- Kimball-style dimensional modeling favors wide consumer-facing tables.
- BI tool compatibility — Looker/Tableau/PowerBI work more naturally with wide tables.
- Semantic layer readiness — when MetricFlow lands in 3.2, a wide performance fact is the natural `model:` anchor with measures and dimensions defined on its columns.

The long-format facts (`fct_weekly_player_stats`, `fct_weekly_team_stats` from Phase 3.0) were promoted to wide format. The Phase 3.0 wide *marts* (`mart_weekly_*_performance`) were redundant and removed.

### 2. Slot preserved at intermediate, filter at fact

`int_player_weekly_stats` keeps `lineup_slot` as a grain dimension. The active filter (`NOT IN ('BE', 'IL', 'FA')`) lives at the fact layer, where slot is then aggregated away.

The alternative — filter at intermediate, slot-agnostic from there down — would force two parallel intermediate paths if/when wasted-points work needs the inverse filter. Slot preservation at int gives both active and inactive a single shared upstream.

Rates are NOT computed at intermediate for the same reason: rate values are only meaningful after the slot filter is applied. A "rate over all slots" mixes active production with bench production and isn't a baseball-meaningful number.

### 3. UNPIVOT for the extended leaderboard

The wide convergence facts have stats as columns. The leaderboard ranks across stats. To rank uniformly we need long format, so the leaderboard's CTEs unpivot the wide facts back into `(stat_name, stat_value)` rows, UNION team and player streams, then rank.

UNPIVOT is Snowflake-specific. If the project ever moves to a different warehouse (e.g., DuckDB for a local-CLI build), this would be rewritten as `UNION ALL` per stat — tedious but portable. Documented in the model's header comment.

### 4. Mart-to-mart rollup at the team grain

`fct_weekly_team_stats` reads from `fct_weekly_player_stats`, not from `int_player_weekly_stats`. This is unconventional dbt (mart depends on mart) but earns its place: team totals are *defined* by construction as `SUM(players)`. Defining them otherwise risks drift between team and player numbers. An analyst asking "why doesn't team HR match player HR sum?" gets a definitional answer: team is literally the player sum.

### 5. W/L stays anchored to `platform_points`

When `calculated_points` arrives in 3.2, it's metadata alongside `platform_points`, not a replacement for it. Win/loss outcomes are determined by ESPN's pre-computed score because that's the official arbiter (it accounts for manual adjustments). `calculated_points` exists for transparency and portability, not for re-deciding outcomes.

### 6. Macros stay grain-agnostic

The 8 rate macros (`batting_avg`, `on_base_pct`, `slugging_pct`, `ops`, `era`, `whip`, `k_per_9`, `k_per_bb`) take column-name parameters with sensible defaults. Used at both the player-weekly and team-weekly fact layers — same formula, same definition, applied wherever the underlying counting columns are in scope. When a wasted-points fact eventually wants rates over bench production, same macros apply.

### 7. Top Hitter / Top Pitcher live in the contributions dict

Player-level superlatives are stashed in the `contributions` dict by `get_contribution_callouts` (which already takes the players list as input) rather than passed as separate arguments to `generate_summary`. Keeps the function signature stable and groups all player-level callout data in one place.

---

## What's in Snowflake (Current)

- **Database**: `ESPN_FANTASY`
- **Raw schema**: `RAW` — `BOX_SCORES` (unchanged)
- **Analytics schema**: `ANALYTICS`:
  - `STG_BOX_SCORES` (view) — modified to include `display_name`
  - `STG_PLAYER_STAT_BREAKDOWNS` (view)
  - `INT_TEAM_DAILY_SCORES` (view)
  - `INT_PLAYER_DAILY_SCORES` (view)
  - `INT_PLAYER_DAILY_STATS` (view) — NEW
  - `INT_PLAYER_WEEKLY_STATS` (view) — replaced (now wide, slot-preserved)
  - `FCT_WEEKLY_PLAYER_SCORES` (table) — unchanged
  - `FCT_WEEKLY_PLAYER_STATS` (incremental table) — replaced (wide convergence)
  - `FCT_WEEKLY_TEAM_STATS` (incremental table) — replaced (wide convergence; absorbs team_scores)
  - `MART_STAT_LEADERBOARD` (view) — replaced (player + team + score)
  - Seeds: `MATCHUP_SCHEDULE`, `STAT_CLASSIFICATION`, `PLAYER_NICKNAMES`, `OWNER_NICKNAMES`

Removed: `FCT_WEEKLY_TEAM_SCORES`, `MART_WEEKLY_PLAYER_PERFORMANCE`, `MART_WEEKLY_TEAM_PERFORMANCE`.

### Test Coverage
32 data tests across staging, intermediate, and marts (down from 47 in 3.0 — fewer tests because the wide schema collapses many grain-specific column tests; coverage is unchanged in spirit). Build + test takes ~30 seconds.

### Data Loaded
- **2025**: All 26 matchup periods (regular season + 3 playoff rounds)
- **2026**: Matchup periods 1–3 (through April 19, 2026)

---

## Bookmarks for Future Work

### Phase 3.2 — Scoring Settings + Calculated Points

Architecture and rationale already laid out in the target-state doc. Required pieces:
- New extraction: `extract_scoring_settings.py` (or a sibling pull in the existing extraction script). Run weekly. Source: ESPN raw API `mSettings` view.
- New raw table: `RAW.SCORING_SETTINGS`
- New staging model: `stg_scoring_settings` — reshapes `scoringItems` array into `(season_year, espn_stat_id, points_per_unit)`. Joins `stat_classification` (extended with `espn_stat_id`) to map back to `stat_name`.
- Compute `calculated_points` in `fct_weekly_player_stats` via `SUM(stat_value * points_per_unit)`.
- Carry `platform_points` AND `calculated_points` in both convergence facts. Surface discrepancies — they indicate either manual adjustments (interesting to flag) or weight-mapping bugs (important to catch).

The Phase 3.1 architecture was designed to slot this in cleanly: rate macros are grain-agnostic, the convergence facts already have the counting columns needed, and adding the `stg_scoring_settings` join is a single new edge in the DAG.

### Phase 4 — Wasted Points

Free-agent investigation completed (see `free_agent_findings.md`). `league.free_agents()` returns daily-grain stat breakdowns and pre-computed points for FAs — no scoring-weight computation needed. The architectural plan:
- Extract free agents alongside box scores per scoring period
- Land FA rows in the same `RAW.BOX_SCORES` (or a parallel raw table) with `lineup_slot='FA'` and synthetic/null `team_id`
- All three wasted-points buckets (BE, IL, FA) become a filter on the same staging foundation, not a separate pipeline
- New mart `mart_wasted_points` — reads from `int_player_daily_stats` (slot-agnostic, already in place after 3.1) with the inverse slot filter

### Phase 3.3 (or later) — MetricFlow Semantic Layer

Demonstrate dbt's current-state metrics pattern. Define semantic models on the wide convergence facts, define ratio metrics for AVG/OBP/SLG/ERA/etc. Consumer queries via `dbt sl query` or the MetricFlow Python client. The macros stay as a SQL-native fallback path; MetricFlow sits above them.

### Medium-term

- **Records report performance** — `generate_records_report.py` opens a new Snowflake connection per query (~60 connections per run). Acceptable for a placeholder. Refactor to a single connection with reusable cursor would drop runtime from ~60s to a few seconds.
- **Rate-stat leaderboards with PA/AB minimums** — current leaderboard ranks rates without sample-size thresholds. Real baseball records always have minimum-PA filters (MLB batting title requires 3.1 PA × games). A future rate-stat leaderboard view would `WHERE ab >= N`.
- **`mart_weekly_recap` (deferred)** — `generate_summary.py` does what was originally scoped as a SQL mart. Confirmed that some logic (tough luck conditional firing, fair-and-just rank-checking) lives more naturally in Python than SQL. Keeping the recap orchestration in Python is the right call; revisit if multiple consumers ever need it.
- **Owner nicknames seed** — still scaffolded but not wired in. Same COALESCE pattern as `player_nicknames` once `owner_id` extraction lands.
- **`fct_team_career_stats` mart** — roll up career totals from the new convergence facts.

### Long-term

- **Cross-platform portability** — The wide player-day box score (post-staging) is the universal interface. Yahoo or other providers would need only a different extraction + staging layer producing the same column contract. Everything from intermediate layer onward is platform-agnostic. Stat names are the universal key.
- **Path B (DuckDB local CLI)** — UNPIVOT in `mart_stat_leaderboard` would need rewriting as explicit `UNION ALL` per stat. Otherwise the architecture is warehouse-agnostic.
- **Path C (hosted multi-tenant)** — every fact's `unique_key` accepts `league_id` extension without structural change. The composite incremental scalar (`season_year * 100 + matchup_period`) generalizes to `league_id * 10000000 + season_year * 100 + matchup_period` for multi-tenant partitioning.

---

## Git History (Commits through Phase 3.1)

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
12. Remove stale fct_weekly_matchups model
13. Gitignore .claude/ worktree directory
14. Phase 3.0: incremental stat facts, leaderboard, wide performance marts, rate macros
15. Add Phase 3.0 documentation
16. Backing up before rearchitecture (merge commit)
17. Phase 3.1: wide convergence facts, slot-agnostic intermediate, extended leaderboard, top hitter/pitcher, records report
