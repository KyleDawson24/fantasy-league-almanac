# Phase 2.1 Handoff — ESPN Fantasy Baseball Front Page Generator

## What Changed Since Phase 2.0

Phase 2.1 is a structural refactor and feature pass. The two mart consolidation resolves the cross-layer dependency flagged in the 2.0 documentation, the player points model was reworked to handle two-way players correctly at the weekly grain, owner names were added end-to-end, and several output improvements landed.

### Summary Output (Current)
```
[u][b]Matchup #3 Recap[/b][/u]
[b]Best Overall[/b]: 354.9 pts by Intentional Walk to the Bar (Dorian)
Shohei Ohtani: 37.7, JJ Wetherholt: 31.0, Riley O'Brien: 27.9, ...
[b]Best Hitting[/b]: 212.1 pts by Clase Action Lawsuit (Grant)
Jose Ramirez: 44.7, Brayan Rocchio: 27.0, Trent Grisham: 26.1
[b]Best Pitching[/b]: 195.5 pts by Intentional Walk to the Bar (Dorian)
Riley O'Brien: 27.9, Parker Messick: 26.1, Shota Imanaga: 24.3
[b]Worst Overall[/b]: 209.8 pts by Atomic Alpaca Armada (Adrian)
[b]Worst Hitting[/b]: 110.5 pts by Ghosts of Polo Grounds Past (Blake)
[b]Worst Pitching[/b]: 54.0 pts by Atomic Alpaca Armada (Adrian)
[b]Tough Luck[/b]: (conditional)
[b]Lucky Bastard[/b]: (conditional)
[b]A FAIR AND JUST LEAGUE![/b]: (conditional)

[u][b]Current Season Records[/b][/u]
Best/Worst Matchup Total, Hitting, Pitching with owner names

[u][b]All-Time League Records[/b][/u]
Best/Worst Matchup Total, Hitting, Pitching with owner names

*Footnotes*
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
│   ├── generate_summary.py       # Reads marts, prints BBCode summary
│   └── logs/                     # Timestamped .txt output files (gitignored)
└── dbt_league/
    ├── dbt_project.yml           # Includes explicit seed column types
    ├── packages.yml              # dbt_utils dependency
    ├── seeds/
    │   ├── matchup_schedule.csv      # 2025 + 2026 seasons, is_abnormal/is_playoff/playoff_round
    │   ├── stat_classification.csv   # 73 stats → hitting/pitching/fielding/unknown
    │   ├── player_nicknames.csv      # ESPN player_id → display nickname
    │   └── owner_nicknames.csv       # ESPN owner GUID → preferred display name (scaffolded, not yet wired in)
    ├── models/
    │   ├── staging/
    │   │   ├── sources.yml           # Declares RAW.BOX_SCORES as a dbt source
    │   │   ├── schema.yml            # Tests + full column descriptions
    │   │   └── stg_box_scores.sql    # Flatten JSON → one row per player per team per scoring period
    │   ├── intermediate/
    │   │   ├── schema.yml            # Model + column descriptions
    │   │   ├── int_team_daily_scores.sql   # Team-level daily totals with hitting/pitching splits
    │   │   └── int_player_daily_scores.sql # Player-level daily scores, active slots only
    │   └── marts/
    │       ├── schema.yml                  # Model + column descriptions, not_null tests on mart totals
    │       ├── fct_weekly_team_scores.sql  # Weekly team totals + opponent context + W/L
    │       └── fct_weekly_player_scores.sql # Weekly player totals with hitting/pitching/total split
    └── profiles.yml → lives at C:\Users\kyled\.dbt\profiles.yml (not in repo)
```

Note: `int_weekly_matchups.sql` was deleted. Its logic now lives inside `fct_weekly_team_scores.sql`.

---

## What Was Built in Phase 2.1

### Structural Refactors

