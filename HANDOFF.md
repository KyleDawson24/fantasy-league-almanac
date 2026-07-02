# ESPN Fantasy Baseball Front-Page Generator — Project Handoff

**Audience:** A developer taking over day-to-day development. The original maintainer (the user) continues to operate this weekly for their own 14-team H2H league, so behavior changes that break the weekly post will surface immediately. Read top-to-bottom once; thereafter, jump to the section you need.

**Status as of handoff:** v1.1.0 shipped the league almanac Google Sheets surface on top of the v1.0 Phase 7 architecture. The stable core is still the symmetric active/inactive dbt model, seed-driven `mart_stat_leaderboard`, and Phase 7 records-module split; the new product surface is `output/generate_almanac_sheet.py`, which builds Home, Records, Team Weeks, and per-team active-stats tabs. Last operational verification: v1.1.0 almanac visual check, golden TSV snapshot at `tests/fixtures/almanac_v1_1_0/`, pytest default + warehouse green, dbt build clean, and static docs regenerated.

---

## 1. What this is

An end-to-end ELT pipeline that turns ESPN's fantasy baseball API into:

1. A **BBCode-formatted weekly recap** (`output/generate_summary.py`) that the league commissioner posts to the ESPN league frontpage every Sunday after the matchup_period closes.
2. A **BBCode all-time-records report** (`output/generate_records_report.py`) — same target audience, covers the full league history.
3. A **Google Sheets league almanac** (`output/generate_almanac_sheet.py`, opt-in via env var) — Home, Records, Team Weeks, and one active-stats tab per fantasy team. This is the primary Sheets surface as of v1.1.0.
4. A **legacy Google Sheet records sink** (`output/sheets_writer.py`, opt-in via env var) — All-Time Records / Current Season Records / Leaderboard Dump. Still present, but not the forward-looking Sheets product.

The user runs it weekly. Output #1 is the canonical league deliverable; #2 fires alongside; #3 is the browsable league archive/almanac; #4 is legacy support.

This is also a **portfolio piece** — the user is targeting Senior Data Analyst / Analytics Lead roles. Every architectural decision has been documented in `Phase X.Y Documentation.md` files in the repo root for that reason. Don't optimize the project as if it were closed-source production code; the documentation IS part of the deliverable.

**Path A** (the only active path) is Snowflake-as-warehouse. Path B (DuckDB retarget) and Path C (hosted multi-tenant) have been considered and deferred — but cross-platform readiness has shaped some staging-layer decisions (e.g., a thin staging contract that other adapters could plug into).

---

## 2. Architecture in one paragraph

