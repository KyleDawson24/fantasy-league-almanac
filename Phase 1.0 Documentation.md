# Phase 1 Handoff — ESPN Fantasy Baseball Front Page Generator

## What Was Built

A working end-to-end pipeline that extracts ESPN Fantasy Baseball box score data, transforms it through a dbt pipeline in Snowflake, and generates a BBCode-formatted (or rather ESPN's even weaker version of BBCode) weekly summary of league highs and lows.

### Pipeline Flow
```
ESPN Fantasy API (espn-api wrapper)
    → Python extraction script
    → Snowflake (ESPN_FANTASY.RAW.BOX_SCORES)
    → dbt project (staging → intermediate → mart)
    → Python output script
    → BBCode text for ESPN league front page
```

### Phase 1 Output
The summary includes:
- Best/worst overall score (team + points)
- Best/worst hitting (hitter points by team)
- Best/worst pitching (pitcher points by team)
- **Tough Luck**: Only fires if the #2 overall scorer lost their matchup
- **Lucky Bastard**: Only fires if the #2 _lowest_ scorer won their matchup
- **Fair and Just League**: Fires if the top half of the scoring teams won and all bottom half lost (rare)

---

## Project Structure

```
espn-league-manager/
├── .env                          # Secrets (gitignored)
├── .env.example                  # Template showing required env vars
├── .gitignore
├── requirements.txt              # Python 3.13, pinned dependencies
├── config/
│   └── matchup_schedule.json     # Matchup-to-date mapping (2026 season)
├── extract/
│   └── extract_box_scores.py     # ESPN API → Snowflake raw JSON
├── output/
│   └── generate_summary.py       # Reads mart, prints BBCode summary
└── dbt_league/
    ├── dbt_project.yml
    ├── seeds/
    │   └── stat_classification.csv   # 73 stats classified into hitting/pitching/fielding/unknown
    ├── models/
    │   ├── staging/
    │   │   ├── sources.yml           # Declares RAW.BOX_SCORES as a dbt source
    │   │   └── stg_box_scores.sql    # Flatten JSON → one row per player per scoring period
    │   ├── intermediate/
    │   │   ├── int_team_daily_scores.sql   # Team-level daily totals with hitting/pitching splits
    │   │   └── int_weekly_matchups.sql     # Matchup pairings with opponent scores and W/L
    │   └── marts/
    │       └── fct_weekly_team_scores.sql  # Weekly team totals — the table the output script queries
    └── profiles.yml → lives at C:\Users\kyled\.dbt\profiles.yml (not in repo)
```

---

## Key Technical Decisions

### 1. Both `matchup_period` AND `scoring_period` required for historical player stats
The ESPN API's `box_scores()` method returns correct matchup-level scores with just `scoring_period`, but player-level stats always reflect "today" (or most recent completed scoring period, uncertain) unless both parameters are passed. Discovered through debugging — the `player.stats` dict key was always the newest scoring period regardless of what was requested until we included both arguments.

### 2. External matchup schedule (JSON config, will update to dbt seed in phase 2)
Matchup periods have variable lengths (opening week = 12 days, All-Star break = 14 days, normal weeks = 7 days). The schedule lives in `config/matchup_schedule.json` so a non-technical user can edit it without touching Python code. **Earmarked for refactor**: move this to a dbt seed with a `season_year` column (multi-season support) and an `is_abnormal` flag (for filtering records comparisons; all star break and opening weeks are frequently extended and therefore produce way higher counting stats).

### 3. ELT pattern — raw JSON stored as Snowflake VARIANT
The extraction script dumps the full API response as JSON into a `VARIANT` column. All flattening and transformation happens in dbt. This preserves raw data for re-transformation and keeps the extraction script simple, and lays some foundation for extending this to other fantasy apps in the future)

### 4. Stat classification via dbt seed, not hardcoded CASE WHEN
A CSV seed (`stat_classification.csv`) maps every stat abbreviation to a category (hitting, pitching, fielding, unknown). Currently the intermediate model still uses position-based classification (`SP`/`RP` = pitching, everything else = hitting) for the team-level splits. The seed is in place for Phase 3 (league records) where stat-level classification is required.

### 5. Staging model at player-level grain
`stg_box_scores` flattens to one row per player per scoring period, not per team. This is finer than Phase 1 strictly needs, but it means Phase 2 (player contributions) reads from the same staging model without re-flattening from raw.

### 6. Python 3.13 (not 3.14)
dbt's dependency chain (specifically `mashumaro`) is incompatible with Python 3.14. The virtual environment uses 3.13, which is stable and fully supported as of 4/19/2026 deployment

### 7. Snowflake role = ACCOUNTADMIN
The free-tier Snowflake setup required `ACCOUNTADMIN` in `profiles.yml` due to permissions. Acceptable for a personal project; would be scoped to a dedicated role in production.

