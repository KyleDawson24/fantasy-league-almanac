# ESPN Fantasy Baseball Front-Page Generator — Project Handoff

**Audience:** A developer taking over day-to-day development. The original maintainer (the user) continues to operate this weekly for their own 14-team H2H league, so behavior changes that break the weekly post will surface immediately. Read top-to-bottom once; thereafter, jump to the section you need.

**Status as of handoff:** Phase 6.3.3 shipped to local main at commit `8fa40bf`. Local main is 5 commits ahead of `origin/main` — the user opted not to push yet. Last operational verification: 2026 Week 5 BBCode + Sheets output, both clean.

---

## 1. What this is

An end-to-end ELT pipeline that turns ESPN's fantasy baseball API into:

1. A **BBCode-formatted weekly recap** (`output/generate_summary.py`) that the league commissioner posts to the ESPN league frontpage every Sunday after the matchup_period closes.
2. A **BBCode all-time-records report** (`output/generate_records_report.py`) — same target audience, covers the full league history.
3. A **Google Sheet with three tabs of records** (`output/sheets_writer.py`, opt-in via env var) — All-Time Records, Current Season Records, Leaderboard Dump. 17-column schema with contributor breakdowns.

The user runs it weekly. Output #1 is the canonical league deliverable; #2 fires alongside; #3 is a sink the user enables when they want the sheet refreshed.

This is also a **portfolio piece** — the user is targeting Senior Data Analyst / Analytics Lead roles. Every architectural decision has been documented in `Phase X.Y Documentation.md` files in the repo root for that reason. Don't optimize the project as if it were closed-source production code; the documentation IS part of the deliverable.

**Path A** (the only active path) is Snowflake-as-warehouse. Path B (DuckDB retarget) and Path C (hosted multi-tenant) have been considered and deferred — but cross-platform readiness has shaped some staging-layer decisions (e.g., a thin staging contract that other adapters could plug into).

---

## 2. Architecture in one paragraph

