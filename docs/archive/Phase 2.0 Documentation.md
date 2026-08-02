# Phase 2.0 Handoff — ESPN Fantasy Baseball Front Page Generator

## What Changed Since Phase 1

Phase 2.0 adds player-level contribution callouts to the weekly summary, a records section (current season + all-time), and several foundational improvements that weren't in the Phase 1 spec but were necessary to support multi-season data correctly.

### Summary Output (Current)
```
[u][b]Matchup #3 Recap[/b][/u]
[b]Best Overall[/b]: 317.3 pts by Intentional Walk to the Bar
Shohei Ohtani: 37.7, JJ Wetherholt: 31.0, Riley O'Brien: 26.9, ...
[b]Best Hitting[/b]: 173.3 pts by No Power, No Panic
Mike Trout: 46.4, Nolan Arenado: 22.4, Pete Alonso: 20.4
[b]Best Pitching[/b]: 154.1 pts by Intentional Walk to the Bar
Riley O'Brien: 26.9, Parker Messick: 26.1, Shota Imanaga: 24.3
[b]Worst Overall[/b]: ...
[b]Worst Hitting[/b]: ...
[b]Worst Pitching[/b]: ...
[b]Tough Luck[/b]: (conditional — fires if #2 scorer lost)
[b]Lucky Bastard[/b]: (conditional — fires if #2 lowest scorer won)
[b]A FAIR AND JUST LEAGUE![/b]: (conditional — fires if all top-half scorers won)

[u][b]Current Season Records[/b][/u]
Best/Worst Matchup Total, Hitting, Pitching (current season only)

[u][b]All-Time League Records[/b][/u]
Best/Worst Matchup Total, Hitting, Pitching (across all loaded seasons)

*Footnotes on abnormal week exclusions and scoring setting changes*
```

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
│   └── generate_summary.py       # Reads marts, prints BBCode summary
└── dbt_league/
    ├── dbt_project.yml           # Includes explicit seed column types
    ├── packages.yml              # dbt_utils dependency
    ├── seeds/
    │   ├── matchup_schedule.csv      # 2025 + 2026 seasons, is_abnormal/is_playoff/playoff_round
    │   ├── stat_classification.csv   # 73 stats → hitting/pitching/fielding/unknown
    │   └── player_nicknames.csv      # ESPN player_id → display nickname (optional overrides)
    ├── models/
    │   ├── staging/
    │   │   ├── sources.yml           # Declares RAW.BOX_SCORES as a dbt source
    │   │   ├── schema.yml            # Tests: not_null on key columns, compound uniqueness
    │   │   └── stg_box_scores.sql    # Flatten JSON → one row per player per team per scoring period
    │   ├── intermediate/
    │   │   ├── schema.yml            # Model + column descriptions
    │   │   ├── int_team_daily_scores.sql   # Team-level daily totals with hitting/pitching splits
    │   │   ├── int_player_daily_scores.sql # Player-level daily scores, active slots only
    │   │   └── int_weekly_matchups.sql     # Matchup pairings with opponent scores and W/L
    │   └── marts/
    │       ├── fct_weekly_team_scores.sql  # Weekly team totals
    │       └── fct_weekly_player_scores.sql # Weekly player totals, one row per player per team per matchup
    └── profiles.yml → lives at C:\Users\kyled\.dbt\profiles.yml (not in repo)
