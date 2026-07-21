# Phase 6.3.3 Handoff — ESPN Fantasy Baseball Front Page Generator

## What Changed Since Phase 5.0

Phase 6 is the Google Sheets sink work. Phase 6.2 was a Python-side refactor that consolidated records data access into a single module. Phase 6.3 then layered the Sheets sink and a meaningful expansion of which stats and presentations the league surfaces. Five sub-phases shipped in sequence:

1. **Phase 6.2** — Records data access extracted from the two consumer scripts into `output/records.py`. Pure-function module with `get_all_time_records()`, `get_current_season_records()`, `get_records_set_this_week()`, plus the polarity filter / seed-to-leaderboard name translation / `count_value_occurrences()` helpers. Both `generate_summary.py` and `generate_records_report.py` now import from it. No behavior change; pure refactor that made Phase 6.3 possible.

2. **Phase 6.3.1** — GCP foundation. `requirements.txt` adds `gspread` + `google-auth-oauthlib`. `.env.example` documents the four new env vars (`SHEETS_OUTPUT_ID`, `GOOGLE_OAUTH_CLIENT_PATH`, plus existing). `.gitignore` excludes the cached OAuth user token (`output/.sheets_oauth_token.json`).

3. **Phase 6.3.2** — Sheets writer foundation. New `output/sheets_writer.py` with OAuth user-flow auth (browser consent → cached token → silent refresh). Two-tab idempotent writes (clear-and-rewrite per tab). Polarity filter applied. First-run consent caches token; subsequent runs are silent. Wired as opt-in sink in `generate_records_report.py` via `SHEETS_OUTPUT_ID` env var; no-op when unset.

4. **Phase 6.3.3a** — Leaderboard rename `record_direction` values `best`/`worst` → `most`/`fewest`. The mart is direction-agnostic (most HR is best, most ER is worst); polarity-aware "Best"/"Worst" label belongs at the consumer side. Cap held at top-10 as buffer for the tie-collapse logic in 6.3.3b. All four output scripts updated to consume the renamed values.

5. **Phase 6.3.3b** — The substantive Sheets sink expansion. Six chunks:
   - **Chunk 1**: 8 new tracked stats (GDP, B_IBB, HBP_P, BLSV, NH, PG, PK, SHO) propagated through int → fct → mart with both count and `_pts` columns.
   - **Chunk 2**: Mart-only derived stats (PA, SB-CS, W-L, SV-BLSV), rate stats surfaced into the leaderboard at team grain (ERA, WHIP, K/9, K/BB, HR/9, BB/9), and `mart_wasted_points` joined as a team-grain stat. Player-grain rate stats deliberately dropped at mart layer (Path A: 1-IP relief outliers carry no signal).
   - **Chunk 3**: `records.py` orchestrator. Bulk contributor fetches (one batched query per grain). New `get_records_with_contributors(scope, top_n)` orchestrator stitches polarity filter + collapse + contributors. Player-grain contributors surface stat NAMES (not player names) plus counts (not pts; readability over precision per locked spec).
   - **Chunk 4**: Tie-collapse logic. Walks each `(grain, stat, direction)` group; emits each row individually if cumulative count fits under cap; replaces overflow tier with one synthetic "N teams tied at value" row. Mart's top-10 buffer feeds `count_value_occurrences()` for accurate counts when the visible tier saturates.
   - **Chunk 4.5**: Three smaller follow-ups bundled in: utf-8 stdout reconfig (Windows cp1252 crashes on emoji team names), player-grain records extended to both directions (was Most-only in Phase 5), `is_always_tracked` seed flag wired into the polarity filter (H/TB/XBH/SF/ER override polarity rules at team grain).
   - **Chunk 5**: Sheets writer expansion to 17-col schema with three tabs (All-Time / Current Season / Leaderboard Dump). Tab 1+2 use `top_n=1`, Tab 3 uses `top_n=5` interleaving both scopes. Player-grain contributor cells render stat name + count; team-grain cells render player name + stat value.
   - **Chunk 6**: Playoff round naming via `format_week_label()` (Week N for regular weeks; "Round 1" / "Semi-Finals" / "Finals" for playoff weeks per the existing `matchup_schedule` seed). Threaded through every BBCode and Sheets value-construction site.
   - **Chunk 6.5**: Polarity-aware "Best"/"Worst" labels everywhere via `get_effective_polarity()` (seed-derived + implicit overrides for non-seed stats). OUTS → baseball IP notation in Sheets `Value` and contributor count cells. New `fmt_record_value()` helper for stat-aware display.
   - **Chunk 6.6**: Inline tier expansion (small overflow tiers ≤ 3 render as comma-joined identities `NPNP, FNA` instead of `2 teams`); 4-cohort sort key (Best Scores → Best Stats → Worst Scores → Worst Stats); new `output/league_notes.py` registry-pattern module for league-flavor color callouts wired into the weekly summary (skipped when no rules fire).

The output is now multi-sink: the league commissioner posts BBCode to the ESPN frontpage, AND a Google Sheet captures the full leaderboard dump (all stats × both directions × both scopes × top-5) for archival and ad-hoc review.

**Concrete validation cases from the most recent run (2026 Week 5, end-to-end):**
- Chunk 1: New BLSV record fires in the recap — `New Most Blown Saves: No Power, No Panic, 5 BLSV (Prior: 4 BLSV by Sam Hartwell (NPNP) in Week 18 of 2025)` with contributors `Ryan Walker: 2, Luke Weaver: 1, Andres Munoz: 1, Kenley Jansen: 1, 23 others with 0`.
- Chunk 1 stats render in the records report: SHO, PK, NH, PG all surface with their respective contributors and second-place tiers.
- Chunk 2 wasted points: `Most WASTED_POINTS (team, current season, rank 1) -- Atomic Alpaca Armada W2 88.56 pts`.
- Chunk 2 ERA: `Fewest ERA (team, current season, rank 1) -- Rob Manfred Death Squad W5 1.35` with empty contributors (rate stat — no per-player breakdown).
- Chunk 4 collapse: 48 collapsed synthetic rows in the current-season leaderboard dump. CG/NH/PG fewest correctly collapse at `tie_count=420-440` (floor-zero situational stats).
- Chunk 5 Sheets: 100 + 100 + 807 rows written to 17-col schema across three tabs, no row-width mismatches.
- Chunk 6 playoff naming: `Wild Pitches: 6 by Bigger Bases Pitch Clock in Round 1 of 2025`, `Shutouts: 1 by Intentional Walk to the Bar in Round 1 of 2025`.
- Chunk 6.5 polarity-aware labels: Earned Runs `most → Worst`, ERA `fewest → Best`, Wasted Points `most → Worst`, Hits `most → Best` (always-tracked positive).
- Chunk 6.6 inline collapse: `Home Runs Best -> NPNP, FNA` in Tab 1 (2 teams tied for All-Time HR record); `Bases Untouched: ...` style callouts available in `league_notes.py` for the user to extend.
- Chunk 6.6 emoji safety: `Pickoffs: 4 by Team Hybrid✊🏽 in Week 15 of 2025` renders without UnicodeEncodeError (utf-8 stdout reconfig).

