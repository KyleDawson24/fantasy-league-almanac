# Phase 6.3.3 Continuation Brief — for fresh chat

**Status**: Phase 6.3.1 + 6.3.2 + 6.3.3a shipped (commit `3c1c883`). About 60% of Phase 6.3.3 still ahead. Pause was a clean save-point; nothing in flight.

---

## Project context (one paragraph)

ESPN fantasy baseball front-page generator. ELT pipeline: Python extract → Snowflake raw → dbt staging/int/marts → Python output scripts producing BBCode for the league frontpage and Google Sheets exports. Solo project, weekly cadence. Portfolio piece targeting Senior Data Analyst / Analytics Lead roles. The user runs a 14-team H2H league. Project conventions, phase plan, and architecture decisions live in `.claude/projects/C--Users-kyled-projects-espn-league-manager/memory/` — read those before deep work. Phase docs `Phase 1.0` through `Phase 5.0 Documentation.md` in the repo root are source of truth for shipped state.

---

## What's already done in Phase 6.3 (do not redo)

**Phase 6.3.1 — GCP setup (committed in `3c1c883`)**:
- `.env.example`, `.gitignore`, `requirements.txt` updated for Sheets dependencies and OAuth token cache exclusion.

**Phase 6.3.2 — Sheets writer (committed in `3c1c883`)**:
- `output/sheets_writer.py` exists. OAuth user-flow client, two-tab idempotent writes, polarity filter applied. First-run consent caches token to `output/.sheets_oauth_token.json` (gitignored). Verified working against the user's league sheet.
- `output/generate_records_report.py` has opt-in Sheets sink (`SHEETS_OUTPUT_ID` env var; no-op when unset).

**Phase 6.3.3a — leaderboard rename (committed in `3c1c883`)**:
- `mart_stat_leaderboard.sql`: `record_direction` values renamed `best`/`worst` → `most`/`fewest`. Cap held at top-10 (collapse-detection buffer for 6.3.3b). Header comment expanded.
- All four output scripts (`records.py`, `generate_summary.py`, `generate_records_report.py`, `sheets_writer.py`) updated to use new direction values.
- Mart tests pass; both BBCode scripts produce identical structural output to pre-rename.

---

## What's remaining in Phase 6.3.3b

Six chunks. Roughly one focused session of work end-to-end (~2 hours including a ~30 min dbt full-refresh wait).

### 1. Tracked-stats expansion (biggest chunk; touches int + 2 fcts + mart + backfill)

Add 8 new pivots to `int_player_weekly_performance.sql` (count + `_pts` columns each), propagate through `fct_weekly_player_performance.sql` and `fct_weekly_team_performance.sql`, then add to `mart_stat_leaderboard.sql` UNPIVOT lists:

| Stat name in seed | Polarity | Notes |
|---|---|---|
| `GDP` | -2 (negative) | Ground into double play. May appear as `GIDP` in some sources. |
| `B_IBB` | +0.5 | Intentional walks (batter). |
| `HBP_P` | -1 | Pitcher HBP — **already overridden at extract via `_STAT_ID_TO_NAME[42]`** (Phase 4 fix), so the stat_name in stg is `HBP_P`. |
| `BLSV` | -2 | Blown saves. |
| `NH` | +15 | No-hitters. |
| `PG` | +250 | Perfect games. |
| `PK` | +1 | Pickoffs. |
| `64` | +5 | Stat ID 64 = SHO (shutouts) per Phase 3.2 docs. **Verify the seed name** — it may be `64` literal or `SHO`. Check `dbt_league/seeds/stat_classification.csv`. |

After int + fct changes, run `dbt build --full-refresh -m mart_stat_leaderboard+` (or appropriate selector). Backfill takes ~30 min based on prior phases. Expect 58+ tests to pass.

### 2. Mart additions: derived stats + wasted_points

Add to `mart_stat_leaderboard.sql`'s `team_source` and `player_source` CTEs (they're views, no backfill needed):

- **Derived stats** (compute inline):
  - `pa = ab + b_bb + hbp + sf` (plate appearances)
  - `sb_cs = sb - cs` (net stolen bases)
  - `w_l = w - l` (net wins-losses)
  - `sv_blsv = sv - blsv` (net saves; depends on BLSV from chunk 1)
  - `hr_per_9 = case when outs > 0 then p_hr * 27.0 / outs else null end`
  - `bb_per_9 = case when outs > 0 then p_bb * 27.0 / outs else null end`
- **Existing rate stats** to add to UNPIVOT (already on fct): `era`, `whip`, `k_per_9`, `k_per_bb`
- **Wasted points** (team grain only):
  - `LEFT JOIN mart_wasted_points` on `(season_year, matchup_period, team_id)` aggregated to team-week, project as `wasted_points` column.
  - Source: `bench_wasted_pts` summed per team per matchup (FAs excluded since they have no team_id).

### 3. records.py orchestration

Add to `output/records.py`:

- `HITTER_AB_THRESHOLD = 225`, `PITCHER_IP_THRESHOLD = 50` constants with v2-vision comment about dynamic thresholds from lineup-slot config.
- `get_team_contributors_bulk(tuples)`: takes list of `(season, mp, team_id, stat_name)` tuples; deduplicates internally; one batched query per call; returns `dict[tuple] -> list of {display_name, stat_value}` (top 3 per tuple).
- `get_player_contributors_bulk(tuples)`: takes list of `(season, mp, player_id)` tuples; for each, reads the player's row from `fct_weekly_player_performance` and identifies top 3 stats by `*_pts` value; returns `dict[tuple] -> list of {stat_name, count_value, point_value}`. Surfaces COUNT not points to the user (per user spec for readability).
- `format_week_label(season, mp, schedule_lookup)`: returns "Week N" for regular weeks, playoff round name for playoff weeks. `matchup_schedule` seed already has `is_playoff` and `playoff_round` columns (verified: 2025 MP24-26 are "Round 1", "Semi-Finals", "Finals"). `schedule_lookup` is a dict pre-loaded once per script run.
- High-level `get_records_with_contributors(scope, top_n=5)` orchestrator: one call returns fully-stitched, threshold-filtered, tie-collapsed records ready for any sink. Both BBCode and Sheets consumers call this.

### 4. Tie-collapse logic (reuses `format_contributors` algorithm pattern)

In `records.py` (or a small helper module). Walk leaderboard tiers from rank 1 down for each `(stat, direction, scope, grain)` tuple:

```
used = 0
for each tier (group of consecutive rows with identical stat_value):
  if used + group_size <= 5:
    list each row individually
    used += group_size
  else:
    emit "N teams tied at value" row using count_value_occurrences()
      for accurate N (mart caps at top-10; ties extending past need fct query)
    break (cap reached)
```

The user's collapse rule (locked in conversation): collapse a tier when listing all members would push cumulative entries past 5. Reusing `format_contributors`-style stable algorithm. `count_value_occurrences()` already exists in `records.py` from Phase 5 #3.

For collapsed rows in Sheets schema:
- `Holder = "N teams"` (or `"N players"` for player grain)
- `Team Abbrev`, `Owner`, contributor cols = blank
- `Value = the tied value`
- `Season`, `Week` = most recent occurrence (for context)

### 5. Sheets writer expansion (`output/sheets_writer.py`)

Current 10-col schema:
```
Scope | Grain | Stat | Direction | Holder | Team Abbrev | Owner | Value | Season | Week
```

Expand to **17-col** schema (locked in conversation):
```
Scope | Grain | Stat | Direction | Rank | Holder | Team Abbrev | Owner | Value | Season | Week | Contributor1 | Count1 | Contributor2 | Count2 | Contributor3 | Count3
```

- **Tab 1 (All-Time Records)**: rank=1 only per (stat, direction). With contributors. Existing tab; expand to include both directions and contributors.
- **Tab 2 (Current Season Records)**: same shape as Tab 1, scoped to active season. Existing tab; expand similarly.
- **Tab 3 (Leaderboard Dump) NEW**: top-5 per (stat, direction, scope) — both scopes interleaved with a Scope column. With contributors. Use the high-level `records.get_records_with_contributors()` orchestrator.

For team-grain rows: contributor cols hold player names + their counts of the ranked stat.
For player-grain rows: contributor cols hold STAT NAMES + counts (not player names) — per user spec. Each player's top 3 stats by `*_pts` contribution, displayed as count not points.

Idempotent writes (clear-and-rewrite per tab) — preserve existing pattern.

### 6. Playoff round naming + final touches

- Apply `format_week_label()` everywhere "Week N" is constructed:
  - `output/generate_summary.py`: records sections + new-record callouts.
  - `output/generate_records_report.py`: BBCode summary.
  - `output/sheets_writer.py`: Sheets `Week` column.
- Verification:
  - `dbt build --full-refresh` passes (58+ tests green).
  - `python output/generate_summary.py` produces structurally-similar BBCode (now with playoff-week round names where applicable).
  - `python output/generate_records_report.py` writes 3 tabs to Sheets correctly.
  - Tie-collapse fires for situational stats (CG/HLD/SV "Fewest" with N in the hundreds → single collapsed row).
  - Spot-check Tab 3 has both scopes interleaved.

### Documentation (don't skip)

- **`Phase 6.3.3 Documentation.md` in repo root** (NEW): standard phase doc structure matching `Phase 5.0 Documentation.md`. ~400 lines. Cover the six chunks above, the rename in 6.3.3a, the Sheets foundation in 6.3.1+6.3.2.
- **Memory updates**:
  - `project_phase_plan.md`: mark Phase 6.3.3 shipped.
  - `project_conventions.md`: capture mart-vs-helper boundary decision (thin marts + Python helpers when consumer count is low; denormalize when 3+ consumers materialize); threshold filtering at output-not-mart; counts-not-points for player contributors.
- **Schema.yml updates** for any new mart columns.
- **Inline comments** on the new helpers explaining the calculate-once-present-many pattern.

### Out of scope for 6.3.3 (capture in eventual ROADMAP.md)