```

---

## What Was Built in Phase 2.0

### New dbt Models

**`int_player_daily_scores`** — One row per player per team per scoring period, active lineup slots only (bench and IL excluded). Classifies each player-day as hitting or pitching based on `lineup_slot`, not `position`. This distinction matters for two-way players like Ohtani — see "Ohtani Fix" below.

**`fct_weekly_player_scores`** — Rolls daily player scores up to weekly grain. One row per player per team per matchup period. Used by the output script for top contributor callouts (top 5 overall scorers from best team, top 3 hitters from best hitting team, top 3 pitchers from best pitching team).

### New Seeds

**`matchup_schedule.csv`** — Replaces the Phase 1 `config/matchup_schedule.json`. Now a first-class dbt source with lineage, tests, and documentation. Columns: `season_year`, `matchup_period`, `start_date`, `end_date`, `is_abnormal`, `abnormal_reason`, `is_playoff`, `playoff_round`. Both 2025 (26 matchup periods including 3 playoff rounds) and 2026 (22 regular season periods, playoffs TBD) are loaded.

**Why a seed instead of JSON:** Seeds are part of the dbt DAG. They get version-controlled, they have explicit column types in `dbt_project.yml`, they can be tested and documented like any other model, and they can be joined to in SQL without writing custom Python loaders. The JSON file worked for Phase 1 but was a second-class citizen — invisible to dbt, referenced only by the extraction script.

**`player_nicknames.csv`** — Maps ESPN `player_id` to a display nickname. The output script joins to this via `COALESCE(nickname, player_name)` so players without nicknames render normally. ESPN player IDs are proprietary and not portable to Yahoo or other platforms — documented in `dbt_project.yml` metadata.

### New Output Features

**Records section** — Current season bests/worsts and all-time bests/worsts for total, hitting, and pitching scores. Excludes abnormal weeks (All-Star break, extended opening weeks) via join to `matchup_schedule` seed. "Current season" is determined dynamically as `MAX(season_year)` from the mart, not hardcoded.

**Player contribution callouts** — Top 5 overall scorers from the best overall team, top 3 hitters from the best hitting team, top 3 pitchers from the best pitching team. Inline with the team superlatives (not a separate section).

### Foundational Improvements

**`season_year` added to grain** — All models from staging through marts now include `season_year`. The compound uniqueness test on `stg_box_scores` is `season_year + player_id + scoring_period + team_id`. Without this, the 2025 backfill would have collided with 2026 data on overlapping matchup period numbers.

**dbt tests** — `schema.yml` in staging with `not_null` tests on key columns and a `dbt_utils.unique_combination_of_columns` test on the compound primary key. Caught 1,957 duplicate rows caused by the missing `season_year` column before any downstream models were affected.

**dbt docs** — Model and column descriptions added to all intermediate models and staging. `dbt docs generate` produces a static site with the full DAG lineage. DAG screenshot taken for portfolio use.

**`--year` CLI argument on extraction script** — Defaults to `date.today().year`. Enables `py extract/extract_box_scores.py --year 2025 1 2 3 ...` for historical backfills. The extraction script now reads the matchup schedule from the dbt seed CSV instead of the JSON config.

**2025 full season backfill** — All 26 matchup periods (regular season + 3 playoff rounds) extracted and loaded into Snowflake.

**`check_fair_and_just` fix** — Now uses active matchup count (`len(matchups) // 2`) instead of `len(ranked) // 2`. Correctly handles leagues with bye weeks (odd team count).

**`season_year` scoping in `generate_summary.py`** — `active_season` computed once in `__main__` and passed explicitly to all query functions. Prevents cross-season data leakage (e.g., querying matchup period 26 from 2025 instead of period 3 from 2026).

---

## Key Technical Decisions

### 1. `player_type` classified by `lineup_slot`, not `position`
ESPN's `position` field reflects a player's MLB position eligibility, not their fantasy deployment on a given day. For two-way players like Ohtani, `position` is always `DH` regardless of whether the manager slotted him as SP that day. `lineup_slot` reflects the actual fantasy slot occupied, so `lineup_slot IN ('SP', 'RP')` correctly identifies pitching contributions.

**Discovered via data inspection:** Ohtani showed `position = DH` and `lineup_slot = SP` on 5 days in 2025 and 3 days in 2026. Without this fix, all his points would have been bucketed as hitting.

### 2. `lineup_slot` kept in intermediate, not in the weekly mart
The weekly player mart (`fct_weekly_player_scores`) aggregates to one row per player per team per matchup period. Including `lineup_slot` in the GROUP BY would split players who changed slots mid-week into multiple rows, pushing aggregation complexity downstream. Slot-level analysis (bench productivity, production by position slot) should come from a dedicated mart (`fct_weekly_player_slots`, planned for Phase 4) or by querying `int_player_daily_scores` directly.

### 3. Seed column types explicitly declared
dbt seeds infer column types from CSV data, which has no type system. A column that's all empty strings might be inferred as VARCHAR one run and BOOLEAN the next if data changes. Explicit `+column_types` in `dbt_project.yml` makes the contract clear and prevents silent type coercion.

