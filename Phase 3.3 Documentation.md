# Phase 3.3 Handoff — ESPN Fantasy Baseball Front Page Generator

## What Changed Since Phase 3.2

Phase 3.3 fixes a class of silent data loss in the espn-api wrapper: on doubleheader days, the wrapper's `box_scores()` method silently overwrites the first game's stats with the second game's stats for affected players, costing roughly 3-5 fpts per affected hitter every time a team plays a DH (~10-15 times per team per season).

The fix is a hybrid extraction architecture: keep the wrapper for matchup structure (lineups, slots, owners, team identifiers), but on DH days replace per-player stats with summed values pulled directly from ESPN's raw mRoster endpoint, which preserves all per-game splits.

**Concrete case validated end-to-end**: The Hosstros' MP1 2026 score was 370.65 in our pipeline vs 374.25 on the website — a 3.6 fpts gap. Root cause traced to MIL's split-doubleheader against KC on April 4 2026 (scoring period 11), made up from an April 3 rainout. Brice Turang lost a complete game line (4 AB, 1 H/3B, 1 R, 1 RBI, 2 K = +3.4 fpts) and Sal Frelick lost a partial (+0.2 fpts) to the wrapper's overwrite bug. Post-fix, Hosstros' platform_points and calculated_points both equal 374.25 exactly.

This also retroactively explains and resolves both Open Investigations from the Phase 3.2 doc.

---

## Project Structure (Current)

```
espn-league-manager/
├── extract/
│   ├── extract.py                    # MODIFIED: DH detection, mRoster override, --all flag, games_played
│   └── dump_stats_map.py             # unchanged
├── output/
│   ├── generate_summary.py           # unchanged (verified — no schema break)
│   ├── generate_records_report.py    # unchanged (verified)
│   ├── LeagueNote.txt                # gitignored
│   └── logs/
└── dbt_league/
    ├── dbt_project.yml               # unchanged
    ├── macros/
    │   └── rate_stats.sql
    ├── seeds/
    │   ├── stat_classification.csv
    │   ├── matchup_schedule.csv
    │   ├── player_nicknames.csv
    │   └── owner_nicknames.csv
    └── models/
        ├── staging/
        │   ├── sources.yml
        │   ├── stg_box_scores.sql                # MODIFIED: surfaces games_played
        │   ├── stg_player_stat_breakdowns.sql
        │   ├── stg_scoring_settings.sql
        │   └── schema.yml                        # MODIFIED: games_played column + accepted_values test
        ├── intermediate/
        │   ├── int_team_daily_scores.sql
        │   ├── int_player_daily_scores.sql
        │   ├── int_player_daily_stats.sql
        │   └── int_player_weekly_performance.sql
        └── marts/
            ├── fct_weekly_player_scores.sql
            ├── fct_weekly_player_performance.sql
            ├── fct_weekly_team_performance.sql
            └── mart_stat_leaderboard.sql
```