Three stages: **extract** (Python pulls ESPN's API → Snowflake `RAW` schema, append-only), **transform** (dbt builds `staging` views → `intermediate` views → `marts` facts and the leaderboard), **output** (Python reads marts and produces BBCode + Sheets). Each stage is independently runnable. The user's weekly cadence is `extract → dbt build → output/generate_summary.py → output/generate_records_report.py`. Backfills happen when extract logic or seed data changes (`extract --year YYYY --all` then `dbt build --full-refresh`).

**Mental model:**
- Extract is the only place that talks to ESPN. It's brittle (vendor wrapper has bugs we work around); changes here usually mean a re-extract.
- dbt is where the data model lives. Wide convergence facts at consumer grain; rate macros for grain-agnostic formulas; the leaderboard mart is the single source of truth for record queries.
- Output is pure presentation + light orchestration. `output/records.py` is the data-access module (no SQL elsewhere except inside this module's helpers); `output/formatters.py` is the rendering module.

---

## 3. Repo map

```
espn-league-manager/
├── extract/
│   ├── extract.py                       # Sole extract entry point
│   └── dump_stats_map.py                # Diagnostic for ESPN's stat ID map
├── dbt_league/
│   ├── dbt_project.yml                  # Seed column types declared here
│   ├── macros/rate_stats.sql            # Grain-agnostic AVG/OBP/SLG/ERA/WHIP/K9/KBB
│   ├── seeds/
│   │   ├── stat_classification.csv      # Stat ID → name + category + flags
│   │   ├── matchup_schedule.csv         # Per-MP: dates, is_abnormal, is_playoff,
│   │   │                                #   playoff_round (Round 1 / Semi-Finals / Finals)
│   │   ├── owner_nicknames.csv          # 14 owners; only consumed by output (not joined in dbt yet)
│   │   └── player_nicknames.csv         # Tiny; ad-hoc display overrides
│   └── models/
│       ├── staging/                     # 1:1 reshapes of RAW; minimal logic
│       ├── intermediate/                # Joins, slot-validity filter, daily/weekly rollups
│       └── marts/                       # Wide convergence facts + leaderboard
├── output/
│   ├── records.py                       # Data access layer (1000+ lines; see §6)
│   ├── formatters.py                    # STAT_DISPLAY/ABBREV maps + rendering helpers
│   ├── sheets_writer.py                 # Google Sheets sink; opt-in
│   ├── league_notes.py                  # Registry-pattern color callouts (user-extensible)
│   ├── generate_summary.py              # Weekly recap BBCode
│   ├── generate_records_report.py       # All-time records BBCode + Sheets opt-in sink
│   ├── LeagueNote.txt                   # gitignored; ad-hoc commissioner notes
│   ├── .sheets_oauth_token.json         # gitignored; cached OAuth token
│   └── logs/                            # gitignored; timestamped output snapshots
├── archive/                             # Tracked via .gitignore exception
│   ├── chunk{3,4,5}_smoke.py            # Phase 6.3.3 smoke tests; useful for re-verification
│   ├── diag_sheet_filter.py             # "Why isn't stat X in the sheet?" diagnostic
│   ├── phase_6.3.3_chunk{1,2}_build.log # dbt build artifacts
│   └── *.json                           # Older debug payloads
├── Phase 1.0 .. Phase 6.3.3 Documentation.md   # One per phase; source of truth for
│                                                 #   architectural decisions
├── HANDOFF.md                           # This file
├── .env                                 # gitignored; credentials
├── .env.example                         # Template
├── requirements.txt                     # Pinned-ish; venv-installable
└── .venv/                               # Local venv (gitignored)
```

---

## 4. Setup

### Required credentials
- **Snowflake**: account, user, password, database (`ESPN_FANTASY`), warehouse. Stored in `.env`.
- **ESPN cookies**: `ESPN_S2` and `SWID` for league access (stored in `.env` or `ESPN Fantasy Baseball Cookies.txt`).
- **Google Cloud OAuth client** (only needed if running the Sheets sink): a desktop OAuth client JSON file from a GCP project with the Sheets API enabled. Path stored in `.env` as `GOOGLE_OAUTH_CLIENT_PATH`. First run opens a browser tab for consent; refresh token cached to `output/.sheets_oauth_token.json` (gitignored).
- **`SHEETS_OUTPUT_ID`**: the Google Sheet ID to write to. **When unset, the Sheets sink is a no-op** — this is the canonical "don't touch the live sheet" mode.

### Install
```bash
python -m venv .venv
.venv/Scripts/activate          # Windows
pip install -r requirements.txt
cp .env.example .env            # then fill in
```

### dbt profile
Snowflake adapter, `dev` target. Profile lives at `~/.dbt/profiles.yml`. Project is at `dbt_league/`.

### Verifying setup
```bash
cd dbt_league && dbt debug             # Snowflake connection works
cd .. && python -c "import records; print(records.load_schedule_lookup())"   # ESPN/Snowflake auth + matchup_schedule
```

---

## 5. The weekly workflow (operator-facing)

This is what the user runs every Sunday after the matchup closes:

```bash
# 1. Pull this week's data
python extract/extract.py
# Default: last 21 days of completed matchup_periods. ~3-5 min.

# 2. Push through the model
cd dbt_league && dbt build && cd ..
# Incremental fcts pick up the new matchup_period. ~1-2 min.

# 3. Generate the BBCode recap
python output/generate_summary.py > /tmp/recap.bbcode
# Read, edit if needed, paste to ESPN frontpage.

# 4. Generate the all-time records report (and refresh the Sheet)
python output/generate_records_report.py
# If SHEETS_OUTPUT_ID is set, this also rewrites the three Sheets tabs.
```

### Backfills

When extract logic, seed data, or anything that affects historical rows changes, full-refresh:
```bash
python extract/extract.py --year 2025 --all   # Re-extract entire 2025 season
python extract/extract.py --year 2026 --all   # Re-extract 2026 to date
cd dbt_league && dbt build --full-refresh
```
Backfill duration: ~30 min currently (Phase 4.x bookmark targets ~3-5 min via parallel-fire and multi-view extracts; not done).

### Don't-write-to-the-live-sheet idiom (CRITICAL during dev)

`generate_records_report.py` writes to the user's live league Google Sheet when `SHEETS_OUTPUT_ID` is set. It IS set in the user's `.env`. The script's `load_dotenv()` populates it into the process env, so:

- ❌ `unset SHEETS_OUTPUT_ID && python output/generate_records_report.py` — DOES NOT suppress; `load_dotenv` repopulates.
- ❌ `$env:SHEETS_OUTPUT_ID = '' ; python ...` — DOES NOT suppress; same reason.
- ✅ `SHEETS_OUTPUT_ID=DRY_RUN python output/generate_records_report.py` — works; the env var is set (to a fake value), `load_dotenv` won't override an existing env var, the Sheets call fails with an invalid sheet ID, the script's try/except prints `[sheets] write failed: ...` and BBCode output is unaffected.
- ✅ Direct helper imports: `python -c "from generate_records_report import format_record, ..."` and skip `main()` entirely.

This has burned us twice (once in PowerShell, once in bash). The fix is documented in memory at `feedback_test_running_side_effects.md`. **A `--no-sheets` CLI flag is on the roadmap** (high priority — see §10) precisely to make this less error-prone.

---

## 6. Code map — the modules you'll touch most

### `output/records.py` (~1030 lines; the busiest file)

The data-access surface. Three logical sections:

1. **Public records API** — `get_all_time_records()`, `get_current_season_records()`, `get_records_set_this_week(season, mp)`, `get_record_top_n(stat, ...)`. Used by both BBCode consumers.
2. **Polarity + filter rules** — `get_stat_polarity()`, `get_effective_polarity()` (the latter augments the former with hardcoded `_IMPLICIT_POLARITY` for stats not in the scoring-settings seed: rates, wasted_points, derived stats, score columns), `should_track_record()`, `get_always_tracked_stats()` (reads `is_always_tracked` from the seed).
3. **Orchestrator + helpers** — `get_records_with_contributors(scope, top_n)` is the high-level entry point (filter → tie-collapse → bulk contributor stitch). Used by Sheets writer. Plus `get_team_contributors_bulk()`, `get_player_contributors_bulk()`, `load_schedule_lookup()`, `format_week_label()`, `count_value_occurrences()`, `league_history_count()`, `ordinal()`.

Key concepts encoded here:
- **Seed-to-leaderboard name translation** (`_SEED_TO_LEADERBOARD`): seed has `'1B'`/`'2B'`/`'3B'`/`'64'` (raw stat IDs); leaderboard has `'SINGLES'`/`'DOUBLES'`/`'TRIPLES'`/`'SHO'` (column names). Every seed-keyed lookup goes through this map.
- **`_TEAM_NON_SEED_STATS`** — rate stats / WASTED_POINTS / derived counts. These don't have polarity in the scoring seed; the orchestrator filter allows them at team grain in both directions anyway.
- **`_NON_FCT_COUNTABLE`** — stats that `league_history_count()` can't accurately count via the fcts (rate stats; wasted_points lives in a separate mart). Returns `None`; callers must degrade gracefully.
- **`INLINE_COLLAPSE_THRESHOLD = 3`** — small overflow tiers render comma-joined identities; tiers > 3 fall back to count-only synthetic rows.

This file is the natural place to look first when "the records section is doing something weird." It's also a refactor candidate — see §11.

### `output/formatters.py`

Rendering primitives + display tables:
- `STAT_DISPLAY` (full names: `'HR': 'Home Runs'`) and `STAT_ABBREV` (short forms: `'OUTS': 'IP'`).
- `fmt_value` / `fmt_avg` / `fmt_ip` / `fmt_record_value` (stat-aware, e.g., OUTS → baseball IP notation).
- `format_contributors(contributors, max_n=3, value_fmt=None)` — top-N stat contributor list with tie-handling and zero-tail. Same algorithm shape as the records.py tie-collapse.
- `format_hitter_stats_line` / `format_pitcher_stats_line` / `format_top_scorer_stats_line` — the player-card renderers used in recap and records sections.
- `filter_eligible_slots` — display-side trimming of multi-position eligibility (drops BE/IL/UTIL/IF + slash-style flex slots, collapses generic OF when LF/CF/RF present).

### `output/sheets_writer.py`

OAuth client + 17-col writer + 3 tabs. Three things to know:
- `_replace_tab` does `worksheet.clear()` + `update()`. **This may wipe user-applied formatting** — there's an open follow-up task to switch to in-place updates.
- All three tabs go through `records.get_records_with_contributors`. Tabs 1+2 use `top_n=1` so the tie-collapse algorithm trivially passes rank-1 rows through (except for inline-collapsed tiers of 2-3); Tab 3 uses `top_n=5` and the floor-zero situational stats (CG/HLD/NH/PG fewest) collapse to one row each.
- Polarity-aware Best/Worst labels via `records.best_or_worst_label()`.

### `output/league_notes.py`

Registry-pattern callout module. Each callout is a function taking `ctx` (built once per script run) and returning a list of BBCode lines. Append to `CALLOUTS` to enable; comment out to disable. Three template patterns shipped (`zero_steals`, `no_hitters`, `hr_drought`) — they're examples for the user to extend; right now most don't fire on typical weeks. Try/except per call so a buggy new callout can't kill the recap.

### `output/generate_summary.py`

The weekly recap. Imports from formatters + records + league_notes. Section ordering (locked in Phase 5):
```
[u][b]Week N Recap[/b][/u]    (or "Round 1 Recap" etc. for playoff weeks)
  best/worst overall/hitting/pitching team callouts (with player contributors)
  Top Scorer / Top Hitter / Top Pitcher
  Top 5 Wasted Performances

[u][b]New Records[/b][/u]    (skipped if no records broken/tied this MP)

  Tough Luck / Lucky Bastard / Fair-and-Just  (each only if true)

  League Notes from league_notes.CALLOUTS  (skipped if no callouts fired)

[u][b]Current Season Records[/b][/u]    (9 lines: 6 team + 3 player)

[u][b]All-Time League Records[/b][/u]    (9 lines: 6 team + 3 player)

[u][b]Additional Notes[/b][/u]    (only if LeagueNote.txt has content)
```

### `output/generate_records_report.py`

The all-time records BBCode (records.py-iterated stats, top-tier with contributors + 2nd-place tier mention). Plus the opt-in Sheets sink at the very end:
```python
sheets_id = os.getenv("SHEETS_OUTPUT_ID")
if sheets_id:
    import sheets_writer
    try:
        sheets_writer.write_records(sheets_id)
    except Exception as e:
        print(f"[sheets] write failed: {e}")
else:
    print("[sheets] SHEETS_OUTPUT_ID not set; skipping Sheets sink")
```

### dbt models — the layered story

- `stg_box_scores` (view): 1:1 reshape of `RAW.BOX_SCORES` per `(season_year, scoring_period, team_id, player_id, lineup_slot)`. Adds `team_abbrev`, `eligible_slots`, `lineup_slot_category` (pitching/hitting/inactive), `games_played` (0/1/2 — DH support).
- `stg_player_stat_breakdowns` (view): per-stat rows from RAW; joins `stat_classification` for `stat_category`.
- `stg_scoring_settings` (view): latest snapshot from append-only RAW; per-stat `points_per_unit`.
- `int_player_daily_stats` (view): slot-stat-category-validity-filtered (gated on `var('strict_slot_validity', true)`). Slot-blind kona stats credited only when `stat_category` matches `lineup_slot_category`.
- `int_player_weekly_performance` (view): weekly rollup with wide pivots (count + `_pts` per stat) and catch-all totals (`total_hitting_stat_pts`, `total_pitching_stat_pts`, `total_stat_pts`) used for `calculated_*`.
- `fct_weekly_player_performance` (incremental table): active-only (BE/IL/FA filtered out at this layer). Wide convergence row per `(season_year, matchup_period, player_id)`.
- `fct_weekly_team_performance` (incremental table): SUM rollup of player fct (the team_total = SUM(players) invariant — with one documented exception: `platform_points` is sourced from wrapper `home_score` to honor commissioner adjustments).
- `mart_wasted_points` (view): bench/IL/FA contributions per player-week, bucketed `'FA' | 'ROSTERED_INACTIVE'`.
- `mart_stat_leaderboard` (view): UNPIVOT of fct columns into long format, ranked top-10 per `(grain, stat_name, scope, direction)`. Four ranked CTEs union'd: `most`/`fewest` × `all_time`/`current_season`. Phase 6.3.3 added derived stats (PA, SB-CS, W-L, SV-BLSV) inline at mart, rate stats from fct surfaced into team-grain UNPIVOT, and a `mart_wasted_points` LEFT JOIN.

The mart is direction-agnostic (`most`/`fewest`); polarity-aware Best/Worst label belongs at consumer side via `records.best_or_worst_label`.

---

## 7. Domain knowledge / tribal knowledge

The hard-won stuff. If you need to debug something weird, check here first.

### ESPN API quirks we work around

1. **`box_scores()` doubleheader bug** (Phase 3.3 → 3.3.1): the espn-api wrapper builds a dict keyed by scoringPeriodId, so on DH days the second game's stats silently overwrite the first. We bypass this by going to the raw `kona_player_info` view (formerly `mRoster`) for stat values and summing splits per scoring period. The wrapper is retained for matchup structure (lineup_slot, owners, team_ids, pairings) and as a fallback when kona misses a player.
2. **Stat ID 12 (HBP batter) and 42 (HBP pitcher) name collision** (Phase 4): the wrapper's `STATS_MAP` collapses both under `'HBP'`. Fixed at extract via `_STAT_ID_TO_NAME[42] = 'HBP_P'`. **Pattern**: when the wrapper drops information, override at extract — don't disambiguate downstream. The extract logs a warning at startup if `STATS_MAP` contains other unflagged collisions.
3. **`eligibleSlots` empty for rostered players from kona** (Phase 5): kona only populates this for FAs. Wrapper's `Player.eligibleSlots` has it for everyone. Extract pulls from wrapper for rostered, kona for FAs.
4. **Stat ID 64 = Shutouts**: ESPN's stat list has no human-readable name for stat 64; wrapper passes through as numeric `'64'`. Per Phase 3.2 reverse-engineering (5 pts/shutout matched a Hosstros delta), confirmed it's SHO. Seed keeps the literal `'64'` (matching the raw breakdown VARIANT key); int model aliases via `case when stat_name = '64' then ... as sho`.
5. **Stat IDs 22, 61, 78, 79, 80**: ESPN internal flags. Documented in seed `notes` column. Don't pivot.
6. **Stat ID 66 (PG)** was previously misidentified as "Pitches Per Game"; Phase 3.2 confirmed via scoring settings (250-pt bonus) it's Perfect Games.
7. **Stat ID 30 = Hit for the Cycle (CYC)** (Phase 7 archaeology): same scoring-weight pattern as PG/SHO. 15 pts/unit (rare-event tier with NH no-hitters), 2 observed rows across 2 seasons matching real cycle candidates. Seed labels it correctly post-Phase-7. `is_record_candidate=false` for now (no wide column on any fact); v1.x candidate for promotion to a tracked stat with a league_notes.py "First cycle of the season!" callout.
8. **Stat ID 31 (seed name `CYC`)** is NOT cycles — labeled as such since seed import but disproven by Phase 7: 148 non-zero rows across 113 players over 2 seasons (impossible for cycles; real MLB has ~5-10 per year league-wide) and no scoring weight. Some other ESPN daily-achievement flag (multi-hit game? extra-base-hit-game?). Seed updated to flag the mislabel; `is_counting=false` so it drops at int_player_daily_stats.

### Scoring-settings + leaderboard naming

- The `stat_classification` seed uses ESPN's raw stat IDs/names: `'1B'`, `'2B'`, `'3B'`, `'64'`, `'B_IBB'`, `'HBP_P'`, etc.
- The leaderboard mart uses spelled-out column names: `'SINGLES'`, `'DOUBLES'`, `'TRIPLES'`, `'SHO'`, etc.
- `_SEED_TO_LEADERBOARD` in `records.py` translates between them. Add to this dict whenever a new seed→column rename happens.

### Polarity conventions

- `get_stat_polarity()` derives from `sign(points_per_unit)` in `stg_scoring_settings`. Stats without a row are `'neutral'` and don't surface as records.
- `get_effective_polarity()` augments with `_IMPLICIT_POLARITY` for stats not in scoring settings: rates (ERA/WHIP/BB9/HR9 → negative; K9/KBB → positive), WASTED_POINTS → negative, derived stats (PA/SB-CS/W-L/SV-BLSV → positive), score columns → positive.
- `is_always_tracked` seed flag (Phase 6.3.3): bypasses the polarity rule at team grain. Currently flagged: H, TB, XBH, SF, ER, PA. Edit the seed to add/remove members; reseed (`dbt seed --full-refresh -s stat_classification`).

### `platform_*` vs `calculated_*`

This distinction is load-bearing — get it wrong and analytics will lie:
- **`platform_*`**: direct API passthrough, zero math. Player-level is slot-blind (kona's `appliedTotal`); team-level is wrapper's `home_score`/`away_score` (slot-aware AND inclusive of commissioner adjustments).
- **`calculated_*`**: our derivation under current-season scoring settings, with full slot-validity filter applied at `int_player_daily_stats`.
- **The team_total = SUM(players) invariant has a documented exception**: for `platform_points` specifically, team-level is the wrapper's authoritative number, NOT the player rollup. The divergence (when slot misuse exists) is meaningful, not drift, and is exposed via `platform_calculated_delta`. All other counting/scoring columns hold the invariant.
- **Records flipped to `calculated_*` in Phase 5**: cross-season comparison is meaningful only under current weights. The recap section's best/worst team callouts and Top Hitter/Pitcher still source from `platform_*` because the recap is about what happened (W/L outcomes are platform-determined).

### Slot validity filter

`int_player_daily_stats` filters `stat_category = lineup_slot_category` (or `'fielding'` or `'inactive'`), gated on `var('strict_slot_validity', true)`. Set the var to false to disable in case ESPN's behavior changes cross-platform. Inactive (BE/IL/FA) rows bypass the filter so wasted-points sees the full stat lines.

### `is_abnormal` matchup periods

`matchup_schedule.is_abnormal` flags weeks that should be excluded from records (e.g., All-Star break, weather-shortened weeks). Every leaderboard CTE filters on `is_abnormal = false`. Don't bypass.

### Playoff weeks

`matchup_schedule.is_playoff` and `matchup_schedule.playoff_round` (currently `'Round 1'` / `'Semi-Finals'` / `'Finals'`) drive `format_week_label()` substitution. As of handoff: 2025 MP24-26 are tagged playoff. 2026 playoffs not yet played.

### Connection management (current state)

`output/records.py::query_snowflake` opens and closes a connection per call. Cost is modest (~10-20 calls per script run). Consolidating to a single connection is on the roadmap (see §10). When you fix this, watch for: cursor cleanup, error-handling around connection failure, and don't let one user-facing crash leak the connection.

### Worktree convention

Each phase happens in its own worktree at `.claude/worktrees/<name>/` with its own branch, then merges back to main. Cleaned up post-merge via `git worktree remove`. Long-lived `phase-3.2` worktree exists for historical reasons; ignore unless you specifically need that branch.

---

## 8. Conventions and patterns

These are calls already made; lead with the established pattern rather than re-debating.

### dbt patterns

- **Wide convergence facts at consumer grain**. Counting + rate + `_pts` + scoring totals all in one wide row per (player|team)-week.
- **`fct_weekly_team_performance` reads from `fct_weekly_player_performance`**. Team totals are *defined* as `SUM(players)`, eliminating drift (with the `platform_points` exception noted above).
- **Macros for grain-agnostic formulas** (`macros/rate_stats.sql`).
- **Slot-agnostic intermediate, filter at fact**. `int_player_daily_stats` keeps `lineup_slot`. Active filter (`NOT IN ('BE', 'IL', 'FA')`) at the fact.
- **Disambiguation at intermediate, not staging** (Phase 3.2): when a name collision needs context to resolve (e.g., HBP batter vs HBP pitcher pre-Phase-4), do it at int. Staging stays a pure reshape. **Phase 4 superseded this pattern for HBP** by overriding at extract via `_STAT_ID_TO_NAME` — better still.
- **Catch-all totals beat per-stat enumeration for `calculated_points`**. Sum `stat_points` across all categories regardless of whether the stat has a wide pivot column. Robust to seed updates.
- **Per-stat `*_pts` columns retained alongside catch-all**. Catch-all is for `calculated_*`; per-stat columns serve consumer-side rankings.
- **FA determination by anti-join** (Phase 4): query kona without status filter, anti-join against wrapper's `box_scores()` for that scoring_period. Status fields don't reflect status as of historical periods — anti-join handles transactions correctly without a transaction log.
- **Var-toggleable filters** for behaviors that may change cross-platform (`var('strict_slot_validity', true)`).
- **Always `dbt seed --full-refresh` when changing seed CSVs**. Plain `dbt seed` may treat the existing table as already-loaded and skip.
- **Snowflake reserved words**: avoid `as rows`, `as values`, `as group`. Use `row_count`, `record_count`, etc.

### Records / leaderboard patterns (Phase 6.3.3)

- **Mart stays thin; contributor stitching via Python helpers** when consumer count is low. Denormalize when 3+ consumers materialize.
- **Threshold filter at output, not in mart**. Constants (e.g., `HITTER_AB_THRESHOLD = 225`) live in `records.py`. Exception: player-grain rate stats are dropped at mart layer entirely (Path A) — 1-IP relief outliers carry no signal and would dominate ad-hoc mart queries.
- **Counts not points for player-grain contributors** — readability over precision.
- **`is_always_tracked` seed flag** drives team-grain stats that surface as records regardless of polarity.
- **`format_week_label` pattern**: load schedule lookup once via `records.load_schedule_lookup()`, thread the dict through every formatter that needs week labels. Explicit threading (parameter on every relevant function) instead of module-global state.
- **`league_notes.py` registry pattern**: each callout is a function appended to `CALLOUTS`; comment out to disable. Try/except per callout so a buggy one can't kill the recap.

### Output script conventions

- **BBCode formatted output** for the ESPN frontpage. Tags `[b]`, `[u]`, `[i]`.
- **Owner names in records sections, not weekly recap.**
- **Logs to `output/logs/`** — timestamped `.txt` files, gitignored.
- **`output/LeagueNote.txt`** — gitignored optional file for ad-hoc commissioner notes; appended verbatim under `[u][b]Additional Notes[/b][/u]` if non-empty.
- **utf-8 stdout reconfig at script start** (Phase 6.3.3): Windows defaults to cp1252 which crashes on emoji team names (`Team Hybrid✊🏽` is a real team in this league). When the script crashes mid-print, opt-in sinks like Sheets never fire — silent failure. Idempotent `try: sys.stdout.reconfigure(encoding='utf-8') except (AttributeError, OSError): pass` pattern at top.

### Git hygiene

- One bundled phase commit + a doc commit per phase.
- Worktree cleanup with `git worktree remove`; if files locked (dbt logs), kill processes or use `--force`.
- Co-authored commits when AI-assisted (per the user's preference).
- **Don't push without explicit user permission**. Local main is currently 5 ahead of `origin/main`; the user wants to review before push.

---

## 9. What works today

Verified end-to-end as of Phase 6.3.3 ship (2026 Week 5):

- `dbt build --full-refresh` runs clean: 58 PASS / 0 ERROR / 0 WARN.
- `python output/generate_summary.py` produces the full BBCode recap with all sections, including:
  - 8 new tracked stats (GDP, B_IBB, HBP_P, BLSV, NH, PG, PK, SHO) surfacing as records when records are set.
  - Playoff round names where applicable (`Round 1` for 2025 MP24, etc.).
  - Polarity-aware Best/Worst direction labels.
  - emoji-safe rendering (utf-8 stdout reconfig).
- `python output/generate_records_report.py` produces BBCode + writes 3 tabs to Google Sheets (when `SHEETS_OUTPUT_ID` set):
  - Tab 1 (All-Time): 100 rows, rank-1 holder per (stat, direction).
  - Tab 2 (Current Season): 100 rows, same shape scoped to active season.
  - Tab 3 (Leaderboard Dump): 807 rows, top-5 per (stat, direction, scope), both scopes interleaved.
  - Tie-collapse fires for floor-zero situational stats (CG/HLD/NH/PG fewest collapse to single rows with N in the hundreds).
  - 17-column schema with per-row contributors (player names + counts for team grain, stat names + counts for player grain).
- 3 archived smoke tests pass (`archive/chunk{3,4,5}_smoke.py`).

### Coverage

- **Snowflake state**: 2025 (full season + playoffs MP24-26) and 2026 (MP1-5) loaded.
- **Live Sheet**: populated with current Phase 6.3.3 17-col data (1007 rows across 3 tabs). The user accepted this state during verification (sheet was previously a 10-col schema from Phase 6.3.2 — clean replacement).

---

## 10. Roadmap, prioritized

The user wants this section explicit, in priority order. **Top-of-list items have direct impact on the user's weekly use; lower-priority items are quality / portfolio polish.**

### NOW — high-impact, ship before anything else

1. **`--no-sheets` / `--dry-run` CLI flag on `generate_records_report.py`** (small, ~15 min). Has burned us twice. The current "set `SHEETS_OUTPUT_ID=DRY_RUN` to suppress" idiom works but is non-obvious. A flag makes it explicit. Pair with: log `[sheets] dry-run; would have written N rows` so the verifier sees the intent.
2. **Sheets formatting preservation** (already spawned as a follow-up task; chip pending in user's queue). `_replace_tab` does `worksheet.clear()` + `update()` — likely wipes user-applied formatting. Switch to in-place `update()` that overwrites cells without clearing, plus targeted `batch_clear` for trailing rows when the new dataset is smaller. Verify by hand-formatting a tab, running the script, and confirming colors / frozen rows / column widths survive.
3. **"No-Hitters: 0 by N teams" rendering** (small, ~10 min). When rank-1 stat_value=0 for stats with `is_always_tracked=false`, the section is misleading (implies a record exists). Either skip the section entirely or render as `Nobody has thrown a no-hitter yet`. Affects NH, PG, SHO most visibly post-Phase-6.3.3 because they're newly surfaced.

### NEXT — Phase 7 (v1.0 portfolio prep)

The user has flagged Phase 7 + refactoring as their next session. Don't start without coordination. Scope (per `Phase 6.3.3 Documentation.md` and `project_phase_plan.md`):

4. **`CHANGELOG.md`** in keepachangelog format. Map phases retroactively to semver: 0.1.0 = Phase 1 → 1.0.0 = this release.
5. **`README.md` rewrite** as the entry point: 30-second pitch, sample output screenshot, Mermaid architecture diagram, "notable engineering decisions" linking to phase docs, "what this demonstrates" recruiter-facing section, separate `SETUP.md` for the bring-your-own-credentials path.
6. **`ROADMAP.md`** with Now / Next / Later / Won't Do buckets (this section is already a draft of that — promote and curate).
7. **dbt docs**: fill in description fields across all `schema.yml` files, add `exposures` for the output scripts, `dbt docs generate` + push `target/` to `gh-pages` for hosted lineage.
8. **Repo hygiene**: pinned `requirements.txt`, MIT or Apache 2.0 LICENSE, comprehensive `.gitignore`.
9. **Tag `v1.0.0`**, GitHub Release with changelog as release notes.
10. *(Optional post-release)*: r/dbt, r/dataengineering, LinkedIn share.

### REFACTORING — opportunities the user has flagged

The user mentioned wanting to address some refactoring during their separate Phase 7 session. Candidates:

11. **Connection-management consolidation** (medium, ~2 hours). Single Snowflake connection per script run; pass `conn` into query functions. Replaces `query_snowflake`'s open-and-close-per-call pattern. ~10-20 round-trip handshakes saved per script run. Bundle with the Phase 4.x extract optimizations if going hard on perf.
12. **Split `output/records.py`** (medium, ~3 hours). At 1030 lines it's the largest module. Natural splits: `records/data.py` (raw queries), `records/polarity.py` (polarity + filter rules), `records/orchestrator.py` (`get_records_with_contributors`, bulk contributors), `records/collapse.py` (tie-collapse), `records/schedule.py` (`load_schedule_lookup`, `format_week_label`, `ordinal`, `league_history_count`). Backward compat: keep `output/records.py` as a thin re-export shim during transition.
13. **Factor shared output-script boilerplate** (small, ~30 min). Both `generate_summary.py` and `generate_records_report.py` have identical utf-8 stdout reconfig + dotenv loading + (now) schedule_lookup loading. Factor into `output/_setup.py` or similar.
14. **Conditional 3rd "Top Scorer" line** (small, ~30 min). Long-standing backlog item from Phase 5. Top Scorer/Hitter/Pitcher render together; Top Scorer often duplicates one of the others when no two-way Ohtani week occurred. Show only when overall winner had BOTH non-zero hitting AND non-zero pitching contributions. Affects both recap and records sections.

### LATER — substantive features, not blocking

15. **Dynamic rate-stat thresholds from lineup-slot config**. Currently `HITTER_AB_THRESHOLD = 225` and `PITCHER_IP_THRESHOLD = 50` are placeholder constants in `records.py` with v2-vision comments. Activates if/when team-grain rate stats need a threshold (currently moot since Path A dropped player-grain rates at mart).
16. **Tracked-stats config seed/YAML for cross-league portability** (v2). The Phase 6.3.3 chunk-1 stat list is hardcoded into mart UNPIVOT lists; cross-league portability would want a config-driven version.
17. **"Non-playoff teams during playoff weeks" edge case** (v1.x). 8 teams play; 6 teams have `is_playoff=true` `matchup_period` rows but are eliminated and contribute zero stats — currently they show as "fewest of everything" records. Not blocking but slightly misleading.
18. **Wire `owner_nicknames` seed into models**. Currently the seed exists but isn't joined; output scripts read it ad-hoc.
19. **`fct_team_career_stats` mart**. Career-aggregate equivalent of `fct_weekly_team_performance`.
20. **Investigate explicit `pointsAdjustment` field** in ESPN API to split `platform_calculated_delta` into clean `commissioner_adjustment` + `derivation_delta`.
21. **Verify stat ID 30** (15 pts per, only 1 observed row) — is this a real scored stat we're missing?
22. **GitHub Actions CI on PRs**: `dbt test` + Python compile checks.
23. **Python tests for output formatters**. Currently no test coverage on the rendering layer; a few golden-output tests would catch a lot.
24. **Multi-sink output abstraction**. Defer until a 2nd non-Sheets sink emerges (Discord webhook, email, etc.).

### EXTRACT PERFORMANCE — Phase 4.x parallel track

25. **Multi-view single HTTP call** (`?view=mMatchupScore&view=kona_player_info`).
26. **Batched kona via `filterStatsForScoringPeriodIds`**.
27. **Parallel-fire of wrapper calls**.
   - All three combined: backfill drops from ~30 min to ~3-5 min. The user wants this but it's not blocking weekly cadence (incremental extracts are already fast; this matters when re-extracting full seasons for any reason).

### WON'T DO — explicit deferrals

- **Path B (DuckDB retarget) and Path C (hosted multi-tenant)**: post-1.0 considerations only.
- **Frequency-table / "Notable Frequencies" tab** (rejected during Phase 6.3.3): tie-collapse handles this naturally; no separate output needed.
- **AB-SO contact-rate proxy** (deferred per Phase 6.3.3 spec discussion).

---

## 11. The user's continued operational dependence

The user runs this every week. Behaviors they specifically rely on — break these and you'll hear about it:

- **Section ordering in `generate_summary.py`** (locked Phase 5; documented in §6 above). Don't reorder without coordination.
- **Header conventions**: `[u][b]Section Name[/b][/u]` for sections, `[b]Label[/b]: value` for callouts.
- **Player-card shape**: `Player (TeamAbbr), X.X pts -- {stats}` — locked Phase 5.
- **Records show owner names**, recap doesn't — locked Phase 5.
- **`Matchup #N` was renamed to `Week N` everywhere** — locked Phase 5.
- **Playoff weeks render as round names** (`Round 1` / `Semi-Finals` / `Finals`) instead of `Week 24-26` — locked Phase 6.3.3 chunk 6.
- **Sheet schema is 17 cols across 3 tabs** — locked Phase 6.3.3 chunk 5. Adding a column requires bumping `_HEADER` in `sheets_writer.py` and the row-builder logic; consumers of the Sheet (the user's manual analysis) probably do not adapt automatically.
- **`SHEETS_OUTPUT_ID` env-var opt-in** for the Sheets sink — don't accidentally make it opt-out.
- **`output/LeagueNote.txt` is appended verbatim** under "Additional Notes" when non-empty. The user uses this for ad-hoc commissioner messages each week.

If you change a public-facing behavior, surface it before merging. The user is the most important QA on this project.

### What the user does NOT do

- They don't run dbt against prod. There's no prod environment — `dev` target IS the operational target. The "test" lives in the verification you run before shipping.
- They don't read the dbt docs catalog (yet — that's Phase 7). For now, model documentation lives in inline comments + `schema.yml` + the phase docs.
- They don't review every diff line-by-line. They trust the verification (`dbt build` clean + smoke tests + spot-check of one BBCode output) more than the diff. Don't skip verification.

---

## 12. How to verify a change

The pattern that's worked:

1. **Make changes in a worktree branch**, not main.
2. **`dbt build --full-refresh`** if you touched anything in `dbt_league/` (or `dbt build` for incremental fcts only). Should be 58+ PASS / 0 ERROR / 0 WARN.
3. **Run the relevant Python script with sinks suppressed**:
   - For BBCode-only verification of `generate_records_report.py`: `SHEETS_OUTPUT_ID=DRY_RUN python output/generate_records_report.py`
   - For `generate_summary.py`: it has no opt-in sinks, run as-is.
   - For deep changes to `records.py`: rerun the smoke tests in `archive/`.
4. **Spot-check the output**: section ordering, polarity-aware labels right, playoff weeks render as round names, no UnicodeEncodeErrors.
5. **Diff review**: `git diff` should be minimal-and-focused.
6. **Commit + (separate) doc commit**. Phase docs at repo root.

If you make schema changes (new mart column, new seed column), update `schema.yml` and `dbt_project.yml` `column_types` for seeds.

---

## 13. Memory + reference index

Pointer reading:

- **`Phase 1.0 Documentation.md` → `Phase 6.3.3 Documentation.md`** — sequential phase docs in repo root. Each captures the architectural decisions of that phase. Phase 6.3.3 is the most recent (and longest); Phase 5.0 is a good model for what a thorough phase doc looks like.
- **`Phase 6.3.3 Handoff.md`** — soon-to-be-deleted continuation brief from before the Phase 6.3.3 ship. Superseded by `Phase 6.3.3 Documentation.md`. (Per Phase 5.0 convention, the handoff stub gets deleted when the formal doc lands.)
- **Memory files** at `~/.claude/projects/C--Users-kyled-projects-espn-league-manager/memory/`:
  - `MEMORY.md` — index.
  - `user_role.md` — what this project is for + portfolio context.
  - `project_phase_plan.md` — phase cadence convention + shipped/upcoming.
  - `project_conventions.md` — the patterns this handoff doc references.
  - `feedback_documentation_source_of_truth.md` — when phase docs disagree with overview, trust phase docs.
  - `feedback_test_running_side_effects.md` — the Sheets-sink suppression idiom (don't repeat the mistake we made twice).
- **`archive/`** — verification artifacts. Smoke tests + dbt build logs + diagnostic scripts. Useful for re-verification post any future refactor.

---

## 14. Contact / coordination notes

- **Don't push to `origin/main`** without explicit user approval. Currently 5 commits ahead.
- **The user does verification in their own way** — typically by running the BBCode scripts and eyeballing output against expected. Don't merge a change until they've signed off.
- **Phase docs are the canonical record of decisions**. When this handoff doc disagrees with a phase doc, the phase doc wins (per `feedback_documentation_source_of_truth.md`).
- **The user is open to refactoring** but has limited time per session. Bundled, documented refactors land better than scattered cleanups.

---

## TL;DR for the new dev

- **Read** `Phase 6.3.3 Documentation.md` (most recent), `Phase 5.0 Documentation.md` (best-shaped phase doc), and the memory files. ~2 hours.
- **Set up** the venv + `.env` + dbt profile. ~30 min.
- **Run** the weekly workflow against the user's existing data: `dbt build` + `python output/generate_summary.py | head -50`. Confirm output renders. ~10 min.
- **Pick** one item from the NOW list (§10) and ship it. The `--no-sheets` flag is the easiest first PR.
- **Don't** touch section ordering, header conventions, schema columns, or push to origin without coordination.

The project is in a healthy place. Phase 6.3.3 just shipped a substantial expansion (8 new tracked stats, derived stats, mart-side wasted_points, polarity-aware labels, playoff round naming, league_notes registry, 17-col Sheets schema) and the verification was clean. Next moves are polish (Phase 7) and the small NOW-list items.

Welcome.
