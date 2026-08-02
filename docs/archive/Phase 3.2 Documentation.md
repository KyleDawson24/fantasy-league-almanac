# Phase 3.2 Handoff — ESPN Fantasy Baseball Front Page Generator

## What Changed Since Phase 3.1

Phase 3.2 adds league scoring-settings extraction and produces a `calculated_points` column alongside the existing `platform_points` (ESPN's pre-computed value). The two sit side-by-side in the convergence facts: platform stays the official arbiter for W/L; calculated enables cross-season normalization (apply current rules to historical breakdowns) and per-stat point-contribution analysis (`hr_pts`, `k_pts`, etc.).

Two architectural payoffs from this phase:

1. **Cross-season comparability** — under the current league's scoring rules, what would 2025 teams have scored? Calculated_points answers this.
2. **Per-stat point attribution** — for each stat, how many fantasy points did it generate this week? Enables future "top N stats by point contribution" callouts in player recaps.

The most meaningful column rename in 3.2: `total_points` / `hitting_points` / `pitching_points` → `platform_points` / `platform_hitting_pts` / `platform_pitching_pts`. The "platform" prefix removes ambiguity with `calculated_*`. This rippled through `fct_weekly_player_performance`, `fct_weekly_team_performance`, `mart_stat_leaderboard`, `generate_summary.py`, and `generate_records_report.py`.

---

## Project Structure (Current)

```
espn-league-manager/
├── extract/
│   ├── extract.py                    # NEW: consolidated extraction (box scores + scoring settings)
│   ├── extract_box_scores.py         # superseded by extract.py; can be deleted on merge
│   └── dump_stats_map.py             # NEW: one-shot helper to dump espn-api STATS_MAP
├── output/
│   ├── generate_summary.py           # MODIFIED: platform_* column rename
│   ├── generate_records_report.py    # MODIFIED: STAT_ORDER + STAT_DISPLAY platform_* rename
│   ├── LeagueNote.txt                # gitignored
│   └── logs/
└── dbt_league/
    ├── dbt_project.yml               # MODIFIED: stat_classification gains espn_stat_id + notes column types
    ├── macros/
    │   └── rate_stats.sql
    ├── seeds/
    │   ├── stat_classification.csv   # MODIFIED: espn_stat_id + notes columns; PG fix; HBP_P added; stat 64 = SHO
    │   ├── matchup_schedule.csv
    │   ├── player_nicknames.csv
    │   └── owner_nicknames.csv
    └── models/
        ├── staging/
        │   ├── sources.yml           # MODIFIED: scoring_settings raw table declared
        │   ├── stg_box_scores.sql
        │   ├── stg_player_stat_breakdowns.sql
        │   └── stg_scoring_settings.sql       # NEW
        ├── intermediate/
        │   ├── int_team_daily_scores.sql
        │   ├── int_player_daily_scores.sql
        │   ├── int_player_daily_stats.sql     # MODIFIED: HBP disambiguation, scoring weights join, stat_points column
        │   └── int_player_weekly_performance.sql  # RENAMED from int_player_weekly_stats; added *_pts cols + catch-all totals
        └── marts/
            ├── fct_weekly_player_scores.sql
            ├── fct_weekly_player_performance.sql   # MODIFIED: *_pts cols, calculated_*, platform_* rename
            ├── fct_weekly_team_performance.sql     # MODIFIED: same rollup additions, platform_* rename
            └── mart_stat_leaderboard.sql            # MODIFIED: platform_* rename in unpivot lists
```

---

## What Was Built in Phase 3.2

### Extraction layer

**`extract/extract.py`** — consolidated entry point. Defaults to box scores (preserving Phase 1+ workflow). Add `--include-settings` to pull scoring settings alongside, or `--settings-only` for settings without box scores. Settings are append-only into `RAW.SCORING_SETTINGS` (history preserved per snapshot, timestamped via `extracted_at`). Box score logic unchanged from Phase 1+ (delete-then-insert per matchup_period).

**`extract/dump_stats_map.py`** — one-shot helper. Dumps the espn-api package's internal `STATS_MAP` (numeric stat ID → human-readable name) so the seed's `espn_stat_id` column can be backfilled. Run when adding a new league or when ESPN updates their wrapper.

### Seed reconciliation

`stat_classification.csv` extended with:
- `espn_stat_id` (integer): bridges to ESPN's numeric stat IDs
- `notes` (varchar): explanatory context for unusual cases

Resolutions made during 3.2:
- **Stat ID 64 = SHO (Shutouts)**, 5 pts per. Originally labeled `ESPN_UNUSED_64` with `is_counting=false`. Identified after the website QA showed SO=5.0 for Sandy Alcantara's complete-game shutout. Now `is_counting=true`, `stat_category='pitching'`.
- **Stat ID 66 = PG (Perfect Game)**, 250 pts per. Was incorrectly labeled "Pitches Per Game" with `is_counting=false`. Now properly identified as Perfect Game.
- **HBP_P (stat ID 42)** added to seed as `pitching, is_counting=true, points_per_unit=-1`. Note: the espn-api wrapper doesn't disambiguate from batter HBP (id 12) — both translate to "HBP" in the breakdown. Disambiguation handled at intermediate via `lineup_slot` (see below).
- **Unknowns retained as numeric stat_names**: 22, 30, 61, 64, 78, 79, 80, 99 — the espn-api wrapper doesn't translate these. The seed's `stat_name` matches the literal value in the breakdown VARIANT (numeric strings). Descriptive labels live in `stat_description`/`notes`.

### Staging

**`stg_scoring_settings`** — surfaces only the *current season's* scoring weights (max season_year with extracted data), one row per scored stat. Joins `stat_classification` on `espn_stat_id` to bring back human-readable `stat_name` and `stat_category`. Output: `(settings_season, espn_stat_id, stat_name, stat_category, points_per_unit)`.

Historical settings remain in `RAW.SCORING_SETTINGS` (append-only) but are not surfaced. Current-season weights are applied universally — including to historical breakdowns — to enable cross-season normalization. This is YAGNI on per-season weight selection; if a fringe use case ever needs "what would 2024 score under 2024 rules?", the staging extends to accept a season parameter.

`isReverseItem` flag investigated and confirmed unused in this league — penalty stats use direct negative `points` values (e.g., `B_SO=-1`, `GDP=-2`). The flag handling is in place anyway in case a future league uses it.

### Intermediate

**`int_player_daily_stats`** modifications:
- Joins `stg_scoring_settings` to bring in `points_per_unit`
- Computes `stat_points = stat_value * points_per_unit` per row
- **HBP disambiguation**: a `disambiguated` CTE rewrites `stat_name='HBP'` to `'HBP_P'` when `lineup_slot IN ('SP', 'RP', 'P')`. Required because the wrapper conflates ESPN stat IDs 12 and 42 under "HBP" — without disambiguation, pitcher HBPs were credited at +1 instead of penalized at -1. The disambiguation logic lives at int (not staging) per project convention that staging is a pure reshape.

**`int_player_weekly_performance`** (renamed from `int_player_weekly_stats` because the table now carries scoring-derived `*_pts` columns alongside counting stats):
- Wide pivot of counting stats (existing)
- Added per-stat `*_pts` columns (e.g., `hr_pts = hr * points_per_unit_for_hr`)
- **Catch-all totals**: `total_hitting_stat_pts`, `total_pitching_stat_pts`, `total_stat_pts` — sum `stat_points` across ALL scored stats (regardless of whether they have a dedicated `*_pts` column in the wide pivot). The fact layer uses these for `calculated_points` so the value is correct even for stats not enumerated in the wide pivot (GDP, B_IBB, HBP_P, PK, BLSV, NH, PG, SHO).

### Fact layer

**`fct_weekly_player_performance`**:
- All 31 per-stat `*_pts` columns carried through (consumer access for "top N contributing stats" callouts)
- `calculated_hitting_pts` / `calculated_pitching_pts` / `calculated_points` — sourced from the catch-all totals (NOT from summing the wide `*_pts` columns). Robust to stats outside the wide pivot.
- Renamed scoring columns: `total_points` → `platform_points`, `hitting_points` → `platform_hitting_pts`, `pitching_points` → `platform_pitching_pts`. Disambiguation from `calculated_*`.

**`fct_weekly_team_performance`**:
- Rolls up all `*_pts` and `calculated_*` from player fact (consistency-by-construction)
- Same `platform_*` rename
- W/L still determined by `platform_points` (ESPN's official arbiter, includes manual adjustments)

### Output scripts

`generate_summary.py`: column references updated to `platform_*` throughout (SELECT clauses + Python dict accesses). Added `team_id` to player query for future joins. Otherwise unchanged behavior.

`generate_records_report.py`: `STAT_ORDER` and `STAT_DISPLAY` updated to `PLATFORM_POINTS` / `PLATFORM_HITTING_PTS` / `PLATFORM_PITCHING_PTS`.

---

## Key Technical Decisions

### 1. Calculated alongside platform, not replacing

Both columns live in the convergence facts. Platform is the ESPN arbiter (authoritative for W/L outcomes, includes manual scoring adjustments). Calculated is the rules-normalized derivation (current-season weights × counting stats; same formula applied uniformly across seasons). Neither dominates; they answer different questions.

### 2. W/L stays anchored to platform_points

When `calculated_points` differs from `platform_points`, calculated does NOT override. ESPN's pre-computed score is the official record (already used to compute league standings; manual adjustments baked in). Calculated_points is metadata, not a re-determination.

### 3. Catch-all totals at int, not summing wide columns at fact

`calculated_points` uses `total_stat_pts` from `int_player_weekly_performance` (which sums `stat_points` across all rows including stats not in the wide pivot) rather than summing the explicit `*_pts` columns. This is robust — when a new stat enters the league's scoring (or our seed extends), calculated_points stays correct without touching the wide pivot.

### 4. HBP disambiguation at intermediate, not staging

The espn-api wrapper conflates ESPN stat IDs 12 (batter HBP, +1) and 42 (pitcher HBP, -1) under the single name "HBP" in the breakdown VARIANT. Without disambiguation, pitcher HBPs were credited as batter HBPs (a +2 swing per occurrence). Fixed by rewriting `stat_name='HBP'` → `'HBP_P'` when `lineup_slot IN ('SP','RP','P')` at the int layer (where business logic belongs; staging stays a pure reshape).

### 5. Single extract script with flags

Box score extraction is the default behavior (one command, weekly cadence preserved). Scoring settings opt-in via `--include-settings` or `--settings-only`. They change rarely; no need to pull every week.

### 6. Append-only raw scoring settings

`RAW.SCORING_SETTINGS` keeps every snapshot timestamped. Staging picks the latest per season via `ROW_NUMBER()` over `extracted_at`. Future use case: "settings changed mid-season; what changed?" → answer by querying the raw table directly.

### 7. Stg surfaces only current season's weights

The fringe use case "what would 2025 teams have scored under 2024 rules?" doesn't justify the complexity of conditional weight selection at staging. Current-season weights apply universally; the cross-season comparison happens by virtue of applying the same weights everywhere.

### 8. Per-stat `*_pts` columns retained alongside catch-all totals

The wide `*_pts` columns enable consumer-side per-stat ranking ("top 2 stats by point contribution this week"). Catch-all totals power calculated_points. Both coexist because they serve different consumer questions.

### 9. Platform/calculated naming convention

`platform_*` = ESPN-computed (arbiter). `calculated_*` = our derivation (transparency + normalization). Both end with `_pts` for points-derived columns; counting columns have no suffix (`hr`, `rbi`, etc.).

---

## What's in Snowflake (Current)

- **Database**: `ESPN_FANTASY`
- **Raw schema**: `RAW`
  - `BOX_SCORES`
  - `SCORING_SETTINGS` — NEW (append-only)
- **Analytics schema**: `ANALYTICS`
  - All existing models from 3.1
  - `STG_SCORING_SETTINGS` (view) — NEW
  - `INT_PLAYER_DAILY_STATS` (view) — modified
  - `INT_PLAYER_WEEKLY_PERFORMANCE` (view) — renamed + extended
  - `FCT_WEEKLY_PLAYER_PERFORMANCE` (incremental) — extended
  - `FCT_WEEKLY_TEAM_PERFORMANCE` (incremental) — extended

---

## Open Investigations (carry into next phase)

### 1. QA query (370.65) ≠ fact (calculated_points ~377.55) for Hosstros MP1

Both `int_player_daily_stats` and `int_player_weekly_performance` totals match at 370.65 when filtered by `team_name = 'The Hosston Hosstros'`. The fact's calculated_points is ~7 higher.

**Hypothesis**: A player whose `team_id` is Hosstros has `team_name` rendering differently in some daily rows (mid-week trade, attribute drift, owner change, etc.). The QA filter on `team_name` misses those rows; the fact aggregates by `team_id` and includes them.

**Diagnostic to run first**:
```sql
SELECT player_id, player_name, team_name, lineup_slot,
       SUM(stat_value) AS total_value, SUM(stat_points) AS total_pts
FROM ESPN_FANTASY.ANALYTICS.INT_PLAYER_DAILY_STATS
WHERE season_year = 2026 AND matchup_period = 1
  AND team_id = (SELECT team_id FROM ESPN_FANTASY.ANALYTICS.FCT_WEEKLY_TEAM_PERFORMANCE
                 WHERE team_name = 'The Hosston Hosstros' AND season_year = 2026 AND matchup_period = 1)
GROUP BY player_id, player_name, team_name, lineup_slot
ORDER BY player_name, lineup_slot;
```

Look for any row where `team_name != 'The Hosston Hosstros'`. If found, that's the discrepancy.

**If hypothesis confirmed**: the fix isn't necessarily code — `team_name` drift is real data. May want to standardize on `team_id` filtering throughout, or add a "current_team_name" derived dimension.

### 2. Website per-stat counting differs from extracted breakdown

Comparison for Hosstros MP1 batters:
- Website: 367 ABs, 53 1Bs, 50 Rs, 40 RBIs, 117 Ks
- Calculated/extracted: 361 ABs, 52 1Bs, 49 Rs, 39 RBIs, 114 Ks

Website is consistently 1-7 stat counts higher. Most plausible explanation: MLB stat corrections applied after extraction. ESPN's website refreshes from live MLB data; the wrapper's `breakdown` is the snapshot at extraction time.

After re-extraction these should mostly close. Residual differences are unfixable on our side (depend on MLB data lag).

### 3. Stat ID 30 (15 points per) — never observed but scored

Currently flagged `ESPN_UNUSED_30` with `is_counting=false`. Given the SHO discovery (stat 64), worth verifying stat 30 isn't a real scored stat we're missing. Low priority — only 1 observed row across 2025-2026 data.

---

## Bookmarks for Future Work (carried forward)

### Phase 4 — Wasted Points

Free-agent risk fully resolved (Phase 3.1 testing). FA rows can land in same staging with `lineup_slot='FA'`. New mart `mart_wasted_points` reads from `int_player_daily_stats` with inverse slot filter (`IN ('BE', 'IL', 'FA')`). Three buckets, one foundation.

### "Record Set This Week" callout

Originally scoped for Phase 3.1. Build on `mart_stat_leaderboard` — query rank=1 row per stat in current week vs previous-build snapshot.

### MetricFlow Semantic Layer

Wide convergence facts are the natural anchor. `platform_points`, `calculated_points`, rate stats — all map cleanly to MetricFlow ratio metrics.

### Output script split

`generate_summary.py` and `generate_records_report.py` could share a common module for connection management, formatting helpers (`fmt_avg`, `fmt_ip`), and BBCode output. Not urgent.

### Owner nicknames seed wired in

Same `COALESCE(nickname, owner_name)` pattern as `player_nicknames`. Extraction needs `owner_id`; threading through staging is straightforward.

### `fct_team_career_stats` mart

All-time wins/losses, GOTW counts, average weekly score, season-best/worst. Built from `fct_weekly_team_performance` rolling up multiple seasons.

---

## Migration Notes for Next Session

State at end of Phase 3.2:

- Phase 3.2 work is in `phase-3.2` worktree, **uncommitted**. To pick up cleanly:
  1. Commit the worktree changes (handoff doc + all dbt models + extract scripts)
  2. Merge to main
  3. Drop old Snowflake tables: `INT_PLAYER_WEEKLY_STATS` (renamed) — not necessary if `dbt run` handles drop on rename, but worth checking via `SHOW VIEWS LIKE 'INT_PLAYER_WEEKLY%';`
- Memory files updated: `project_phase_plan.md`, `project_conventions.md`
- One open investigation (Hosstros QA gap, see above) — should be a quick diagnostic in the next session

To resume in a fresh conversation:
> "Reading the project memory and Phase 3.2 docs to get oriented. Ready to investigate the Hosstros QA-vs-fact discrepancy noted in the Phase 3.2 doc, then pick a next phase (Phase 4 wasted points or MetricFlow)."

---

## Git History (commits expected through Phase 3.2)

After commit, history will include:
1-17. (Phase 1.0 through 3.1, see prior phase docs)
18. Phase 3.2: scoring settings extraction, calculated_points, HBP disambiguation, catch-all totals, platform_* rename
19. Add Phase 3.2 documentation