The Phase 3.2 leftover `extract/extract_box_scores.py` (which Phase 3.2 marked for deletion on merge but didn't actually delete) was removed in this phase.

---

## What Was Built in Phase 3.3

### Bug class — root cause

The espn-api wrapper's `box_scores()` builds a Python dict keyed by `scoringPeriodId`. On a doubleheader day, ESPN's API returns **two** stat splits with the same `scoringPeriodId` (one per game). The wrapper iterates and assigns to the dict — second write silently overwrites the first. Whichever game ESPN's response listed first is gone. Classic last-write-wins dict collision.

The user verified this by directly hitting ESPN's raw API and observing both splits for SP=11 with `statSplitTypeId=5`; the wrapper returns only one of them. There is no flag, parameter, or alternative wrapper method that preserves per-game granularity for baseball — `box_scores()` and `free_agents()` are the only data methods on the baseball `League` class.

### Architecture — hybrid extraction

**Detection**: Before serializing each scoring period, hit ESPN's *public* MLB scoreboard endpoint (`site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates=YYYYMMDD`) and look for events with `notes[].headline` containing "Doubleheader". Any team in a flagged event has a DH that day. No auth required — fast and cheap.

**Override**: If any DH teams exist for this scoring period, hit ESPN's authenticated mRoster endpoint (`lm-api-reads.fantasy.espn.com/.../leagues/{LEAGUE_ID}?view=mRoster&scoringPeriodId={SP}`). Iterate every rostered player's `stats[]` array, filter to `(statSplitTypeId == 5 AND scoringPeriodId == target)`, and sum stat values + `appliedTotal` across the (1 or 2) splits. Replace wrapper-derived `breakdown` and `points` only for players whose `proTeam` matches a DH team. All other players use wrapper data unchanged.

**Cost**: Zero added latency on non-DH days. On DH days, two extra HTTP calls per scoring period (one public scoreboard, one authenticated mRoster). Typical season has DHs on ~30-40 calendar days across the league — call it 1-3% of scoring periods.

### Code changes in `extract/extract.py`

**New helpers**:
- `_STAT_ID_TO_NAME` — module-level dict discovered from `espn_api.baseball.constant` (the attribute name varies across wrapper versions, so we scan for the largest dict). Maps numeric stat IDs to wrapper-style stat name strings.
- `scoring_period_to_date(sp, season_opener)` — inverse of the existing `date_to_scoring_period`.
- `get_doubleheader_pro_teams(d)` — returns set of uppercase MLB team abbreviations playing a DH on date `d`. Generic — no fantasy-team coupling, can be reused by Phase 4.
- `fetch_raw_player_stats(year, scoring_period)` — returns `dict[player_id] -> {breakdown, points, games_played}` summed across per-game splits. Scoped to ROSTERED players only (mRoster constraint).

**Modified**:
- `serialize_box_scores` — now takes `season_opener`, runs DH detection, conditionally fetches mRoster overrides, applies override per-player based on `proTeam` match. Adds `games_played` field (0/1/2) to every player dict.
- `extract_matchup_period` — passes `season_opener` through to `serialize_box_scores`.
- CLI: new `--all` flag that extracts every *completed* matchup period in the year's schedule (`end_date <= today`). Overrides the recent-only default and the positional periods list. In-progress and future periods are skipped — the API has no stable data for them.

**Removed**:
- `extract/extract_box_scores.py` — superseded by `extract.py` in Phase 3.2.

### Code changes in dbt

`stg_box_scores.sql`:
- Surfaces `games_played` as a column. New extractions write it directly per-player.
- For raw rows extracted before Phase 3.3 (no `games_played` field in JSON), COALESCE to `1` if breakdown is non-empty, else `0` — matching the semantics the wrapper produced before we knew about the bug.

`schema.yml` (staging):
- `games_played` documented.
- `not_null` test.
- `accepted_values: [0, 1, 2]` test — guards against future regressions in either direction.

No changes to intermediate, fact, or output layers. The grain is unchanged (one row per player per scoring period). DH days now carry summed stats under a single lineup_slot, which is semantically correct for fantasy scoring (ESPN awards points per scoring period, not per game).

---

## Key Technical Decisions

### 1. Hybrid extraction, not full bypass

We could have replaced `box_scores()` entirely with raw API calls. We didn't — the wrapper does a lot of useful matchup-structure work (home/away pairing, lineup_slot assignment, owner resolution, team_id resolution) that we'd otherwise re-implement. The bug is narrowly in stat collation, not in matchup structure. Hybrid keeps the parts that work and replaces only the parts that don't.

### 2. Detection via public MLB scoreboard, not via the schedule seed or the wrapper

The matchup_schedule seed is fantasy-week granularity; it doesn't know about MLB schedule changes (rainouts, makeups). The wrapper exposes no MLB schedule lookup. ESPN's public MLB scoreboard does, requires no auth, and surfaces DH games via `notes[].headline = "Doubleheader - Game N"`. Cheap, robust, no dependency on third-party MLB Stats API.

### 3. Override per-player by proTeam match, not blanket per scoring period

The mRoster response contains every rostered player league-wide (~250-350 players). We only override the subset whose `proTeam` is in the DH-team set (~15-50 players in practice). Minimizes the blast radius — non-DH-affected players keep wrapper data identical to before this phase, so any latent wrapper-vs-raw inconsistencies for ordinary games can't sneak in.

### 4. `games_played` as a 0/1/2 semantic, not a boolean DH flag

The natural temptation was a `had_doubleheader` boolean. The richer `games_played` count carries strictly more information (a player can have 0 appearances if benched both halves, or 1 if started one game), and is reusable for forensic queries beyond DH detection. The `accepted_values: [0, 1, 2]` test is a tighter contract than `is_boolean` and surfaces silently-wrong data immediately.

### 5. `scoring_period_to_date` as a separate helper, not inlined

Phase 3.2 added `date_to_scoring_period`. The inverse direction is now needed for DH detection (need calendar date to query MLB scoreboard). Kept symmetric and explicit rather than scattering `season_opener + timedelta(days=sp - 1)` inline.

### 6. `--all` filters to completed periods, not literal "all"

`--year 2026 --all` on April 30 2026 returns matchup periods 1-4, not 1-22. In-progress and future periods have no stable data; extracting them produces empty-rows-overwriting-empty-rows churn. Filtering matches the "useful" interpretation of the flag rather than the literal one.

### 7. DH log fires after the loop with actual override count

The first version of this code logged `len(raw_player_stats)` (~250-350 = entire mRoster response size) at fetch time. Misleading — it implied we were "applying override to 250 players" when in reality only ~15-50 were affected. Moved the log to after the player loop and counted actual overrides applied.

---

## What's in Snowflake (Current)

- **Database**: `ESPN_FANTASY`
- **Raw schema**: `RAW`
  - `BOX_SCORES` — same shape; new rows now include `games_played` per player in the JSON. Pre-Phase-3.3 rows lack the field; staging COALESCEs to a sensible default.
  - `SCORING_SETTINGS`
- **Analytics schema**: `ANALYTICS`
  - `STG_BOX_SCORES` — modified: new `games_played` column.
  - All other staging/intermediate/fact models unchanged structurally.
  - Both 2025 and 2026 reprocessed end-to-end via `--year YYYY --all` extraction + `dbt build --full-refresh`. Every incremental fact rebuilt from scratch with the corrected stat sums.

---

## Verification

Three tests run before close-out, all green:

1. `dbt test --select stg_box_scores` — `accepted_values: [0, 1, 2]` and `not_null` on `games_played` both passed across the full backfill (2025 + 2026).
2. 2025 DH spot-check — picked a known historical doubleheader, queried `stg_box_scores` for the affected pro_teams on that scoring period, confirmed `games_played = 2` rows for the expected hitters/pitchers.
3. Output scripts — `generate_summary.py` and `generate_records_report.py` run cleanly against the rebuilt facts. No schema regression.

Final verification query:
```sql
SELECT platform_points, calculated_points
FROM ESPN_FANTASY.ANALYTICS.FCT_WEEKLY_TEAM_PERFORMANCE
WHERE season_year = 2026 AND matchup_period = 1
  AND team_name = 'The Hosston Hosstros';
-- → 374.25, 374.25  (was 370.65, 370.65 pre-fix)
```

---

## Open Investigations Resolved from Phase 3.2

### 1. "QA query (370.65) ≠ fact (calculated_points ~377.55) for Hosstros MP1" — RESOLVED as misdiagnosis

The 377.55 reading was a transient or misread; current state cleanly has platform = calculated = 370.65 pre-fix and 374.25 post-fix. The actual mystery was 370.65 (us) vs 374.25 (website), 3.6 fpts. Same root cause.

### 2. "Website per-stat counting differs from extracted breakdown" — RESOLVED, same root cause

Website was 1-7 stat counts higher than our extracted breakdown for Hosstros MP1 batters (367 ABs vs 361, 53 1Bs vs 52, etc). Re-extraction didn't close the gap, ruling out simple stat-correction lag. Phase 3.3's DH fix recovers exactly the missing counts (Turang's missing 4 AB / 1 3B / 1 R / 1 RBI / 2 K plus Frelick's micro-gap) — the same root cause explained both the team-level fpts gap and the per-stat count gap.

---

## Bookmarks for Future Work (carried forward)

### Phase 4 — Wasted Points (NEXT)

When Phase 4 lands, the FA path will need its own DH treatment. The mRoster endpoint we use in Phase 3.3 is *rostered-players-only* by construction — FAs are absent. Phase 4's FA stat extraction (most likely via `league.free_agents()` or a raw `kona_player_info` view call) almost certainly has the same wrapper overwrite bug for FAs on DH days, and will need the same sum-across-splits treatment.

Reusable: `get_doubleheader_pro_teams(d)` — generic, no rostered-only assumption, drop in as-is.
Not reusable: `fetch_raw_player_stats(year, scoring_period)` — mRoster doesn't include FAs.

This implication is also flagged in code via a docstring note on `fetch_raw_player_stats`, so the next-phase author will see it when looking for the pattern to copy.

### "Record Set This Week" callout

Originally scoped for Phase 3.1, deferred to Phase 4+. Build on `mart_stat_leaderboard` — query rank=1 row per stat in current week vs previous-build snapshot.

### MetricFlow Semantic Layer

Wide convergence facts are the natural anchor. Phase 3.3 didn't touch this; still on the radar for Phase 3.4 or later.

### Output script split

`generate_summary.py` and `generate_records_report.py` could share a common module for connection management, formatting helpers, and BBCode output. Not urgent.

### Owner nicknames seed wired in

Same `COALESCE(nickname, owner_name)` pattern as `player_nicknames`. Extraction needs `owner_id`; threading through staging is straightforward.

### `fct_team_career_stats` mart

All-time wins/losses, GOTW counts, average weekly score, season-best/worst.

---

## Migration Notes for Next Session

State at end of Phase 3.3:

- Phase 3.3 work is in the `charming-turing-b57294` worktree, ready to commit and merge.
- Backfill complete: both 2025 (full season) and 2026 (MP1-4 as of 2026-04-30) re-extracted with DH-corrected logic. All incremental facts rebuilt via `dbt build --full-refresh`.
- All dbt tests green including the new `accepted_values: [0, 1, 2]` test on `games_played`.
- Memory files updated: `project_phase_plan.md` (move 3.3 to shipped, resolve Phase 3.2 open investigations), `project_conventions.md` (add hybrid-extraction pattern + `games_played` semantics).

To resume in a fresh conversation:
> "Reading the project memory and Phase 3.3 docs. Phase 3.3 (doubleheader handling) is shipped and verified. Ready to start Phase 4 (wasted points) — the FA extraction path will need its own DH treatment per the Phase 3.3 carryforward note."

---

## Git History (commits expected through Phase 3.3)

After commit, history will include:
1-19. (Phase 1.0 through 3.2, see prior phase docs)
20. Phase 3.3: doubleheader handling — hybrid extraction with mRoster override on DH days, `games_played` column, `--all` flag (completed periods only), removed Phase 3.2 leftover extract_box_scores.py
21. Add Phase 3.3 documentation