### 4. `active_season` passed explicitly, not computed per-function
The output script fetches `MAX(season_year)` once in `__main__` and passes it as an argument to every query function. The alternative — each function running its own `SELECT MAX(season_year)` subquery — works but hides the dependency. A reader of the code can immediately see that all functions anchor to the same season. Explicit over implicit.

---

## Known Architectural Issues (Flagged for 2.1)

### `int_weekly_matchups` depends on `fct_weekly_team_scores` (cross-layer dependency)
The matchup model needs weekly score totals to compute W/L results. It currently reads from the mart (`fct_weekly_team_scores`) instead of rolling up from `int_team_daily_scores` itself. This creates a backward dependency in the DAG — an intermediate depending on a mart — which violates the standard stg → int → mart flow.

**Why it happened:** `fct_weekly_team_scores` already had the rollup, so the matchup model reused it rather than recomputing. Pragmatic at the time, but architecturally wrong.

**2.1 fix:** Consolidate `fct_weekly_team_scores` and `int_weekly_matchups` into a single mart. Both serve the same grain (one row per team per matchup period), the same consumers (the output script), and the same update cadence. The opponent/W-L columns belong on the team scores table — there's no principled reason to separate them. The matchup model should do its own rollup from `int_team_daily_scores`, eliminating the cross-layer dependency entirely.

### Two marts serving the same grain and consumer
`fct_weekly_team_scores` and `int_weekly_matchups` answer slightly different questions (team scores vs. matchup context) but at the same grain and for the same downstream script. This happened incrementally — Phase 1 built the scores mart, Phase 2 added matchup context as a separate model. The 2.1 consolidation will merge them into a single `fct_weekly_team_matchups` (or similar) with opponent columns added.

---

## What's in Snowflake (Current)

- **Database**: `ESPN_FANTASY`
- **Raw schema**: `RAW` — `BOX_SCORES` (includes `season_year` column, added via ALTER TABLE)
- **Analytics schema**: `ANALYTICS` — dbt-built models:
  - `STG_BOX_SCORES` (view)
  - `INT_TEAM_DAILY_SCORES` (view)
  - `INT_PLAYER_DAILY_SCORES` (view)
  - `INT_WEEKLY_MATCHUPS` (view)
  - `FCT_WEEKLY_TEAM_SCORES` (table)
  - `FCT_WEEKLY_PLAYER_SCORES` (table)
  - `MATCHUP_SCHEDULE` (seed table)
  - `STAT_CLASSIFICATION` (seed table)
  - `PLAYER_NICKNAMES` (seed table)

### Data Loaded
- **2025**: All 26 matchup periods (regular season + 3 playoff rounds)
- **2026**: Matchup periods 1–3 (through April 19, 2026)

---

## Phase 2.1 Plan

### Structural Refactors
1. **Consolidate `fct_weekly_team_scores` + `int_weekly_matchups` into a single mart** — add opponent columns to the team scores table, eliminate the cross-layer dependency by rolling up from `int_team_daily_scores` directly. `generate_summary.py` drops the separate `get_matchups` call.
2. **Break `generate_summary.py` into two top-level functions** — weekly recap vs. records tracker. Different responsibilities, different query patterns.

### Feature Additions
3. **Owner names alongside team names** — requires extraction script change to capture `team.owners` from the ESPN API, then thread through staging/marts. Output format: "Team Name (Owner Name)".
4. **`.txt` log file output** — generate_summary writes to a timestamped `.txt` file in addition to printing to console.
5. **"Record set this week" callout** — compare current matchup's scores against existing records; if a new best/worst is set, call it out in the recap section.

### Seeds
6. Add nicknames to `player_nicknames.csv` as encountered.
7. Append 2026 playoff rows to `matchup_schedule.csv` once ESPN sets them.

---

## Git History (Commits through Phase 2.0)
1. Initial commit: add .gitignore
2. Add project scaffold: gitignore, requirements, env template
3. Add extraction script and matchup schedule config
4. Fix extraction: pass both matchup_period and scoring_period for historical player stats
5. Complete Phase 1: dbt pipeline, stat classification seed, weekly summary output
6. Add Phase 1 handoff document
7. Idempotent extraction with auto-detection of recent matchup periods
8. Phase 1 cleanup: matchup schedule seed, season_year grain, dbt tests
9. Phase 2.0 complete: player contributions, records, nickname seed, dbt docs, Ohtani lineup_slot fix