Three stages: **extract** (Python pulls ESPN's API → Snowflake `RAW` schema, append-only), **transform** (dbt builds `staging` views → `intermediate` views → `marts` facts and the leaderboard), **output** (Python reads marts and produces BBCode + Sheets). Each stage is independently runnable. The user's weekly cadence is `extract → dbt build → output/generate_summary.py → output/generate_records_report.py → output/generate_almanac_sheet.py`. Backfills happen when extract logic or seed data changes (`extract --year YYYY --all` then `dbt build --full-refresh`).

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
│   │   ├── owner_nicknames.csv          # 14 owners; feeds dim_owner (owner_display resolution)
│   │   └── player_nicknames.csv         # Tiny; ad-hoc display overrides
│   ├── tests/                           # Singular tests: cross-model invariants + data canaries
│   └── models/
│       ├── staging/                     # 1:1 reshapes of RAW; minimal logic
│       ├── intermediate/                # Slot-validity filter, daily rollup, owner bridge
│       └── marts/
│           ├── core/                    # Contract layer: dims + facts
│           └── reporting/               # Consumer marts: leaderboard, benchmarks, matchup,
│                                        #   roster snapshot, draft board
├── output/
│   ├── records.py                       # Data access layer (1000+ lines; see §6)
│   ├── formatters.py                    # STAT_DISPLAY/ABBREV maps + rendering helpers
│   ├── sheets_writer.py                 # Legacy Google Sheets records sink; opt-in
│   ├── almanac_sheets.py                # v1.1 Google Sheets league almanac
│   ├── generate_almanac_sheet.py        # Almanac entry point + TSV previews
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
│   ├── *.json                           # Older debug payloads
│   ├── phase_7_working/                 # Phase 7 cross-session scaffolding —
│   │                                    #   architecture review, continuation
│   │                                    #   briefs, kickoff handoff (not the
│   │                                    #   canonical record; see Phase 7
│   │                                    #   Documentation.md at repo root)
│   └── research/                        # gitignored local-only research scripts
│                                        #   (test_espn.py, test_kona_returns.py,
│                                        #   etc. — relocated from repo root in
│                                        #   Phase 7 Step 4)
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
- **Snowflake**: account, user, auth credentials, database (`ESPN_FANTASY`), warehouse. Stored in `.env`. Auth is either key-pair (recommended; required when MFA is enforced) via `SNOWFLAKE_PRIVATE_KEY_PATH` + optional `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`, or password (legacy) via `SNOWFLAKE_PASSWORD`. `db.py::_build_config` detects which one is set. See SETUP.md §4.
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

# 5. Refresh the league almanac Google Sheet
python output/generate_almanac_sheet.py
# Use --no-sheets or --preview-dir during development.
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

`generate_records_report.py` and `generate_almanac_sheet.py` write to the user's live league Google Sheet when `SHEETS_OUTPUT_ID` is set. It IS set in the user's `.env`. The scripts' `load_dotenv()` path populates it into the process env, so:

- ❌ `unset SHEETS_OUTPUT_ID && python output/generate_records_report.py` — DOES NOT suppress; `load_dotenv` repopulates.
- ❌ `$env:SHEETS_OUTPUT_ID = '' ; python ...` — DOES NOT suppress; same reason.
- ✅ `SHEETS_OUTPUT_ID=DRY_RUN python output/generate_records_report.py` — works; the env var is set (to a fake value), `load_dotenv` won't override an existing env var, the Sheets call fails with an invalid sheet ID, the script's try/except prints `[sheets] write failed: ...` and BBCode output is unaffected.
- ✅ Direct helper imports: `python -c "from generate_records_report import format_record, ..."` and skip `main()` entirely.

The fix is documented in memory at `feedback_test_running_side_effects.md`. As of v1.0 there's a `--no-sheets` CLI flag on `generate_records_report.py` for explicit suppression, and the almanac entry point also has `--no-sheets` plus `--preview-dir` for TSV inspection. The DRY_RUN env-var workaround above is kept as a fallback for direct-helper-import paths that bypass the CLI.

---

## 6. Code map — the modules you'll touch most

### `output/records.py` + `records_data.py` + `records_logic.py` (Phase 7 3-way split)

The data-access surface. Pre-Phase-7 this was one 930-line module; the
split landed in Phase 7 Step 3. Current shape:

- **`output/records.py`** (~238 lines): two workflow orchestrators
  (`get_records_set_this_week`, `get_records_with_contributors`) plus
  explicit named re-exports so the four consumer scripts keep working
  unchanged.
- **`output/records_data.py`** (~377 lines): the SQL access surface --
  rank-1 record fetches, top-N-per-stat, contributor lookups, bulk
  contributor batches, `league_history_count` and its
  `count_value_occurrences` shortcut, schedule lookup. Imports
  `from db import query_snowflake`.
- **`output/records_logic.py`** (~382 lines): pure consumer-side rules
  -- `should_track_record`, `best_or_worst_label`, `format_week_label`,
  `ordinal`, tie-collapse (`collapse_ties`, `_collapse_one_group`),
  `_sort_new_records`. One explicit `from records_data import
  count_value_occurrences` for the saturated-tier backfill (honest
  one-way edge; no cycle).

Polarity and auto-tracked sets are NOT in this code anymore -- those
moved to the `stat_classification` seed at Phase 7 B1 and surface via
`stat_catalog.get_polarity_map()` / `get_auto_tracked()`.

Key concepts:
- **Seed-to-leaderboard name translation** lives in `dim_stat`.
  The seed has `'1B'`/`'2B'`/`'3B'`/`'30'`/`'64'` (raw stat IDs); the
  leaderboard has `'SINGLES'`/`'DOUBLES'`/`'TRIPLES'`/`'CYC'`/`'SHO'`
  (column names). `dim_stat.leaderboard_name` is the single source of
  truth; Python display helpers read it through `output/stat_catalog.py`.
- **`_TEAM_NON_SEED_STATS`** (in `records_logic.py`) -- rate stats /
  WASTED_POINTS / derived counts. These don't have polarity in the
  scoring seed; the orchestrator filter allows them at team grain in
  both directions anyway.
- **`_NON_FCT_COUNTABLE`** (in `records_data.py`) -- stats that
  `league_history_count()` can't accurately count via the fcts (rate
  stats; WASTED_POINTS derives from the inactive facts). Returns
  `None`; callers must degrade gracefully.