- Frequency-table / "Notable Frequencies" tab (the user reframed: tie-collapse handles this naturally; no separate output).
- Conditional 3rd "Top Scorer" line (suppress when redundant — already a backlog item from Phase 5).
- AB-SO contact-rate proxy (deferred per spec discussion).
- Tracked-stats config seed/YAML for cross-league portability (v2 candidate).
- Dynamic rate-stat thresholds from lineup-slot config (v2 candidate).
- Non-playoff teams during playoff weeks edge case (v1.x).
- Cross-platform support / BigQuery target (post-1.0).

---

## Key decisions already locked in (don't relitigate)

1. **Both directions for player records too** — Phase 5 originally shipped player-grain "best only"; user asked to extend to most+fewest in conversation. Concrete intent: see what surfaces; if Player "fewest calc_points" is dominated by zero-tied non-participants, defer or threshold later.

2. **Tie-collapse rule**: collapse when listing all of a tier would push cumulative displayed entries past 5. Same algorithm as `format_contributors` (`max_n=5`). Use `count_value_occurrences()` for accurate tie counts when mart top-10 saturates.

3. **AB threshold = 225, IP threshold = 50** — hardcoded constants. Slightly more lenient than calculated p33 (232 / 50 from team-week distribution analysis).

4. **Counts not points for player-grain contributors** — readability over precision. Fans who know the league weights can mentally convert.

5. **Mart stays thin; contributor stitching via Python helpers** — explicit decision (thin marts + helpers when consumer count is low; denormalize only when 3+ consumers emerge).

6. **Threshold filter at output layer, not in mart** — keeps mart pure; threshold lives where a maintainer expects to find it.

7. **Naming conventions for stats in display**: abbreviations with disambiguation parentheticals where needed. Examples: `HR` / `HR Allowed`; `K (Pitcher)` / `SO (Hitter)`; `BB Allowed` / `BB (Hitter)`. The `STAT_DISPLAY` map in `output/formatters.py` is the source of truth and may need updates to fit this convention; pick one (numeric short like `1B/2B/3B/HR` OR prose like `Singles/Doubles/Triples/Home Runs`) and apply uniformly across BBCode and Sheets.

8. **Playoff data is already in matchup_schedule seed** (verified). 2025 MP24-26 carry `is_playoff=true` with `playoff_round` strings.

---

## File touch list (estimated)

```
dbt_league/seeds/stat_classification.csv             # verify SHO mapping for stat 64
dbt_league/models/intermediate/int_player_weekly_performance.sql   # 8 new pivots
dbt_league/models/marts/fct_weekly_player_performance.sql          # propagate new columns
dbt_league/models/marts/fct_weekly_team_performance.sql            # propagate new columns
dbt_league/models/marts/mart_stat_leaderboard.sql                  # add new stats to UNPIVOT,
                                                                    # add derived stats to source CTE,
                                                                    # LEFT JOIN mart_wasted_points
dbt_league/models/marts/schema.yml                                 # column docs
dbt_league/models/intermediate/schema.yml                          # column docs
output/records.py                                                  # bulk helpers, orchestrator,
                                                                    # tie-collapse, threshold filter,
                                                                    # playoff naming helper
output/sheets_writer.py                                            # 17-col schema, 3rd tab
output/generate_records_report.py                                  # consume orchestrator
output/generate_summary.py                                         # apply playoff naming
Phase 6.3.3 Documentation.md                                       # NEW phase doc
.claude/projects/.../memory/project_phase_plan.md                  # mark shipped
.claude/projects/.../memory/project_conventions.md                 # new conventions
```

---

## Anchor verification checks

After implementation:

1. `dbt build --full-refresh` runs clean (58+ PASS / 0 ERROR / 0 WARN).
2. `python output/generate_summary.py` produces all sections with playoff weeks rendered as round names (verify against 2025 MP24-26).
3. `python output/generate_records_report.py` writes 3 tabs to Sheets:
   - Tab 1 (All-Time): rank=1 only per stat × direction, with contributors.
   - Tab 2 (Current Season): same shape, scoped.
   - Tab 3 (Leaderboard Dump): top-5 per stat × direction × scope.
4. Tie-collapse renders "N teams tied at 0" rows for situational stats (CG/HLD/SV "Fewest").
5. Player-grain contributor cols show stat names + counts (e.g., `K | 8 | W | 2 | SV | 1`), not point values.
6. `Phase 6.3.3 Documentation.md` lands in repo root.
7. Final commit + push.

---

## Suggested kickoff message for fresh chat

```
Phase 6.3.3 continuation. Read "Phase 6.3.3 Handoff.md" in the repo root --
that's the spec lock and remaining-work brief for this session. Phase
6.3.3a (mart rename) shipped at commit 3c1c883; we're picking up at the
six remaining chunks (tracked-stats expansion, mart additions, records.py
orchestration, tie-collapse, sheets writer expansion, playoff naming).

The handoff doc has key decisions, file touch list, and verification
checks. Memory in .claude/projects/.../memory/ has project context.
Start by reading the handoff doc, then propose your first chunk and
approach before making any changes.
```