**Mart consolidation** — `fct_weekly_team_scores` and `int_weekly_matchups` (formerly `fct_weekly_matchups`) were merged into a single mart. `fct_weekly_team_scores` now contains opponent name, opponent score, and W/L result alongside the team's own scoring totals. The consolidated model sources its weekly score rollup from `int_team_daily_scores` and its matchup pairings from the raw source, eliminating the cross-layer dependency where an intermediate was reading from a mart.

**Why consolidate:** Both models served the same grain (one row per team per matchup period), the same consumers (the output script), and the same update cadence. The separate matchup model existed only because Phase 1 built scores first and Phase 2 added opponent context as a separate model. There was no principled reason to keep them apart — opponent info is an attribute of a team's weekly score, not a separate entity.

**`player_type` removed from mart grain** — `fct_weekly_player_scores` previously had `player_type` in the GROUP BY, producing separate rows for hitting and pitching contributions. This meant two-way players like Ohtani appeared as two rows per week, requiring downstream aggregation to get a true total. The mart now produces one row per player per team per matchup period, with `hitting_points`, `pitching_points`, and `total_points` as separate columns. `player_type` is consumed inside the mart's CASE WHEN logic and does not appear as a column in the output.

**Why this matters:** `player_type` described a false dichotomy — it classified the *player*, but what matters is whether the *points* came from hitting or pitching. The column-level split (matching the team mart's convention of `hitting_points` / `pitching_points` / `total_points`) is both more correct and more queryable. `total_points` now means the same thing across every table in the project.

**`int_team_daily_scores` hitting/pitching classification fix** — Changed from `position IN ('SP', 'RP')` to `lineup_slot IN ('SP', 'RP', 'P')` to match the classification logic in `int_player_daily_scores`. This corrects the Ohtani edge case at the team level (previously his SP-slot pitching days were bucketed as hitting because his `position` was always DH) and adds support for the flex `P` lineup slot used in some leagues.

### Feature Additions

**Owner names** — The extraction script now captures owner name from the ESPN API's `team.owners` attribute on each box score matchup. Stored as `home_owner` / `away_owner` in the raw JSON, then threaded through staging (`owner_name`), both intermediates, and both marts. For co-managed teams, owner name is a slash-delimited list of first names. For single-owner teams, it's title-cased first and last name. The output script displays team names as "Team Name (Owner Name)" in the superlatives and records sections.

**Nickname resolution in the mart** — The `COALESCE(nickname, player_name)` join to `player_nicknames` was moved from the output script into `fct_weekly_player_scores` as a `display_name` column. This puts the nickname resolution in the dbt DAG where it's testable and documented, and simplifies the output script to plain SELECTs with no joins.

**`.txt` log output** — `generate_summary.py` writes each summary to a timestamped file in `output/logs/` in addition to printing to console. Filenames include matchup period and timestamp for easy reference. The logs directory is gitignored.

**`P` lineup slot support** — Added to pitcher classification in both intermediate models. This league doesn't use it, but many ESPN leagues have a flex P slot that can hold either SP or RP.

### New Seeds

**`owner_nicknames.csv`** — Scaffolded with all 14 current owner GUIDs, first names, and last names. `preferred_name` column is empty for all owners — when populated, the intended logic is `COALESCE(preferred_name, first_name || ' ' || last_name)`, matching the player nicknames pattern. Not yet wired into any model.

---

## Key Technical Decisions

### 1. Parallel intermediate paths, not serial
`int_team_daily_scores` and `int_player_daily_scores` both read from `stg_box_scores` independently rather than the team model being derived from the player model. This means the active-slot filter and hitting/pitching classification exist in two places — a minor DRY violation. The tradeoff: independence. dbt can run them concurrently, a change to player-level logic cannot accidentally break team-level outputs, and each model is readable without understanding the other. The duplication is of *code*, not of *knowledge* — both models make the same classification decision for the same reason, they just serve different downstream grains.

### 2. Matchup pairings as a CTE, not a staging model
The home/away matchup pairings are extracted from raw JSON inside `fct_weekly_team_scores` as a CTE rather than a separate staging model. This keeps the logic contained — the pairings are only consumed by one model. If a second consumer ever needs matchup pairings (e.g., a schedule analysis mart), that would be the signal to promote the CTE to `stg_matchup_pairings`.

### 3. Owner name captured in extraction, not a seed
Owner names come from the ESPN API at extraction time rather than being manually maintained in a CSV. This is self-maintaining — it always reflects the current ESPN data. The limitation: ESPN likely returns the *current* owner for all historical periods, not the owner at the time of the game. If a team changes hands mid-season, historical data would show the new owner retroactively. Acceptable for this use case; would need point-in-time snapshots for a more rigorous dimensional model.

### 4. `total_points` as a universal column name
`total_points` means "true total of all points" in every table across the project — team marts, player marts, intermediates. At the player level, `hitting_points` and `pitching_points` provide the category breakdown, and `total_points` is always the sum. This convention means any analyst querying any table gets the expected answer from `total_points` without needing to know about two-way players or typed rows.

---

## What's in Snowflake (Current)

- **Database**: `ESPN_FANTASY`
- **Raw schema**: `RAW` — `BOX_SCORES` (includes `season_year` and owner fields in JSON)
- **Analytics schema**: `ANALYTICS` — dbt-built models:
  - `STG_BOX_SCORES` (view)
  - `INT_TEAM_DAILY_SCORES` (view)
  - `INT_PLAYER_DAILY_SCORES` (view)
  - `FCT_WEEKLY_TEAM_SCORES` (table) — now includes opponent context and W/L
  - `FCT_WEEKLY_PLAYER_SCORES` (table) — now one row per player with hitting/pitching/total split
  - `MATCHUP_SCHEDULE` (seed table)
  - `STAT_CLASSIFICATION` (seed table)
  - `PLAYER_NICKNAMES` (seed table)
  - `OWNER_NICKNAMES` (seed table) — scaffolded, not yet joined in models

### Data Loaded
- **2025**: All 26 matchup periods (regular season + 3 playoff rounds), re-extracted with owner fields
- **2026**: Matchup periods 1–3 (through April 19, 2026), re-extracted with owner fields

---

## Bookmarks for Future Work

### Near-term (Phase 3 candidates)
- **"Record set this week" callout** — Compare current matchup scores against existing records; call out new bests/worsts in the recap section. The records query infrastructure already exists in `get_records` / `format_records`.
- **Wire `owner_nicknames` seed into models** — Same COALESCE pattern as player nicknames. Requires adding owner_id to extraction and threading through staging/marts.
- **`fct_team_career_stats` mart** — All-time wins, losses, GOTWs (game of the week count), best/worst single-week scores, average weekly score. New grain (one row per team or per team per season), built from `fct_weekly_team_scores`. No new extraction needed.
- **Break `generate_summary.py` into two functions** — Weekly recap vs. records tracker. Different responsibilities, different query patterns. Deferred from 2.1 because the script is stable and readable as-is.

### Medium-term
- **`stg_matchup_pairings`** — Promote the matchup pairings CTE from `fct_weekly_team_scores` to a standalone staging model if a second consumer needs it.
- **`dim_teams`** — Standalone team dimension table extracted from `league.teams` with team_id, team_name, owner GUID, division, logo_url, etc. Currently unnecessary since owner name is the only team-level attribute surfaced, but becomes worthwhile if more team attributes are needed.
- **Stat-level classification in intermediate models** — Replace position-based CASE WHEN with a join to `stat_classification` seed. Required for Phase 3 stat-level records (e.g., most home runs in a week).

### Long-term
- **`fct_weekly_player_slots` mart** — One row per player per slot per team per matchup period. Enables bench productivity analysis and Phase 4 wasted points. `lineup_slot` is preserved in `int_player_daily_scores` for this purpose.
- **Phase 4: Wasted points** — Top scorers while benched or unrostered. Requires testing `league.free_agents()` API and potentially per-period free agent extraction.
- **Historical scoring normalization** — "What would a 2025 matchup have scored under 2026 settings?" Requires applying current scoring weights to historical raw stat breakdowns from the `breakdown` column in staging.

---

## Git History (Commits through Phase 2.1)
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