- **`INLINE_COLLAPSE_THRESHOLD = 3`** (in `records_logic.py`) -- small
  overflow tiers render comma-joined identities; tiers > 3 fall back
  to count-only synthetic rows.

Start here when "the records section is doing something weird."

### `output/formatters.py`

Rendering primitives + display tables:
- `STAT_DISPLAY` (full names: `'HR': 'Home Runs'`) and `STAT_ABBREV` (short forms: `'OUTS': 'IP'`).
- `fmt_value` / `fmt_avg` / `fmt_ip` / `fmt_record_value` (stat-aware, e.g., OUTS → baseball IP notation).
- `format_contributors(contributors, max_n=3, value_fmt=None)` — top-N stat contributor list with tie-handling and zero-tail. Same algorithm shape as the records.py tie-collapse.
- `format_hitter_stats_line` / `format_pitcher_stats_line` / `format_top_scorer_stats_line` — the player-card renderers used in recap and records sections.
- `filter_eligible_slots` — display-side trimming of multi-position eligibility (drops BE/IL/UTIL/IF + slash-style flex slots, collapses generic OF when LF/CF/RF present).

### `output/sheets_writer.py`

Legacy OAuth client + 17-col writer + 3 records tabs. Three things to know:
- `_replace_tab` does `worksheet.clear()` + `update()`. **This may wipe user-applied formatting** — there's an open follow-up task to switch to in-place updates.
- All three tabs go through `records.get_records_with_contributors`. Tabs 1+2 use `top_n=1` so the tie-collapse algorithm trivially passes rank-1 rows through (except for inline-collapsed tiers of 2-3); Tab 3 uses `top_n=5` and the floor-zero situational stats (CG/HLD/NH/PG fewest) collapse to one row each.
- Polarity-aware Best/Worst labels via `records.best_or_worst_label()`.

### `output/almanac_sheets.py` + `output/generate_almanac_sheet.py`

The v1.1 Google Sheets league almanac. Generates Home, Records, Team
Weeks, and one active-stats tab per team. It currently contains both
data-access SQL and rendering logic; this is accepted for v1.1.0 so the
league can review the product, but v1.1.1 should split it along the
records.py layer lines and move reusable analytical contracts into dbt.
Use `python output/generate_almanac_sheet.py --no-sheets --preview-dir
output/almanac_preview` during development; the golden v1.1.0 snapshot
lives at `tests/fixtures/almanac_v1_1_0/`.

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

  League Notes from league_notes.CALLOUTS  (skipped if no callouts fired)
  Includes Tough Luck / Lucky Bastard / Fair-and-Just (matchup-outcome
  callouts; migrated into the registry in v1.x) plus the rare-event and
  oddity callouts (no_hitters, cycles, clean_slate, zero_steals, ...).

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

Staging (`models/staging/`, one view per raw table):

- `stg_box_scores` (view): 1:1 reshape of `RAW.BOX_SCORES` per `(season_year, scoring_period, team_id, player_id, lineup_slot)`. Adds `team_abbrev`, `eligible_slots`, `lineup_slot_category` (pitching/hitting/inactive), `games_played` (0/1/2 — DH support).
- `stg_player_stat_breakdowns` (view): per-stat rows from RAW; joins `stat_classification` for `stat_category`.
- `stg_scoring_settings` (view): latest snapshot from append-only RAW; per-stat `points_per_unit`.
- `stg_team_owners` (view): per-season team → owner bridge from `RAW.TEAM_OWNERS` (latest snapshot). `owner_id` is the stable ESPN member GUID the `owner_nicknames` seed joins on.
- `stg_draft` (view): one row per draft pick from `RAW.DRAFT_PICKS` (latest snapshot per season); `keeper` flagged.
- `stg_matchup_scores` (view): final ESPN team score per `(season, matchup, team)`, read from the wrapper's home/away score at the last scoring period (the field is cumulative). The matchup-grain sibling of `stg_box_scores`.
- `stg_matchup_pairs` (view): the who-played-whom spine per `(season, matchup, home_team, away_team)`, collected across every scoring period so a single-day capture glitch can't drop a pairing.
- `stg_roster_settings` (view): long-form reshape of the rosterSettings payload — one row per `(season, setting_type, espn_id)` covering both lineupSlotCounts and positionLimits.