### 8. Idempotent, auto-detecting extraction
The extraction script automatically identifies which matchup periods to pull based on a 21-day lookback window from today's date. It deletes existing data for a matchup period before reinserting, making re-runs safe. Specific periods can also be targeted via command-line arguments: `py extract/extract_box_scores.py 1 2 3`. This eliminates manual editing of the script between runs.

---

## What's in Snowflake

- **Database**: `ESPN_FANTASY`
- **Raw schema**: `RAW` — contains `BOX_SCORES` (written by extraction script)
- **Analytics schema**: `ANALYTICS` — contains dbt-built models:
  - `STG_BOX_SCORES` (view)
  - `INT_TEAM_DAILY_SCORES` (view)
  - `INT_WEEKLY_MATCHUPS` (view)
  - `FCT_WEEKLY_TEAM_SCORES` (table)
  - `STAT_CLASSIFICATION` (seed table)

### Current data loaded
- Matchup period 1 (scoring periods 1–12, March 25–April 5, 2026) — 12-day opening week
- Matchup period 2 (scoring periods 13–19, April 6–12, 2026)
- Matchup period 3 (scoring periods 20–26, April 13–19, 2026) — in progress at time of extraction
- 2025 season: confirmed accessible via `year=2025` parameter, not yet back-filled

---

## Open Items & Bookmarks for Future Phases

### Phase 2 — Player Contributions (Next)
- Add `int_player_daily_scores` and `fct_weekly_player_contributions` models
- Top 5 overall scorers from the highest-scoring team
- Top 3 hitters from best offense, top 3 pitchers from best pitching
- Data is already available in `stg_box_scores` — no new extraction needed

### Phase 3 — League Records
- Incremental dbt model comparing weekly stat totals against all-time records
- Requires stat-level breakdowns from `stg_box_scores.breakdown` column
- Stat classification seed is already in place
- Needs the `is_abnormal` flag on matchup schedule to exclude All-Star break from records
- Needs historical data: back-fill 2026 matchup periods 1 and 3+, test 2025 season access

### Phase 4 — Wasted Points
- **Open risk**: Does `league.free_agents()` return pre-computed `points`? If not, we need to apply scoring weights ourselves from raw stats — significantly harder.
- **Point-in-time status**: Need to capture whether a player was a free agent *at the time they produced stats*, not just currently. This may require extracting FA data per scoring period alongside box scores.

### Refactors Earmarked
- **Matchup schedule → dbt seed**: Replace `config/matchup_schedule.json` with a CSV seed that includes `season_year` and `is_abnormal` columns. Both the extraction script and dbt models would reference the same source of truth.
- **Ohtani edge case**: Current hitting/pitching split uses position (`SP`/`RP` = pitching). Two-way players may need stat-level classification instead. Monitor whether ESPN's `position` field correctly reflects the role for each day's stats.
- **Stat-based classification in intermediate models**: Replace position-based `CASE WHEN` with a join to `stat_classification` seed. Required for Phase 3, optional improvement for Phase 1/2.
- **Unknown stat IDs**: ESPN stat codes `22`, `61`, `64`, `78`, `79`, `80` are unresolved. Preserved in the seed as `unknown` category.
- **Multi-season extraction**: `YEAR` is currently hardcoded in the extraction script. Needs to become a command-line argument or config value, paired with the matchup schedule seed refactor so each season has its own schedule.

---

## Environment Setup (for new machines or collaborators)

```bash
# Clone
git clone https://github.com/KyleDawson24/fantasy-league-front-page.git
cd fantasy-league-front-page

# Python environment (requires Python 3.13)
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Secrets — copy template and fill in real values
cp .env.example .env
# Edit .env with ESPN cookies (espn_s2, SWID) and Snowflake credentials

# dbt profile — create C:\Users\<you>\.dbt\profiles.yml with:
# dbt_league:
#   outputs:
#     dev:
#       type: snowflake
#       account: <account.region>
#       user: <username>
#       password: <password>
#       role: ACCOUNTADMIN
#       database: ESPN_FANTASY
#       schema: ANALYTICS
#       warehouse: COMPUTE_WH
#   target: dev

# Verify dbt connection
cd dbt_league
dbt debug

# Run pipeline
cd ..
py extract/extract_box_scores.py     # Extract recent matchup periods (or specify: eg py extract/extract_box_scores.py 1 2 3)
cd dbt_league
dbt seed                             # Load stat classification
dbt run                              # Build models
cd ..
py output/generate_summary.py        # Generate summary
```

---

## Git History (Commits)
1. Initial commit: add .gitignore
2. Add project scaffold: gitignore, requirements, env template
3. Add extraction script and matchup schedule config
4. Fix extraction: pass both matchup_period and scoring_period for historical player stats
5. Complete Phase 1: dbt pipeline, stat classification seed, weekly summary output
6. Add Phase 1 handoff document
7. Idempotent extraction with auto-detection of recent matchup periods