---

## Project Structure (Current)

```
espn-league-manager/
├── extract/
│   ├── extract.py                                 # unchanged since Phase 4
│   └── dump_stats_map.py                          # unchanged
├── output/
│   ├── formatters.py                              # MODIFIED: STAT_DISPLAY +
│   │                                              #   STAT_ABBREV expanded with
│   │                                              #   chunk-1+2 stats; new
│   │                                              #   fmt_record_value();
│   │                                              #   format_contributors gains
│   │                                              #   value_fmt parameter
│   ├── records.py                                 # MODIFIED: orchestrator
│   │                                              #   (get_records_with_contributors),
│   │                                              #   bulk contributor helpers,
│   │                                              #   tie-collapse, format_week_label,
│   │                                              #   load_schedule_lookup,
│   │                                              #   league_history_count, ordinal,
│   │                                              #   get_effective_polarity,
│   │                                              #   best_or_worst_label,
│   │                                              #   get_always_tracked_stats
│   ├── sheets_writer.py                           # MODIFIED: 17-col schema, 3rd tab,
│   │                                              #   polarity-aware Best/Worst,
│   │                                              #   inline-collapse rendering,
│   │                                              #   4-cohort sort, OUTS→IP
│   ├── league_notes.py                            # NEW: registry-pattern callout
│   │                                              #   module (zero_steals,
│   │                                              #   no_hitters, hr_drought
│   │                                              #   templates; user-extensible)
│   ├── generate_summary.py                        # MODIFIED: utf-8 stdout reconfig;
│   │                                              #   format_week_label threaded
│   │                                              #   through all formatters; ordinal
│   │                                              #   moved to records (re-imported
│   │                                              #   here for backward compat);
│   │                                              #   league_notes.render_callouts
│   │                                              #   wired in
│   ├── generate_records_report.py                 # MODIFIED: utf-8 stdout reconfig;
│   │                                              #   schedule_lookup threaded;
│   │                                              #   chunk-2 stats excluded
│   │                                              #   (no per-player breakdown
│   │                                              #   story); fmt_record_value
│   │                                              #   for OUTS→IP; chunk-1 stats
│   │                                              #   added to STAT_ORDER
│   ├── LeagueNote.txt                             # gitignored
│   ├── .sheets_oauth_token.json                   # gitignored (Phase 6.3.1)
│   └── logs/
└── dbt_league/
    ├── seeds/
    │   └── stat_classification.csv                # unchanged structurally;
    │                                              #   chunk-1 stats already
    │                                              #   present pre-Phase-6.3.3
    └── models/
        ├── intermediate/
        │   ├── int_player_weekly_performance.sql  # MODIFIED: 8 new chunk-1
        │   │                                      #   pivots (count + _pts each)
        │   └── schema.yml                         # docs unchanged for now
        └── marts/
            ├── fct_weekly_player_performance.sql  # MODIFIED: chunk-1 columns
            │                                      #   rolled up + projected
            ├── fct_weekly_team_performance.sql    # MODIFIED: chunk-1 columns
            │                                      #   rolled up + projected
            ├── mart_stat_leaderboard.sql          # MODIFIED: chunk-1 stats in
            │                                      #   UNPIVOT lists; chunk-2
            │                                      #   derived stats inline +
            │                                      #   rate stats from fct +
            │                                      #   wasted_points LEFT JOIN
            │                                      #   (team_wasted CTE filtered
            │                                      #   to ROSTERED_INACTIVE);
            │                                      #   record_direction values
            │                                      #   most/fewest (Phase 6.3.3a)
            ├── mart_wasted_points.sql             # unchanged
            └── schema.yml                         # docs unchanged for now
```

Three new untracked files in `archive/` documenting the build:
- `phase_6.3.3_chunk1_build.log` — full `dbt build --full-refresh` log proving chunk 1 compiles + tests pass.
- `phase_6.3.3_chunk2_build.log` — `dbt build --select mart_stat_leaderboard` confirming chunk 2 pure-view rebuild (11/11 tests pass).
- `chunk3_smoke.py` / `chunk4_smoke.py` / `chunk5_smoke.py` — targeted Python smoke tests for the chunk 3/4/5 helpers (orchestrator return shape, collapse-tie behavior, sheets row builder + polarity-aware labels + playoff naming + inline-collapse). Useful for re-verification post any future refactor.

---

## What Was Built in Phase 6.3.3

### Phase 6.2 — `output/records.py` extraction (precursor)

Pulled records data access out of `generate_summary.py` and `generate_records_report.py` into a single module:

- `get_all_time_records()` / `get_current_season_records()` — rank-1 leaderboard rows for both grains, all stats, both directions. Polarity filter NOT applied here; consumers filter for their use case.
- `get_records_set_this_week(season, mp)` — new/tied records detected via the leaderboard's recency tiebreak (rank-1 row with matching `(season_year, matchup_period)` is "set this week"; rank-2 row is the prior holder unless tied at rank 1).
- `get_record_top_n(stat, grain, direction, scope, limit)` — multi-rank holders for one stat (records report uses this).
- `get_team_contributors(season, mp, team_id, stat_column)` — per-player contributions for a single team-week (records report uses this for the top-3 contributor line).
- `get_stat_polarity()` — derives `'positive' | 'negative' | 'neutral'` from `sign(points_per_unit)` in `stg_scoring_settings`. Translates seed-side names (`1B`/`2B`/`3B`/`64`) to leaderboard-side names (`SINGLES`/`DOUBLES`/`TRIPLES`/`SHO`) via `_SEED_TO_LEADERBOARD`.
- `should_track_record(grain, stat_name, direction, polarity, always_tracked=None)` — Phase 5 polarity filter rule, extended in 6.3.3 chunk 4.5 with the `always_tracked` parameter.
- `count_value_occurrences(grain, stat_name, value)` — entity-week count at exact equality. Used for accurate "Nth team" framing on tied records when the leaderboard's top-10 buffer saturates at one value. Extended in chunk 6.6 (see `league_history_count` below).

### Phase 6.3.1 — GCP foundation

- `.env.example` documents `SHEETS_OUTPUT_ID` (Google Sheet ID for the records sink) and `GOOGLE_OAUTH_CLIENT_PATH` (path to OAuth desktop client JSON downloaded from GCP).
- `requirements.txt` adds `gspread` and `google-auth-oauthlib`.
- `.gitignore` excludes `output/.sheets_oauth_token.json` (cached user OAuth token; first-run flow caches it, refresh tokens valid until revoked).

### Phase 6.3.2 — `output/sheets_writer.py` foundation

OAuth user-flow client. First call opens browser tab for consent; subsequent calls use cached refresh token and silently refresh access token. Token cache lives next to the script (gitignored).