Intermediate (`models/intermediate/`):

- `int_player_daily` (view): wide daily row per `(season, scoring_period, team, player, lineup_slot)`. Combines per-stat point contributions with per-player ESPN platform totals and player display metadata. Slot-stat-category-validity filtered (gated on `var('strict_slot_validity', true)`); slot-blind kona stats credited only when `stat_category` matches `lineup_slot_category`.

Marts — core (`models/marts/core/`, the contract layer):

- `dim_stat` / `dim_matchup_period` (views): thin contract dims over the `stat_classification` / `matchup_schedule` seeds; `dim_stat` adds `leaderboard_name`.
- `dim_roster_slot_counts` (view): roster-settings reshape — one row per configured lineup slot with starter counts and position maximums.
- `dim_owner` (view): owner-GUID-grain dimension; `owner_display` = nickname override else proper-cased name.
- `dim_team_owner` (view, born `int_team_owner_display`): `(season, team)` → `owner_display`, co-owned teams collapsed to "Name / Name". Four marts + the almanac join through it.
- `fct_player_position_pts` (table — frozen for float determinism; born `int_player_position_pts`): eligible-slots VARIANT explosion into per-`(season, matchup, team, player, position)` calculated points. The optimal-team selector's data contract.
- `fct_player_daily_performance` (view): thin daily contract over `int_player_daily`, adding `performance_status` + `wasted_bucket`.
- `fct_player_weekly_slot_performance` (table): slot-preserved weekly rollup (promoted from `int_player_weekly_performance` in v1.0.2). Wide counting + per-stat `_pts` + catch-all totals + `negative_points` + platform totals; `lineup_slot` kept in the grain so the fact layer can filter active/inactive cleanly.
- `fct_player_weekly_active_performance` (incremental): active-only filter (lineup_slot NOT IN BE/IL/FA). Wide convergence row per `(season_year, matchup_period, team_id, player_id)`.
- `fct_player_weekly_inactive_performance` (incremental): symmetric counterpart for inactive slots, with `wasted_bucket` ('FA' or 'ROSTERED_INACTIVE') in the grain.
- `fct_team_weekly_active_performance` (incremental): team-grain rollup of the player active fact. The team_total = SUM(players) invariant holds for all columns except `platform_points` (sourced directly from the wrapper's `home_score` to honor commissioner adjustments; divergence captured in `platform_calculated_delta`).
- `fct_team_weekly_inactive_performance` (table): team-grain rollup of the player inactive fact, plus a single FA pool row per matchup (team_id NULL).
- `fct_player_season_performance` (view): season-grain brick per `(season, team, player, slot)`; points columns rounded once per row for cross-session determinism.

Marts — reporting (`models/marts/reporting/`, consumer surfaces):

- `mart_stat_leaderboard` (view): seed-driven Jinja UNPIVOT over all four facts (team_active, team_inactive, player_active, player_inactive) union'd into one combined CTE. Ranked top-10 per `(entity_grain, performance_status, stat_name, record_scope, record_direction)`. `performance_status` partition segregates active/inactive rankings; consumers default-filter to active.
- `mart_league_weekly_benchmarks` (view): league-wide per-week mean/max/min on the calculated lens + percentile/rank within history.
- `mart_team_matchup` (view): each team-week plus opponent line, margin, combined totals, league averages.
- `mart_daily_roster_snapshot` (view): full roster shell per day (includes zero-stat bench/IL days); joins the slot dim + owner display.
- `mart_draft_board` (view): each draft pick joined to the player's season production — the draft-value surface.

The mart is direction-agnostic (`most`/`fewest`); polarity-aware Best/Worst label belongs at consumer side via `records_logic.best_or_worst_label`.

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
7. **Stat ID 30 = Hit for the Cycle (CYC)** (Phase 7 archaeology, promoted in v1.0.1): same scoring-weight pattern as PG/SHO. 15 pts/unit (rare-event tier with NH no-hitters), 2 observed rows across 2 seasons matching real cycle candidates. v1.0.1 promoted it to a tracked stat: wide `cyc` column on `int_player_daily`, `int_player_weekly_performance`, and all four facts; `is_record_candidate=true`; surfaced in the records report and via a `cycles` callout in `league_notes.py`.
8. **Stat ID 31 (formerly seed name `CYC`, now `STAT_31`)** is NOT cycles — labeled as such since seed import but disproven by Phase 7 archaeology: 148 non-zero rows across 113 players over 2 seasons (impossible for cycles; real MLB has ~5-10 per year league-wide) and no scoring weight. Some other ESPN daily-achievement flag (multi-hit game? extra-base-hit-game?). v1.0.1 renamed the seed row to `STAT_31` so the real cycle stat (id 30) could own the `CYC` leaderboard column; `stg_player_stat_breakdowns` filters wrapper-emitted `'CYC'` rows so the FK invariant holds.

### Scoring-settings + leaderboard naming

- The `stat_classification` seed uses ESPN's raw stat IDs/names: `'1B'`, `'2B'`, `'3B'`, `'30'`, `'64'`, `'B_IBB'`, `'HBP_P'`, etc.
- The leaderboard mart uses spelled-out column names: `'SINGLES'`, `'DOUBLES'`, `'TRIPLES'`, `'CYC'`, `'SHO'`, etc.
- `dim_stat.leaderboard_name` translates between them. The dbt mart reads
  this value in its Jinja seed loop, and Python reads it through
  `output/stat_catalog.py`. Add new seed-to-column translations in
  `dim_stat.sql`, not in parallel Python/dbt maps.

### Polarity conventions

- Polarity is stored as a column in `stat_classification` (`positive` | `negative` | `neutral`) and read at runtime via `stat_catalog.get_polarity_map()`. The seed value pre-merges what used to be runtime logic: `sign(points_per_unit)` from scoring settings for scored stats, plus hardcoded values for stats not in scoring settings (rates ERA/WHIP/BB9/HR9 → negative; K9/KBB → positive; WASTED_POINTS → negative; derived stats PA/SB-CS/W-L/SV-BLSV → positive; score columns → positive). To change polarity, edit the seed CSV directly and reseed.
- Stats with `polarity = 'neutral'` (or absent from the seed entirely) don't surface as records.
- `auto_tracked` seed flag: bypasses the polarity rule at team grain for stats tracked regardless of league scoring settings (distinct from stats tracked because they appear in scoring_settings). Currently flagged: H, TB, XBH, SF, ER, PA. Edit the seed to add/remove members; reseed (`dbt seed --full-refresh -s stat_classification`).

### `platform_*` vs `calculated_*`

This distinction is load-bearing — get it wrong and analytics will lie:
- **`platform_*`**: direct API passthrough, zero math. Player-level is slot-blind (kona's `appliedTotal`); team-level is wrapper's `home_score`/`away_score` (slot-aware AND inclusive of commissioner adjustments).
- **`calculated_*`**: our derivation under current-season scoring settings, with full slot-validity filter applied at `int_player_daily`.
- **The team_total = SUM(players) invariant has a documented exception**: for `platform_points` specifically, team-level is the wrapper's authoritative number, NOT the player rollup. The divergence (when slot misuse exists) is meaningful, not drift, and is exposed via `platform_calculated_delta`. All other counting/scoring columns hold the invariant.
- **Records flipped to `calculated_*` in Phase 5**: cross-season comparison is meaningful only under current weights. The recap section's best/worst team callouts and Top Hitter/Pitcher still source from `platform_*` because the recap is about what happened (W/L outcomes are platform-determined).

### Slot validity filter

`int_player_daily` filters `stat_category = lineup_slot_category` (or `'fielding'` or `'inactive'`), gated on `var('strict_slot_validity', true)`. Set the var to false to disable in case ESPN's behavior changes cross-platform. Inactive (BE/IL/FA) rows bypass the filter so wasted-points sees the full stat lines.

### `is_abnormal` matchup periods

`matchup_schedule.is_abnormal` flags weeks that should be excluded from records (e.g., All-Star break, weather-shortened weeks). Every leaderboard CTE filters on `is_abnormal = false`. Don't bypass.

### Playoff weeks

`matchup_schedule.is_playoff` and `matchup_schedule.playoff_round` (currently `'Round 1'` / `'Semi-Finals'` / `'Finals'`) drive `format_week_label()` substitution. As of handoff: 2025 MP24-26 are tagged playoff. 2026 playoffs not yet played.

### Connection management

`output/db.py::query_snowflake` is the shared Snowflake connection wrapper used by every output script. `db.init()` opens a single connection per script run; subsequent `query_snowflake()` calls reuse it. Cursor cleanup + error handling are encapsulated in the wrapper.

### Worktree convention

Each phase happens in its own worktree at `.claude/worktrees/<name>/` with its own branch, then merges back to main. Cleaned up post-merge via `git worktree remove`. Long-lived `phase-3.2` worktree exists for historical reasons; ignore unless you specifically need that branch.

---

## 8. Conventions and patterns

These are calls already made; lead with the established pattern rather than re-debating.

### dbt patterns

- **Wide convergence facts at consumer grain**. Counting + rate + `_pts` + scoring totals all in one wide row per (player|team)-week.
- **`fct_team_weekly_active_performance` reads from `fct_player_weekly_active_performance`**. Team totals are *defined* as `SUM(players)`, eliminating drift (with the `platform_points` exception noted above).
- **Macros for grain-agnostic formulas** (`macros/rate_stats.sql`).
- **Slot-agnostic intermediate, filter at fact**. `int_player_daily` and `int_player_weekly_performance` keep `lineup_slot` in the grain. Active filter (`NOT IN ('BE', 'IL', 'FA')`) at the active fact; inverse filter at the inactive fact.
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
- **`auto_tracked` seed flag** drives team-grain stats that surface as records regardless of polarity (stats tracked regardless of league scoring settings).
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

Verified end-to-end as of v1.1.0 ship:

- `dbt build` runs clean: PASS=140 / WARN=0 / ERROR=0 / NO-OP=4.
- `pytest` default suite is green: 148 passed / 15 deselected.
- `pytest tests/ -m warehouse` is green: 15 passed / 148 deselected,
  including golden BBCode regressions.
- `python output/generate_summary.py` produces the full BBCode recap
  with the Phase 7/v1.0 behavior intact.
- `python output/generate_records_report.py` produces the BBCode records
  report and can still write the legacy 3-tab records Sheet when
  `SHEETS_OUTPUT_ID` is set.
- `python output/generate_almanac_sheet.py` writes the v1.1 almanac
  workbook when `SHEETS_OUTPUT_ID` is set, and can generate TSV previews
  via `--no-sheets --preview-dir`.
- `tests/fixtures/almanac_v1_1_0/` is the golden TSV baseline for the
  planned v1.1.1 almanac refactor.

### Coverage

- **Snowflake state**: 2025 full season plus 2026 current-season data
  loaded for the maintainer's league.
- **Live Sheet**: v1.1 almanac output visually checked by the maintainer.
  The legacy records-sheet sink remains available but is no longer the
  forward-looking Sheets product.

---

## 10. Roadmap

Forward-looking work is tracked in `ROADMAP.md` at repo root. That doc
has the public Now / Next / Later / Decided Against buckets and stays
authoritative as items ship.

Highlights as of v1.1.0:
- **Now (v1.1.1)**: refactor the almanac without changing the generated
  workbook. Use `tests/fixtures/almanac_v1_1_0/` as the golden TSV
  baseline, move reusable SQL into dbt contracts, and split
  `output/almanac_sheets.py` into data/logic/render/write layers.
- **Next player-layer work**: start with pure performance facts sliced
  by season/team/player/slot. Acquisition, trades, free-agent signing,
  and league-note callout history should join in as separate models
  later rather than nullable columns on the performance fact.
- **Shipped in v1.1.0**: Google Sheets league almanac with Home,
  Records, Team Weeks, and one active-stats tab per team; roster-settings
  extraction; `dim_roster_slot_counts`; `mart_daily_roster_snapshot`;
  golden TSV fixture for refactor regression.
- **Shipped in v1.0.2** (refactor-only; byte-identical output): new
  mart-layer contracts (`dim_stat`, `dim_matchup_period`,
  `fct_player_daily_performance`); `int_player_weekly_performance`
  promoted to `fct_player_weekly_slot_performance`; schedule columns
  denormalized onto the four weekly facts; output scripts repointed
  through the new contract layer; `SEED_TO_LEADERBOARD` and
  `to_leaderboard_name` removed (`dim_stat.leaderboard_name` is the
  single source of truth); dbt exposures rewired; raw + duplicate-
  CASE-block edges eliminated from the catalog DAG.
- **Shipped in v1.0.1**: stat-catalog cleanup (`auto_tracked` rename,
  NEGATIVE_POINTS as record candidate, stat 30 = Hit for the Cycle
  promotion); recap polish (fact-layer rounding, conditional Top
  Scorer line, "none yet" rendering); DI cleanup
  (`count_value_occurrences` injected via `count_fn`,
  `records_logic` import-pure); league-wide benchmarks mart +
  always-on `League This Week:` recap line + hot/cold-week callouts;
  Snowflake key-pair auth; 8 new league_notes callouts.
- **Open v1.x items**: Owner Names in the Mart, Career Stats Per Team,
  Playoff Contention Identification, Calendar Auto-Populate. Sheets
  Sink Hardening dropped for the legacy sink; future formatting work
  belongs in the almanac writer.
- **Next (v2.0, likely-exclusive)**: cross-platform Yahoo/Sleeper extract
  OR DuckDB target. MetricFlow as a deliberate learning exercise.
- **Decided Against**: frequency-table tab (tie-collapse covers it),
  player-grain rate stats at mart layer (Phase 6.3.3 Path A choice).

For the open architectural questions surfaced during Phase 7 review --
inactive-fact grain edge case, PLATFORM_* in SCORE_STAT_NAMES product
call -- see `Phase 7 Documentation.md` § "Open Investigations Carried
Forward".

---

## 11. The user's continued operational dependence

The user runs this every week. Behaviors they specifically rely on — break these and you'll hear about it:

- **Section ordering in `generate_summary.py`** (locked Phase 5; documented in §6 above). Don't reorder without coordination.
- **Header conventions**: `[u][b]Section Name[/b][/u]` for sections, `[b]Label[/b]: value` for callouts.
- **Player-card shape**: `Player (TeamAbbr), X.X pts -- {stats}` — locked Phase 5.
- **Records show owner names**, recap doesn't — locked Phase 5.
- **`Matchup #N` was renamed to `Week N` everywhere** — locked Phase 5.
- **Playoff weeks render as round names** (`Round 1` / `Semi-Finals` / `Finals`) instead of `Week 24-26` — locked Phase 6.3.3 chunk 6.
- **Legacy records sheet schema is 17 cols across 3 tabs** — locked Phase 6.3.3 chunk 5 for `sheets_writer.py`. The v1.1 almanac is the forward-looking Sheets surface and has its own tab-specific schemas.
- **`SHEETS_OUTPUT_ID` env-var opt-in** for both Sheets sinks — don't accidentally make either sink opt-out.
- **`output/LeagueNote.txt` is appended verbatim** under "Additional Notes" when non-empty. The user uses this for ad-hoc commissioner messages each week.

If you change a public-facing behavior, surface it before merging. The user is the most important QA on this project.

### What the user does NOT do

- They don't run dbt against prod. There's no prod environment — `dev` target IS the operational target. The "test" lives in the verification you run before shipping.
- They primarily consume the recap output, not the dbt docs catalog. Model documentation lives in `schema.yml` files (with descriptions on every model and most columns post-Phase 7) and the phase docs; the dbt catalog is the formal browsable surface but the user rarely opens it.
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

- **Read** `Phase 7 Documentation.md` (the v1.0 retrospective; most recent and most relevant), then `Phase 5.0 Documentation.md` (well-shaped doc covering the recap structure), then `Phase 4.0 Documentation.md` (for the wasted-points concept). ~2 hours.
- **Set up** the venv + `.env` + dbt profile per `SETUP.md`. ~30-45 minutes.
- **Run** the weekly workflow against existing data: `dbt build` + `python output/generate_summary.py | head -50`. Confirm output renders. ~10 minutes.
- **Pick** one item from `ROADMAP.md` Now (v1.x). The `dim_player` + `fct_player_career` flagship is the highest-leverage starting point.
- **Don't** touch section ordering, header conventions, the 17-column Sheets schema, or push to origin without coordination.

The project is in a healthy place. Phase 7 shipped a major rearchitect (active/inactive symmetric facts, seed-driven catalog, 3-way records split) plus the v1.0 public documentation surface, verified clean against golden BBCode baselines. v1.x work is incremental polish on this foundation.

Welcome.