Two tabs initially: `All-Time Records` and `Current Season Records`. Each clear-and-rewrite per call (idempotent).

Wired as opt-in sink in `generate_records_report.py`:
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

The exception swallow exists so a Sheets-side failure (network blip, expired refresh token, sheet permission revoked) doesn't kill the BBCode output that the league still needs.

### Phase 6.3.3a — `mart_stat_leaderboard.sql` rename

`record_direction` values renamed `best`/`worst` → `most`/`fewest`. Reasoning: the mart is direction-agnostic — "most HR" is best for HR but "most ER" is worst for ER. Polarity-aware Best/Worst label belongs at the consumer side, not in the mart. This rename made room for chunk 6.5's polarity-aware labeling to land cleanly without ambiguity.

Cap held at top-10 (was previously top-10 for buffer purposes pre-6.3.3a — the cap height itself didn't change in this rename, just the semantic name of the dimension). This buffer feeds chunk-4 collapse detection.

All four output scripts updated to consume the renamed values: `records.py`, `generate_summary.py`, `generate_records_report.py`, `sheets_writer.py`.

### Phase 6.3.3b chunk 1 — Tracked-stats expansion

Eight new pivots through the int → fct → mart pipeline. Each gets both a count column and a `_pts` column. The seed (`stat_classification.csv`) had these stats already documented; chunk 1 added them to the wide pivots:

| Stat name | ESPN ID | Polarity | Notes |
|---|---|---|---|
| GDP | 26 | -2 (negative) | Ground into double play (batter). |
| B_IBB | 11 | +0.5 | Intentional walks (batter). |
| HBP_P | 42 | -1 | Pitcher HBP. Already disambiguated at extract via `_STAT_ID_TO_NAME[42]` (Phase 4 fix), so the stat_name in stg is `HBP_P`. |
| BLSV | 58 | -2 | Blown saves. |
| NH | 65 | +15 | No-hitters. |
| PG | 66 | +250 | Perfect games. Previously misidentified as Pitches Per Game (Phase 3.2 lesson). |
| PK | 52 | +1 | Pickoffs. |
| 64 (→ SHO column) | 64 | +5 | Shutouts. **Seed kept as numeric '64'** because espn-api wrapper doesn't translate stat ID 64; the int model aliases it to `sho` via `case when stat_name = '64' then ... as sho`. The Phase-5-era `_SEED_TO_LEADERBOARD = {'1B': 'SINGLES', ..., '64': 'SHO'}` map was extended for this. |

Backfill: `dbt build --full-refresh` rebuilt all incremental facts. 58/58 tests passed.

### Phase 6.3.3b chunk 2 — Mart additions

Three categories of additions to `mart_stat_leaderboard.sql`'s `team_source` and `player_source` CTEs (views, so no incremental backfill — pure rebuild):

**Derived stats (computed inline):**
- `pa = ab + b_bb + hbp + sf` — plate appearances (matches the seed's id-16 PA definition).
- `sb_cs = sb - cs` — net stolen bases.
- `w_l = w - l` — net wins.
- `sv_blsv = sv - blsv` — net effective save rate. Depends on chunk-1 BLSV.

These are mart-layer only — the facts stay close to raw API semantics. Stats live where the consumer-facing analytical metric lives.

**Existing rate stats from fct, surfaced into the leaderboard at team grain:**
- `era`, `whip`, `k_per_9`, `k_per_bb` (already on `fct_weekly_team_performance` via the rate-stat macros from Phase 3.0).
- `hr_per_9` and `bb_per_9` computed inline (no macro yet): `case when outs > 0 then p_hr * 27.0 / outs else null end`. The 27.0 factor is 9 innings × 3 outs; the `outs > 0` guard prevents div/0 on weeks where a team had no innings pitched (NULL is dropped by Snowflake's `UNPIVOT EXCLUDE NULLS` default, so those rows naturally don't appear in the leaderboard).

**Wasted points (team grain only):**
- `team_wasted` CTE aggregates `mart_wasted_points` per `(season_year, matchup_period, team_id)`, **filtered to `wasted_bucket = 'ROSTERED_INACTIVE'`**. FAs (the other bucket) have no team_id and are nobody's roster decision, so they don't belong in a team-vs-team comparison.
- `LEFT JOIN`'d into `team_source`: a team-week with zero bench/IL waste produces no row in `team_wasted`, so `wasted_points` is NULL there and `UNPIVOT EXCLUDE NULLS` drops it from the leaderboard (correct: a team that wasted nothing shouldn't appear on the wasted-points leaderboard).

**Player-grain rate stats deliberately dropped at mart layer (Path A decision).** Single-IP relief appearances produce 27.00 WHIP / 243 ERA values that dominate the leaderboard but carry no signal. The league only cares about rate records at team grain anyway. Player-level rate stats remain available on `fct_weekly_player_performance` for ad-hoc analysis; they're just excluded from being treated as records here. **Rejected alternative**: threshold-filter at output (e.g., min 1 IP). Path A keeps ad-hoc mart queries clean too.

### Phase 6.3.3b chunk 3 — `output/records.py` orchestrator

Bulk contributor fetches with one batched `SELECT` per grain instead of N round-trips. Built around three new bulk helpers and one high-level orchestrator:

- **`get_team_contributors_bulk(tuples, top_n=3)`** — input is a list of `(season, mp, team_id, stat_name)` tuples; output is `dict[tuple] → list of {display_name, stat_value}` (top N by stat value desc, zero/None values dropped). Single Snowflake call; ranking happens in Python. Stats with no per-player breakdown story (rate stats, WASTED_POINTS) return `[]` for those input tuples. Derived counting stats (PA/SB-CS/W-L/SV-BLSV) are computed inline per-row in `_player_stat_value()` since they don't have fct columns.

- **`get_player_contributors_bulk(tuples, top_n=3, positives_only=True)`** — input is `(season, mp, player_id)` tuples; output is `dict[tuple] → list of {stat_name, count_value, point_value}` (top N stats by signed `*_pts` contribution). Surfaces COUNT not POINTS to the user (per locked spec — readability over precision). Hitter and pitcher pools both included since player-grain records are typically score-level (calculated_*) where contributions can come from either side.

- **`load_schedule_lookup()`** — builds a `(season_year, matchup_period) → {is_playoff, playoff_round}` dict from `matchup_schedule`. One query per script run; passed through every formatter that needs `format_week_label()` instead of re-querying.

- **`format_week_label(season, mp, schedule_lookup)`** — returns `'Week N'` for regular weeks, the playoff round name (`'Round 1'`, `'Semi-Finals'`, `'Finals'`) for playoff weeks. Defensive fallback to `'Week N'` if the (season, mp) is missing — `matchup_schedule` is canonical for the current season; defense-in-depth only.

- **`get_records_with_contributors(scope, top_n=5)`** — high-level orchestrator. One mart query pulls the top-10 buffer per `(grain, stat, direction)` for the given scope. Apply chunk-3's layered filter (Phase 5's `should_track_record` + chunk-2's `_TEAM_NON_SEED_STATS` extension for rates/wasted/derived). Apply chunk-4 collapse trimmed to `top_n`. Then bulk-fetch contributors AFTER collapse (so we don't pay round-trip cost for rows the collapse pass discards). Returns leaderboard rows annotated with `'contributors'`, plus synthetic collapsed rows with `'is_collapsed': True`.

The orchestrator is the single entry point for any consumer that wants a "fully-stitched, threshold-filtered, tie-collapsed records list ready for any sink." Both BBCode and Sheets consumers can call it with appropriate `top_n` for their display target.

### Phase 6.3.3b chunk 4 — Tie-collapse logic

`collapse_ties(records, max_n=5)` walks each `(grain, stat, direction)` group and either passes a tier through unchanged or replaces it with one synthetic row, depending on whether listing every member would push cumulative count past `max_n`. Same algorithm as `format_contributors`'s `max_n` rule.

**Rule:**
```
used = 0
for each tier (consecutive rows with identical stat_value, sorted by rank):
  if used + tier_size <= max_n:
    emit each row individually
    used += tier_size
  else:
    emit ONE synthetic collapsed row for the whole tier
    break  (cap reached)
```

**Synthetic row shape:**
- `entity_grain`, `stat_name`, `record_direction` — inherited from the tier.
- `rank` = `'collapsed'` (sentinel string so consumers can tell synthetic from rank-1..N rows).
- `is_collapsed` = `True`.
- `tie_count` = N. Accurate even when the visible tier saturates the mart's top-10 cap: in that case, `count_value_occurrences()` queries the fct directly. Falls back to visible tier_size when the stat has no fct counterpart (rate stats, WASTED_POINTS).
- `stat_value` = the tied value.
- `season_year` / `matchup_period` = most recent occurrence in the visible tier (for context display).
- `team_id` / `team_name` / `team_abbrev` / `owner_name` / `player_id` / `player_name` / `display_name` = `None` (no single holder).
- `holders` = small-tier-only payload (chunk 6.6, see below).
- `contributors` = `[]`.

### Phase 6.3.3b chunk 4.5 — Three follow-ups bundled

Three smaller items that the chunk-4 work surfaced:

- **utf-8 stdout reconfig**. Windows defaults stdout to cp1252 which crashes on emoji team names (e.g., `Team Hybrid✊🏽`). When the script crashes mid-print, the Sheets sink never fires and the Sheet stays stale — a silent failure mode that masquerades as "the new stats aren't showing up." Fix: `sys.stdout.reconfigure(encoding='utf-8')` at script start, wrapped in try/except for backward compat. Idempotent. Applied to both `generate_summary.py` and `generate_records_report.py`.

- **Player-grain records extended to both directions**. Phase 5 originally shipped player records as Most-only (the polarity filter rejected `(player, *, fewest)`). User asked in conversation to extend to `most + fewest` for player-grain score stats — see what surfaces; if Player "fewest calc_points" is dominated by zero-tied non-participants, defer or threshold later. Concrete change: `should_track_record('player', stat, *)` now passes both directions for `SCORE_STAT_NAMES` instead of just `'most'`.

- **`is_always_tracked` seed flag wired into the polarity filter**. The seed has had this flag since earlier phases but it wasn't consumed. New `get_always_tracked_stats()` returns a frozenset of leaderboard stat_names (after seed→leaderboard translation). `should_track_record()` now accepts `always_tracked` and bypasses the polarity rule for any stat in that set. Use case: stats like H/TB/XBH/SF/ER aren't scored directly in this league but "Most X in a week" is a real, interesting record. Set membership is data-driven via the seed — no code edits needed to add/remove members.

### Phase 6.3.3b chunk 5 — Sheets writer expansion

Schema grows from 10 columns to 17:

```
Scope | Grain | Stat | Direction | Rank
Holder | Team Abbrev | Owner
Value | Season | Week
Contributor1 | Count1
Contributor2 | Count2
Contributor3 | Count3
```

**Three tabs:**
- **Tab 1 (`All-Time Records`)**: rank-1 holder per (stat, direction). All-time scope. With contributors. `top_n=1` so the orchestrator's collapse algorithm doesn't have an overflow tier to detect (max_n=5 default; 1 visible row trivially under cap), so all rank-1 rows pass through individually — except where a tier of 2-3 ties at rank 1, which inline-collapse renders (chunk 6.6).
- **Tab 2 (`Current Season Records`)**: same shape as Tab 1, scoped to active season.
- **Tab 3 (`Leaderboard Dump`)**: top-5 per (stat, direction, scope) — both scopes interleaved with a Scope column. Tie-collapse fires for groups whose tier extends past rank 5 (CG/NH/PG zero-floor ties with N in the hundreds collapse to one synthetic row).

**Contributor cell semantics:**
- Team-grain row: contributor cells hold player names + their counts of the ranked stat (e.g., `Jordan Walker | 5 | Teoscar Hernandez | 2 | Xander Bogaerts | 2`).
- Player-grain row: contributor cells hold STAT NAMES (display labels via `STAT_DISPLAY`) + counts (e.g., `Innings Pitched | 17.0 | Strikeouts (Pitcher) | 22 | Wins | 2`).

**Idempotent writes**: `_replace_tab` clears the worksheet (creating it if missing) and rewrites header + rows. Pre-existing pattern from Phase 6.3.2; preserved.

### Phase 6.3.3b chunk 6 — Playoff round naming

`format_week_label()` (defined in chunk 3) applied everywhere "Week N" was previously constructed:
- `generate_summary.py`: active-week heading (now `"Round 1 Recap"` / `"Semi-Finals Recap"` / `"Finals Recap"` for playoff matchup_periods); records sections; new-record callouts (`"in Round 1 of 2025"` instead of `"in Week 24 of 2025"`).
- `generate_records_report.py`: `fmt_team_in_week()` now reads from the schedule lookup; switched from `"Matchup #N"` to `format_week_label`-derived strings.
- `sheets_writer.py`: `Week` column. Inline-collapsed rows with multiple holders comma-join their per-row labels (each holder's matchup_period gets its own week label so a tier mixing playoff and regular weeks renders correctly).

The lookup is loaded once per script run via `records.load_schedule_lookup()` and passed through every formatter that needs it. Cheaper than re-querying for each record.

### Phase 6.3.3b chunk 6.5 — Polarity-aware Best/Worst + OUTS→IP

Three follow-up items for display polish:

- **`get_effective_polarity()`** — augments `get_stat_polarity()` (seed-derived: sign of `points_per_unit`) with `_IMPLICIT_POLARITY` (hardcoded for stats not in the seed: rates, WASTED_POINTS, derived stats, score columns, always-tracked counting stats). Stats present in the seed keep their seed-derived polarity (zero-weighted gets `'neutral'` and is overridden by implicit). Fills the gap so direction labels can be polarity-aware everywhere.

- **`best_or_worst_label(stat, direction, effective_polarity)`** — uniform Best/Worst label, polarity-aware:
  - Positive stat (HR, K, points): `most → 'Best'`, `fewest → 'Worst'`.
  - Negative stat (ER, GDP, WASTED_POINTS, ERA): `most → 'Worst'`, `fewest → 'Best'`.
  - Unknown stat defaults to positive.

  Replaces the prior per-call-site direction-label string-mapping. Caller pre-builds the polarity dict once per script run and passes it; the helper is O(1) per row.

- **`fmt_record_value(stat_name, value)`** — stat-aware value display for record output. OUTS → baseball IP via `fmt_ip` (51 outs → `"17.0"`); score stats → 1-decimal precision (forces `"415.0"` not `"415"`); everything else falls through to `fmt_value`'s int-or-1-decimal heuristic.

- **`format_contributors(contributors, max_n=3, value_fmt=None)`** gains a `value_fmt` callable parameter (default `fmt_value`) so callers can swap the value formatter (e.g., pass `fmt_ip` for OUTS contributor counts).

In Sheets, the OUTS conversion happens inline in `_format_row()` for the Value column and in `_contributor_cells()` for contributor counts. Other stats keep raw numeric values for clean Sheets numeric sorting.

### Phase 6.3.3b chunk 6.6 — Inline tier expansion + 4-cohort sort + league_notes

Three final items:

**Inline tier expansion**. The original chunk-4 collapse always produced a single "N teams tied at value" summary row regardless of tier size. For small overflow tiers (≤ 3), this is unhelpful — the user sees `2 teams` instead of `NPNP, FNA`. New behavior: when an overflow tier's size is at most `INLINE_COLLAPSE_THRESHOLD = 3` AND the tier isn't saturated against the mart top-10 cap, the synthetic collapsed row carries a `holders` list with each member's identity. The Sheets writer uses this to render comma-joined cells:
- Team-grain: `Holder` = comma-joined team_abbrevs; `Season` = comma-joined years; `Week` = comma-joined `format_week_label`-derived strings (per-row, so playoff/regular mixes render correctly).
- Player-grain: `Holder` = comma-joined display_names.

Above the threshold (or when the tier saturates and we don't actually have all members in memory), `holders=[]` and we fall back to the legacy "N teams"/"N players" summary.

**4-cohort sort key**. Tab 3 (and Tab 1+2) now sort top-level by:
1. Best Scores  (direction='Best',  score-stat — hitting/pitching/total points)
2. Best Stats   (direction='Best',  non-score)
3. Worst Scores (direction='Worst', score-stat)
4. Worst Stats  (direction='Worst', non-score)

Within each cohort: stat label alphabetical → scope (All-Time before Current Season) → grain (Player before Team) → rank ascending (collapsed rows sort last).

Note: alphabetical within the score cohort yields Hitting Points → Pitching Points → Total Points (H → P → T), which reads cleanly. The score-cohort score_label_set is hardcoded against the `STAT_DISPLAY` labels, not the raw `CALCULATED_*` keys, so any future label rename only needs to touch `_SCORE_LABEL_ORDER` in `sheets_writer.py`.

**`output/league_notes.py`** (NEW). Registry-pattern module for league-flavor "color" callouts that fire conditionally each week. Same shape as the existing `find_tough_luck` / `check_fair_and_just` inline functions in `generate_summary.py`, but factored into its own module so the rule list is easy to read, edit, and extend without touching the main script.

**How a callout works:**
1. A function takes `ctx` (the dict assembled by `build_ctx()`: scores, players, schedule_lookup, season/mp) and returns a list of BBCode-ready lines (empty if the rule didn't fire).
2. It's appended to `CALLOUTS` (registry list at module bottom; comment-out to disable without deleting).

**Three template patterns shipped as examples:**
- Pattern 1 (regular occurrence, single inline line): `zero_steals` collapses matching teams to one line with comma-joined abbrevs + ordinal range from `league_history_count` ("our 47th-49th zero-steal team-week").
- Pattern 2 (rare occurrence, multi-line): `no_hitters` emits one line per matching player, each with its own ordinal ("our 3rd no-hitter in league history").
- Pattern 3 (regular occurrence, varied template): `hr_drought` random-picks from a small phrasing list to keep weekly recaps from feeling repetitive.

Helpers exposed for callouts (documented in module docstring): `records.ordinal()`, `records.league_history_count()`, `records.format_week_label()`, `formatters.fmt_ip()`, `formatters.fmt_value()`.

`render_callouts(ctx)` runs every callout in registry order with try/except per call so a buggy callout can't kill the weekly recap. Bug message goes to stderr; remaining callouts fire.

`generate_summary.py` wires the section in between Tough Luck and Current Season Records. The section header is conditional — skipped entirely when the rendered list is empty (consistent with how Tough Luck / Fair-and-Just are conditional).

`league_history_count(grain, stat_name, value, op='=')` is the generalization of `count_value_occurrences()` (which now delegates here). Supports `=`, `!=`, `<`, `<=`, `>`, `>=`. Derived stats use `_DERIVED_STAT_FCT_EXPR` to translate the leaderboard stat_name into the inline fct expression (`PA → ab + b_bb + hbp + sf`, etc.). Stats with no fct counterpart (rate stats, WASTED_POINTS) return None — callouts must degrade gracefully.

`ordinal(n)` moved from `generate_summary.py` to `records.py` (re-exported via `from records import ordinal` for backward compat) so `league_notes.py` can import without dragging in the summary script.

---

## Key Technical Decisions

### 1. Mart stays thin; contributor stitching via Python helpers

The orchestrator (`get_records_with_contributors`) does all the contributor-stitching work in Python: filter records, run collapse, then bulk-fetch contributors for surviving rows. The mart could have materialized contributor lists (per leaderboard row, top-3 contributors as a JSON array), but that would denormalize aggressively for what is at present a one-consumer pattern (Sheets Tab 3).

**Locked rule (see `project_conventions.md`)**: thin marts + Python helpers when the consumer count is low; denormalize when 3+ consumers materialize. The records report and BBCode summary use simpler patterns; only the new Sheets dump needs the full orchestrator. If/when a third pattern needs the same shape, revisit.

### 2. Threshold filter at output, not mart

For player-grain rate stats specifically, the chunk-2 decision was Path A (drop entirely at mart layer rather than threshold-filter at output). But for the more general counting-stat threshold question (e.g., "Top OPS records minimum 50 PA"), the design rule is: threshold filtering at output, not in the mart. Keeps the mart pure (it represents what was, not what's interesting); the threshold lives where a maintainer expects to find it (in the records.py orchestrator).

Phase 6.3.3 didn't actually need to apply this rule (no rate-stat record currently surfaces with a threshold), but the constants `HITTER_AB_THRESHOLD = 225` and `PITCHER_IP_THRESHOLD = 50` were placeholder-defined per the handoff doc's design — they're available for future hookup. Note (chunk 3): since rate stats were dropped at mart for player grain, these constants currently don't get applied. If the team-grain rate stats ever need a threshold, that's where these would land.

### 3. Counts not points for player-grain contributors

Player-grain leaderboard rows (typically score-level: CALCULATED_POINTS / CALCULATED_HITTING_PTS / CALCULATED_PITCHING_PTS) surface what the player did to earn that score. Three contributor cells are populated with the player's top-3 stats by point contribution (`get_player_contributors_bulk`'s `positives_only=True` ranks by signed `*_pts` desc, drops `pts <= 0`).

The displayed numbers are COUNTS, not POINTS. Per locked spec: readability over precision. League members who know the weights can mentally convert (Wheeler's 22 K + 17 IP + 2 W is more readable than 22.0 + 13.4 + 14.0 pts).

`positives_only=False` mode (rank by `abs(point_value)` desc) is parameterized but not used in the v1 pipeline — player-grain Fewest is allowed in 6.3.3 chunk 4.5 and surfaces zero-tied non-participants in early-season weeks (rare to be interesting). If/when the worst-performances story matters, flip the flag.

### 4. Polarity-aware effective polarity (seed + implicit fallback)

`get_stat_polarity()` derives from `sign(points_per_unit)` in `stg_scoring_settings`. But many stats the leaderboard surfaces have NO row in the scoring-settings seed:
- Rate stats (ERA / WHIP / K/9 / K/BB / HR/9 / BB/9) — derived from counting stats; no direct point weight.
- WASTED_POINTS — from `mart_wasted_points`; not a scored stat.
- Derived counting stats (PA / SB-CS / W-L / SV-BLSV) — composite metrics computed at mart layer.
- Always-tracked counting stats (H / TB / XBH / SF / ER) — flagged by the seed but not directly scored.

`get_effective_polarity()` augments the seed-derived polarity with `_IMPLICIT_POLARITY` (hardcoded). Stats present in the seed with non-neutral polarity keep their seed-derived value; stats absent or 'neutral' get overwritten from `_IMPLICIT_POLARITY`. Net result: every stat the leaderboard surfaces has a polarity, so `best_or_worst_label` always lands.

`_IMPLICIT_POLARITY` lives in `records.py` (not the seed) because:
- Adding a polarity column to the seed for stats not in scoring_settings would force a join-or-merge layer at staging.
- Polarity for rate stats / wasted_points / derived stats is conceptual (rate-stat "more is bad" is a baseball-knowledge call, not a league-config call) — encoding it in code matches its nature.

### 5. Phase 6.3.3b chunk 4: tie-collapse algorithm reuses the format_contributors max_n shape

The user's locked rule (from conversation): "collapse a tier when listing all members would push cumulative entries past max_n." This is structurally identical to `format_contributors`'s tie-handling: walk groups, emit individually if they fit, switch to count format if they overflow.

Reusing the algorithm shape (rather than designing fresh logic for collapse) means consumers and maintainers don't have two rules to keep straight: "how does the records report handle ties?" and "how does the leaderboard dump handle ties?" share an answer.

### 6. INLINE_COLLAPSE_THRESHOLD = 3 for small-tier inline rendering

Chunk 4 always emitted "N teams tied at value" for any overflow tier. Chunk 6.6 split this: tiers ≤ 3 render inline (`NPNP, FNA, IPB`); tiers > 3 render count-only (`19 teams`).

Threshold value (3) is empirical: in the user's 14-team league, 2-3 teams tied at a rank-1 record is interesting and the abbrevs fit on one line; 4+ teams tied is signal that the value is uninteresting (typically zero-floor situational stats). Tunable via the constant in `records.py` if the league's profile changes.

Saturation guard: when the visible tier saturates the mart top-10 cap (`tier[-1]['rank'] >= 10`), we don't actually have all members in memory — listing the visible 3 would be misleading ("3 teams tied" when really 19 are). Falls back to count-only in that case regardless of size.

### 7. Player-grain rate stats dropped at mart layer (Path A), not threshold-filter at output

Single-IP relief appearances produce 27.00 WHIP / 243 ERA values that completely dominate the leaderboard but carry no signal. Two ways to handle:
- Path A: drop at mart layer entirely. Player rate stats simply don't appear in `mart_stat_leaderboard` for player grain.
- Path B: keep at mart, apply threshold filter at output (e.g., min 5 IP to surface as record).

Decision: Path A. Reasoning:
- The league only cares about rate records at team grain anyway.
- Path A keeps ad-hoc mart queries clean (no surprise 243 ERAs in `SELECT * WHERE entity_grain = 'player' AND stat_name = 'ERA'`).
- The fct still has rate stats at player grain for ad-hoc analysis; only the leaderboard treatment is excluded.

The team-grain rate stats stay in the mart — a team's natural denominator accumulation (~10-12 IP per pitching slot per week) keeps them meaningful without explicit thresholds.

### 8. `format_week_label` pattern: load schedule lookup once, thread through

`load_schedule_lookup()` is called once per script run; the resulting dict is passed through every formatter that needs `format_week_label()`. The alternative — re-querying matchup_schedule for each record formatted — would be ~50-100 round-trips per `generate_summary.py` invocation.

The threading is explicit (parameter on every relevant function) rather than module-global state. Trade-off:
- Pros: pure functions, testable in isolation, no hidden coupling.
- Cons: function signatures grow when records-formatting features expand.

In practice the parameter shows up in 4-5 functions across `generate_summary.py` and 2-3 in `generate_records_report.py`. Manageable.

### 9. `output/league_notes.py` registry pattern, not config seed

Callouts are defined as Python functions, not data rows in a seed. Reasoning:
- Each callout has unique narrative shape (count-with-ordinal vs per-occurrence vs random-template-pick) that's awkward to express declaratively.
- Comment-out-a-line-to-disable is a friendlier authoring experience than seed editing + re-seeding.
- Failure isolation via try/except per call is cleaner in Python than in mart logic.

Per-call try/except around each registered callout means a buggy new callout can't kill the weekly recap. Standing rule for adding callouts: try the function locally before committing; production failure is logged but doesn't stop the script.

### 10. utf-8 stdout reconfig at script start

Windows `sys.stdout` defaults to cp1252, which crashes on emoji and certain non-Latin-1 characters. The user's league has at least one team named `Team Hybrid✊🏽` (raised-fist emoji + skin-tone modifier). When the script crashes mid-print, the Sheets sink — which runs after BBCode output in `generate_records_report.main()` — never fires, and the Sheet stays stale.

Fix: `sys.stdout.reconfigure(encoding='utf-8')` at top of both output scripts, wrapped in `try/except (AttributeError, OSError)` (the method exists on Python 3.7+ and may fail on stdout subclasses in some shells; the try/except keeps the scripts running on edge cases). Idempotent, safe to repeat.

### 11. Ordinal moved to records.py for cross-module use

`ordinal(n)` was originally defined in `generate_summary.py`. `league_notes.py` (chunk 6.6) needs it too, but importing `generate_summary` would drag in main(), Snowflake side-effects, etc. Moved to `records.py`; `generate_summary.py` retains a `from records import ordinal` re-export so any in-file callers continue working.

---

## What's in Snowflake (Current)

- **Database**: `ESPN_FANTASY`
- **Raw schema**: `RAW` — unchanged by Phase 6.3.3.
- **Analytics schema**: `ANALYTICS`
  - `INT_PLAYER_WEEKLY_PERFORMANCE` — 8 new pivots (count + `_pts` columns) for chunk-1 stats: `gdp`, `b_ibb`, `hbp_p`, `blsv`, `nh`, `pg`, `pk`, `sho`.
  - `FCT_WEEKLY_PLAYER_PERFORMANCE` — chunk-1 columns rolled up + projected. Player-grain rate stats unchanged (still derived via the rate-stat macros at this layer, just not surfaced to the leaderboard).
  - `FCT_WEEKLY_TEAM_PERFORMANCE` — chunk-1 columns rolled up + projected.
  - `MART_STAT_LEADERBOARD` — chunk-1 stats added to UNPIVOT lists at both grains; chunk-2 derived stats inlined at mart layer (PA, SB_CS, W_L, SV_BLSV at both grains; rate stats and WASTED_POINTS at team grain only); `record_direction` values renamed `most`/`fewest` (Phase 6.3.3a). Roughly 4× more `(grain, stat_name)` partitions than Phase 5.0.
  - `MART_WASTED_POINTS` — unchanged.
  - All facts + leaderboard rebuilt from scratch via `dbt build --full-refresh` post-chunk-1. Backfill ~30 min for incremental fcts; chunk-2 view rebuild was ~45 sec (no backfill).
  - 2025 (full season + playoffs MP24-26) and 2026 (MP1-5) covered.

---

## Verification

### dbt build

Chunk 1 (`dbt build --full-refresh`): 58 PASS / 0 ERROR / 0 WARN. Build log archived at `archive/phase_6.3.3_chunk1_build.log`.

Chunk 2 (`dbt build --select mart_stat_leaderboard`): 1 view rebuild + 11 tests, all green. Log at `archive/phase_6.3.3_chunk2_build.log`.

Chunks 3-6 are Python-only; no dbt changes.

### Smoke tests (Python, against Snowflake)

`archive/chunk3_smoke.py` — orchestrator + format_week_label + bulk contributors:
```
schedule_lookup: 48 entries
  format_week_label(2025, 24): Round 1                    [playoff naming OK]
  format_week_label(2025, 1):  Week 1                     [regular weeks unchanged]
get_records_with_contributors('current_season', top_n=5): 417 rows
  ('player', 'fewest'): 15
  ('player', 'most'):   15
  ('team',   'fewest'): 160
  ('team',   'most'):   227
Most HR (team, current season, rank 1):
  The Hosston Hosstros W2 HR=17.0
  contributors: [Jordan Walker: 5, Teoscar Hernandez: 2, Xander Bogaerts: 2]
Most WASTED_POINTS (team, current season, rank 1):
  Atomic Alpaca Armada W2 88.56  [contributors: [] — expected]
Fewest ERA (team, current season, rank 1):
  Rob Manfred Death Squad W5 ERA=1.35  [contributors: [] — expected]
```

`archive/chunk4_smoke.py` — collapse-tie behavior:
```
current_season after collapse: 417 rows total
  real rows: 369
  collapsed synthetic rows: 48
team | CG | fewest -> 1 row (COLLAPSED tie_count=420 value=0.0)
team | NH | fewest -> 1 row (COLLAPSED tie_count=440 value=0.0)
team | PG | fewest -> 1 row (COLLAPSED tie_count=440 value=0.0)
team | HLD | fewest -> 5 rows (4 individual + 1 collapsed tie_count=104)
team | CALCULATED_POINTS | most -> 5 rows (no collapse; 5 distinct values)
groups with collapse fired: 48
groups without collapse:    52
```

`archive/chunk5_smoke.py` — Sheets row builders + chunk 6.5/6.6 features:
```
Tab 1 (All-Time):       100 rows
Tab 2 (Current Season): 100 rows
Tab 3 (Leaderboard Dump): 807 rows
Header has 17 cols
Row-width mismatches: 0

Sample player row (Best Total Points all-time):
  Direction: Best        [polarity-aware: most CALCULATED_POINTS = Best]
  Holder: Zack Wheeler   FNA   Luke Barrett
  Value: 66.67   Week: Week 15
  Contributor1: Innings Pitched   Count1: 17.0   [count not pts; IP notation]
  Contributor2: Strikeouts (Pitcher)   Count2: 22.0
  Contributor3: Wins   Count3: 2.0

Sample collapsed row (Complete Games Best, all-time):
  Holder: 19 teams   [count-only fallback for tier > 3]
  Value: 1.0

Inline-collapsed rows (Holder has comma): 22
  Earned Runs        Best   CHIN, GPGP, SFG     [tier of 3 — inline]
  Extra Base Hits    Best   NPNP, NPNP          [tier of 2 — inline]
Tab 1 (All-Time rank=1) collapsed: 33 total, 15 inline (small tier), 18 count-only (4+ tied)

Direction labels by polarity:
  Earned Runs   most -> Worst   fewest -> Best     [negative polarity]
  Home Runs     most -> Best    fewest -> Worst    [positive polarity]
  ERA           most -> Worst   fewest -> Best     [negative rate]
  Wasted Points most -> Worst   fewest -> Best     [negative]
  Hits          most -> Best    fewest -> Worst    [always-tracked positive]

Playoff week labels: 17 rows with 'Round 1' in Week column for 2025 mp24
```

### End-to-end runs (2026 Week 5)

`python output/generate_summary.py` → BBCode renders cleanly. New BLSV record fires:
```
[b]New Most Blown Saves[/b]: No Power, No Panic, 5 BLSV
(Prior: 4 BLSV by Sam Hartwell (NPNP) in Week 18 of 2025)
Contributors: Ryan Walker: 2, Luke Weaver: 1, Andres Munoz: 1, Kenley Jansen: 1, 23 others with 0
```
Active-week heading still `Week 5 Recap` (regular week; would be `Round 1 Recap` for MP24). All record sections render with player + team rows. `league_notes.py` callouts didn't fire this week (the example templates: `zero_steals`, `no_hitters`, `hr_drought` — none of those conditions met).

`python output/generate_records_report.py` → BBCode + Sheets writes. 100 + 100 + 807 rows written to live league sheet (the user's `SHEETS_OUTPUT_ID` was set during this verification; chunk-1 stats and chunk-2 derived/rates and the 17-col schema all rendered correctly). Playoff round names show in playoff-week records: `Wild Pitches: 6 by Bigger Bases Pitch Clock in Round 1 of 2025`. Emoji team name (`Team Hybrid✊🏽`) renders without crash.

---

## Open Investigations Carried Forward

- **"No-Hitters: 0 by [10 teams from this week]" pattern**. The records report iterates over tracked stats and shows the rank-1 holder per stat. For chunk-1 stats that have never happened in league history (no actual no-hitter or perfect game so far), rank-1 stat_value is 0 and the holder is whoever's most recent at value=0 (sorted by recency tiebreak in the mart) — typically all 10 teams from the current week. Misleading: implies the rank-1 holder is special when really nobody has ever achieved the stat. **Pre-existing pattern**, not a Phase 6.3.3 regression — just more visible now that NH/PG/SHO are surfaced. Worth a follow-up: skip the section entirely (or render as "Nobody has thrown a no-hitter yet") when rank-1 stat_value=0 for stats with `is_always_tracked=false`. Not blocking v1.

---

## Bookmarks for Future Work

### Phase 6.4 candidates / cleanup items

- "No-Hitters: 0 by N teams" pattern — see Open Investigations above.
- Rate-stat thresholds: dynamic min-IP / min-AB derived from lineup-slot config rather than hardcoded constants. Currently `HITTER_AB_THRESHOLD = 225` and `PITCHER_IP_THRESHOLD = 50` are placeholder-defined in records.py with a v2-vision comment. Activation depends on which rate stats actually need threshold filtering (currently none — Path A handled player rates at mart).
- Frequency-table / "Notable Frequencies" tab: the user reframed during Phase 6.3.3 — tie-collapse handles this naturally; no separate output needed. Captured as "rejected, reason recorded" rather than "deferred".
- Conditional 3rd "Top Scorer" line — long-standing backlog from Phase 5 (Top Scorer/Hitter/Pitcher rendered together, but Top Scorer often duplicates one of the others when no two-way Ohtani week occurred). Skip when redundant.
- `--no-sheets` / `--dry-run` CLI flag on `generate_records_report.py` so verifiers don't have to juggle env vars (suggested by this phase's verification mishap; see also `feedback_test_running_side_effects.md` in memory). Strict win for ergonomics.
- Cross-platform support / BigQuery target / Path B (DuckDB retarget) — post-1.0.

### Phase 7 — v1.0 portfolio prep (next)

Single weekend's worth of work to ship a polished public release. Moves from Phase 5.0's bookmark unchanged:
- `CHANGELOG.md` (keepachangelog format; map phases retroactively to semver).
- dbt docs: fill in description fields, add `exposures` for the output scripts, push `target/` to `gh-pages` for hosted lineage.
- `README.md` rewrite: 30-second pitch, sample output, Mermaid architecture diagram, "notable engineering decisions" linking to phase docs, separate `SETUP.md` for bring-your-own-credentials.
- `ROADMAP.md` with Now / Next / Later / Won't Do buckets (Phase 6.3.3 deferred items already curated for this).
- Tag `v1.0.0`, GitHub Release with changelog as release notes.
- Optional post-release: r/dbt, r/dataengineering, LinkedIn share.

### Backlog (not gating v1.0)

- **Migrate team records from direct fct query to mart_stat_leaderboard** — long-standing item. Phase 6.2 partially did this (the new-record detection now reads from leaderboard); the records-report-side of `generate_records_report.py` still uses `get_record_top_n` which is a leaderboard read. Effectively shipped in 6.2, just hadn't been formally checked off the list.
- **Wire `owner_nicknames` seed into models**.
- **`fct_team_career_stats` mart**.
- **Investigate explicit `pointsAdjustment` field** in ESPN API to split `platform_calculated_delta` into clean `commissioner_adjustment` + `derivation_delta`.
- **Verify stat ID 30** (15 pts per, only 1 observed row) isn't a real scored stat we're missing.
- **Multi-sink output abstraction** (defer until 2nd non-Sheets sink emerges).
- **GitHub Actions CI on PRs** (`dbt test` + Python compile checks).
- **Python tests for output formatters**.
- **Cross-platform adapters** (Yahoo, Sleeper, etc.) — staging contract already designed with this in mind.
- **Connection-management consolidation** — single Snowflake connection per script run; Phase 7 polish item.
- **Extract performance optimizations** (multi-view single HTTP, batched kona, parallel-fire) — Phase 4.x bookmark.

---

## Migration Notes for Next Session

State at end of Phase 6.3.3:

- Worktree: `suspicious-pasteur-db8872` on branch `claude/suspicious-pasteur-db8872`. **Uncommitted at handoff** — the implementation work survived a chat-platform crash; this doc is being written as the recovery commit.
- Backfill complete: 2025 (full + playoffs) and 2026 (MP1-5) re-extracted prior to Phase 6.3.3; chunk-1 brought all incremental fcts forward via `dbt build --full-refresh`.
- All dbt tests green: 58 PASS / 0 ERROR / 0 WARN.
- Live league Sheet at `1C_CJ-jAZ-7iE3bz_rR4DmRXARQFh4qb8O6MgjTMKDF0` populated with 17-col Phase 6.3.3 data (3 tabs, 1007 total rows). Pre-Phase-6.3.3 the sheet held 10-col data from Phase 6.3.2; this is a clean replacement.
- Memory updates:
  - `project_phase_plan.md`: Phase 5.0 / 6.2 / 6.3.1 / 6.3.2 / 6.3.3 marked shipped.
  - `project_conventions.md`: mart-vs-helper boundary, threshold-at-output, counts-not-points, polarity-aware effective polarity, INLINE_COLLAPSE_THRESHOLD pattern, format_week_label threading captured.
  - `feedback_test_running_side_effects.md` updated: bash `unset` doesn't suppress (same reason PowerShell `=''` doesn't); use `SHEETS_OUTPUT_ID=DRY_RUN` to suppress.

To resume in a fresh conversation:

> "Reading the project memory and Phase 6.3.3 docs. Phase 6.3.3 is shipped: 8 new tracked stats end-to-end, mart additions for derived/rate/wasted-points stats, records.py orchestrator with bulk contributors and tie-collapse, sheets writer with 17-col 3-tab schema and polarity-aware Best/Worst labels, playoff round naming threaded through every consumer, and league_notes.py registry-pattern callout module. Starting Phase 7 — v1.0 portfolio prep (CHANGELOG, README rewrite, dbt docs polish, ROADMAP, version tag)."

---

## Git History (commits expected through Phase 6.3.3)

After commit, history will include:

- (Phase 1.0 through 5.0, see prior phase docs)
- Phase 6.2: `output/records.py` extraction (commit `8cc3f53`).
- Phase 6.3.1 + 6.3.2 + 6.3.3a: Sheets integration foundation + leaderboard rename (commit `3c1c883`).
- Phase 6.3.3 Handoff brief (commit `49a20cb`).
- Phase 6.3.3b: tracked-stats expansion + mart additions + records orchestrator + tie-collapse + sheets 17-col + playoff naming + league_notes (this commit).
- Phase 6.3.3 Documentation (this commit OR follow-up).
