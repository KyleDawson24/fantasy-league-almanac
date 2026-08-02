# Phase 5.0 Handoff — ESPN Fantasy Baseball Front Page Generator

## What Changed Since Phase 4.0

Phase 5 is a "first final" cut: the output the league commissioner posts each week is now structurally complete. Six themes shipped, all interrelated:

1. **Records re-anchored on `calculated_points`** instead of `platform_points`. Records now reflect "what would this score under today's rules" rather than whatever scoring weights were live at the time. Both lenses remain available in the leaderboard for cross-season comparison.
2. **Shared player-stat-line formatter module** (`output/formatters.py`) — Top Hitter / Top Pitcher / new Top Scorer callouts use a unified renderer that picks the player's top-N positive point-contributing stats. Reused by the records report and (now) the new-record callouts.
3. **New-record callouts in the weekly recap** — when a player or team breaks (or ties) an all-time record in the recapped matchup_period, it's called out with prior-record context and contributors. Polarity-aware filter trims noise (e.g., negative-stat "fewest" records, floor-at-zero ties).
4. **`eligibleSlots` in wasted-performances display** — players now show their full multi-position eligibility (Sanoja → "2B/RP", Brice Matthews → "2B/LF/CF") instead of just a primary position. Required an extract-side fix because kona returns empty `eligibleSlots` for rostered players.
5. **Negative-active-as-waste** — a player who scored net-negative active points is doubly wasteful (could have benched them for 0). The wasted-points formula now adds `abs(min(0, active_pts))` and the formatter calls it out as a "doubly wasted" component.
6. **Output restructure** — `Matchup #N` → `Week N` everywhere; current-season records drop the year (it's implied); both records sections gain Top Scorer / Top Hitter / Top Pitcher player rows; Top Wasted Performances moved into the Matchup Recap; Additional Notes header gates LeagueNote.txt content.

Plus a foundational data-layer add that runs through all six: **`team_abbrev` plumbed end-to-end** from extract through staging/intermediate/marts so all callouts can use compact team labels (`Player (BP)`, `Big Smell City Smelly Boys (SMEL)`).

The output is now shippable as the weekly commissioner update without further intervention. Phase 6 (Google Sheets sink) and Phase 7 (v1.0 portfolio prep) are sequenced from this point.

**Concrete validation cases from the most recent run (2026 Week 5):**
- Player records firing in both records sections: All-Time Top Scorer = Zack Wheeler 66.7 pts (2025 Week 15); Current-Season Top Scorer = Jose Soriano 54.7 pts (Week 2).
- A new record fired in the recap: `New Worst Team Total Points: Atomic Alpaca Armada, 127.0 pts` with prior-record context (Blake Landry GPGP, 135.9 pts, Week 3 of 2025).
- Polarity filter at work: 6 floor-at-zero "Tied Fewest X" records (HBP, QS, SV, HLD, CG, 3B) suppressed automatically.
- Multi-position eligibility shown: `Brice Matthews (Hou, 2B/LF/CF)` in wasted-performances.
- Doubly-wasted detected (just below cutoff at position 25): `Michael Busch (ChC, 1B) -- ... -- 17.0+0.9 waste pts (17.0 benched, 0.9 doubly wasted)`.

---

## Project Structure (Current)

```
espn-league-manager/
├── extract/
│   ├── extract.py                    # MODIFIED: pulls eligibleSlots from
│   │                                 #   wrapper Player (kona returns empty
│   │                                 #   for rostered); team_abbrev added
│   │                                 #   to matchup dict
│   └── dump_stats_map.py             # unchanged
├── output/
│   ├── formatters.py                 # NEW: shared player-stat-line renderers,
│   │                                 #   STAT_DISPLAY/STAT_ABBREV maps,
│   │                                 #   filter_eligible_slots,
│   │                                 #   format_contributors, fmt_value/avg/ip
│   ├── generate_summary.py           # MODIFIED: top-scorer added; recap
│   │                                 #   restructured (Week labels, Wasted
│   │                                 #   moved earlier, new-record section,
│   │                                 #   Additional Notes gating); records
│   │                                 #   sections gain player records and drop
│   │                                 #   year for current-season
│   ├── generate_records_report.py    # MODIFIED: STAT_ORDER swaps
│   │                                 #   PLATFORM_*→CALCULATED_*; STAT_DISPLAY
│   │                                 #   moved to formatters.py;
│   │                                 #   format_contributors imported
│   ├── LeagueNote.txt                # gitignored
│   └── logs/
└── dbt_league/
    ├── dbt_project.yml               # unchanged
    ├── macros/
    │   └── rate_stats.sql
    ├── seeds/                        # unchanged
    └── models/
        ├── staging/
        │   ├── sources.yml
        │   ├── stg_box_scores.sql               # MODIFIED: team_abbrev +
        │   │                                    #   eligible_slots (VARIANT)
        │   ├── stg_player_stat_breakdowns.sql   # MODIFIED: team_abbrev
        │   │                                    #   propagation
        │   ├── stg_scoring_settings.sql
        │   └── schema.yml                       # MODIFIED: docs for new cols
        ├── intermediate/
        │   ├── int_team_daily_scores.sql
        │   ├── int_player_daily_scores.sql
        │   ├── int_player_daily_stats.sql       # MODIFIED: team_abbrev
        │   │                                    #   propagation
        │   ├── int_player_weekly_performance.sql # MODIFIED: team_abbrev
        │   │                                    #   propagation
        │   └── schema.yml                       # MODIFIED: docs
        └── marts/
            ├── fct_weekly_player_scores.sql
            ├── fct_weekly_player_performance.sql # MODIFIED: team_abbrev
            ├── fct_weekly_team_performance.sql   # MODIFIED: team_abbrev
            ├── mart_stat_leaderboard.sql        # MODIFIED: calculated_*
            │                                    #   added to UNPIVOT;
            │                                    #   record_direction column
            │                                    #   (best | worst); 4 ranked
            │                                    #   CTEs (best/worst x
            │                                    #   all_time/current_season)
            ├── mart_wasted_points.sql            # unchanged (math lives at
            │                                    #   the consumer query)
            └── schema.yml                       # MODIFIED: docs for
                                                 #   record_direction +
                                                 #   team_abbrev on facts
```

---

## What Was Built in Phase 5

### Extraction (`extract/extract.py`)

**Fix**: `eligibleSlots` for rostered players. The kona endpoint (`view=kona_player_info`) returns an empty `eligibleSlots` array for rostered players — it only populates the field for free agents. Phase 4 documented "extract already plumbs the data" but that was only true for FAs. Switched the rostered branch to read from the wrapper Player object: `getattr(player, 'eligibleSlots', []) or []`. FAs continue to source from kona where it IS populated.

**Add**: `team_abbrev` extracted from `matchup.home_team.team_abbrev` / `matchup.away_team.team_abbrev` (wrapper Team object exposes it). Lands in `matchup_dict` as `home_team_abbrev` / `away_team_abbrev`. Used downstream for compact team labels in callouts.

### Staging

`stg_box_scores.sql`:
- New `team_abbrev` column (string, NULL for FA rows by construction).
- New `eligible_slots` column (VARIANT array of slot strings — e.g., `['LF', 'OF', 'DH', 'UTIL', 'BE', 'IL']` — direct passthrough from raw).

`stg_player_stat_breakdowns.sql`:
- `team_abbrev` propagated through.

### Intermediate

`int_player_daily_stats.sql`, `int_player_weekly_performance.sql`:
- `team_abbrev` propagated through. Added to GROUP BY in `int_player_weekly_performance` (constant per team_id within a matchup, doesn't change row counts).

### Marts

`fct_weekly_player_performance.sql`, `fct_weekly_team_performance.sql`:
- `team_abbrev` surfaced. Team grain gets it via the rollup GROUP BY.

`mart_stat_leaderboard.sql`:
- **`calculated_points` / `calculated_hitting_pts` / `calculated_pitching_pts` added to the UNPIVOT lists** at both team and player grain. Both `PLATFORM_*` and `CALCULATED_*` stat_names now coexist in the leaderboard so consumers can choose which lens to rank by.
- **`record_direction` dimension added** ('best' | 'worst'). The mart now emits both top-10 (rank by `stat_value DESC`) AND bottom-10 (rank by `stat_value ASC`) per (entity_grain, stat_name, record_scope). Four ranked CTEs in total: best/worst × all_time/current_season. Recency tiebreak applies in both directions, so a team that just tied a record is rank 1 either way.
- `team_abbrev` carried through unpivots so leaderboard rows expose it directly.

`mart_wasted_points.sql`:
- Unchanged. The negative-active-as-waste math lives at the consumer query (Phase 5.5 below) so the mart stays focused on inactive contributions only.

### Output (`output/formatters.py` — NEW)

A shared module of pure-function utilities consumed by both `generate_summary.py` and `generate_records_report.py`:

- **`STAT_DISPLAY`** dict (full names like `'B_SO': 'Strikeouts (Batter)'`) — moved from `generate_records_report.py`.
- **`STAT_ABBREV`** dict (short forms for inline display: `'B_SO': 'K'`, `'OUTS': 'IP'`, etc.).
- **`HITTER_STAT_DISPLAY` / `PITCHER_STAT_DISPLAY` / `TOP_SCORER_STAT_DISPLAY`** — eligible stats per renderer, with declared order as stable tiebreak.
- **`fmt_value(v)`**: integer for whole numbers, 1 decimal for floats. NULL → 0.
- **`fmt_avg(x)`**: baseball rate format (`.350`, not `0.350`). NULL → `.000`.
- **`fmt_ip(outs)`**: outs → IP in baseball notation (7 outs → `2.1`, not `2.333`).
- **`format_contributors(contributors, max_n=3)`**: parameterized top-N stat-contributor list with tie-handling and zero-tail. Handles "N others with X" overflow when a tie group would push past max_n. Default max_n=3 preserves the records report's existing call sites; new-record callouts pass max_n=5.
- **`filter_eligible_slots(slots)`**: trims an eligibleSlots array to specific positions for display. Drops BE/IL/UTIL/IF/FA and slash-style flex slots (`'2B/SS'`, `'OF/DH'`); collapses generic OF when LF/CF/RF present and generic P when SP/RP present. Preserves source order.
- **`format_hitter_stats_line(player, top_n=2, positives_only=True)`**: `.AVG/.OBP/.SLG -- AB, [top-N counts]`.
- **`format_pitcher_stats_line(player, top_n=2, positives_only=True)`**: `[W-L,] [SV,] ERA, WHIP -- IP, [top-N counts]`.
- **`format_top_scorer_stats_line(player, top_n=5)`**: top-N stats across both pools with no prefix; OUTS auto-converted to IP for display.

The `positives_only` flag (default True) controls whether `_top_n_stats` ranks by signed `*_pts` desc (positive contributions only) or by absolute value (both directions). Default is True for celebratory callouts; can flip for future "worst player" contexts.

### Output (`generate_summary.py`)

**Recap section restructure**:
- Header changed from `[u][b]Matchup #N Recap[/b][/u]` to `[u][b]Week N Recap[/b][/u]`.
- Player superlatives expanded from 2 callouts (Top Hitter / Top Pitcher) to 3 (Top Scorer / Top Hitter / Top Pitcher). Top Scorer is `find_top_scorer(players)` — max(platform_points). Always rendered alongside the category leaders even when redundant; the conditional-skip-when-redundant is queued in backlog.
- New player-callout shape: `Player (TeamAbbr), X.X pts -- {stats}` (was `X.X pts by Player (Full Team Name) -- {stats}`).
- Top Wasted Performances moved earlier in the render order — now appears at the end of the Matchup Recap section, before New Records.

**New Records section** (Phase 5.3):
- `find_new_records(season_year, matchup_period)` queries `mart_stat_leaderboard` for rank-1 rows whose holder is the just-recapped MP. Applies polarity filter (see Key Technical Decisions §6) and tie detection. Returns list of broken/tied record dicts.
- `count_value_occurrences(grain, stat_name, value)` queries the fct directly to count tie population for "Nth team" framing.
- `_format_player_score_record`, `_format_team_record`, `_format_tied_record` render per-record blocks.
- `format_new_records_section` handles the section header and skips entirely when no records fired.
- Section sits between Top Wasted Performances and the Tough Luck callouts.

**Records sections expansion**:
- `format_records(records, season_only)` gains `season_only` parameter. `Week N` for current-season (year dropped); `YYYY Week N` for all-time.
- `Matchup #N` → `Week N` in the value rendering.
- New `get_player_records()` — single leaderboard query for rank-1 player rows in best direction, both scopes. New `format_player_records(rows, season_only)` keys by stat and produces 3 formatted strings (Top Scorer / Top Hitter / Top Pitcher).
- Both Current Season Records and All-Time League Records sections grow from 6 lines (team) to 9 lines (6 team + 3 player).
- Player record format: `Player (TeamAbbr, OwnerName) -- X.X pts, [YYYY] Week N`.

**Wasted Performances** (Phase 5.4 + 5.5):
- `get_wasted_points` query joins `eligible_slots` from a `player_meta` CTE (most-recent scoring-period snapshot per player to handle mid-period trades).
- New `doubly_wasted_pts` column = `GREATEST(0, -COALESCE(active_points, 0))`; added to the `wasted_points` total and incorporated into ORDER BY so the top-N reflects the new formula.
- `format_wasted_points`:
  - Position display switched from primary `position` to filtered `eligible_slots` joined with `/` (e.g., `(Hou, 2B/LF/CF)`). Falls back to primary `position` when `eligible_slots` is empty.
  - Headline shape extends to `X+Y+Z waste pts` when 3 components fire (was `X+Y waste pts` for 2 components, single `X.X pts` for 1).
  - Breakdown parenthetical adds `{N} doubly wasted` for negative-active contributions.

**Additional Notes**:
- LeagueNote.txt content now wrapped in an `[u][b]Additional Notes[/b][/u]` header. When the file is missing or empty, the header AND the content are skipped (no naked header).

### Output (`generate_records_report.py`)

- `STAT_ORDER` and `STAT_DISPLAY` PLATFORM_* entries swapped for CALCULATED_* equivalents.
- `STAT_DISPLAY` dict moved to `formatters.py` (now imported).
- `fmt_value` and `format_contributors` moved to `formatters.py` (imported).
- Leaderboard reads now filter `record_direction = 'best'` explicitly (the leaderboard now has both directions; this script only consumes 'best').

---

## Key Technical Decisions

### 1. Records flip to `calculated_points` (not `platform_points`)

Records now reflect "what this stat line scores under current rules" rather than "what was tallied under whatever rules were live at the time." For cross-season comparison this is meaningful: a 2025 week scored under 2025 weights might rank #1 by raw `platform_points` but tell us nothing about how that performance would translate today. `calculated_points` normalizes everything to the current scoring config.

The leaderboard mart adds `CALCULATED_*` stat_names alongside the existing `PLATFORM_*` (the unpivot is already long-format, so it's three more rows per (grain, scope, direction)). Consumer scripts choose which lens to surface. `generate_records_report.py` and the records sections of `generate_summary.py` were both pointed at `calculated_*`. Platform_* remains available for any future "what did ESPN's official tally say" lookup.

Bounded scope (per user spec): the recap section's best/worst Overall/Hitting/Pitching team callouts and Top Hitter/Pitcher still source from `platform_*` because the recap is about what happened (and W/L outcomes are platform-determined). Only the records sections (current-season and all-time) flipped.

### 2. Shared player-stat-line formatter — `output/formatters.py`

Phase 4 had stat-line rendering inlined in `generate_summary.py`'s `format_hitter_line` / `format_pitcher_line`. Phase 5 needed the same renderer for new-record callouts (player records) and the records sections (Top Scorer/Hitter/Pitcher). Rather than duplicating, the renderer was extracted to a module both scripts import.

The module also became the natural home for `STAT_DISPLAY` (full names for record headings) and `STAT_ABBREV` (short forms for inline display), which were previously sole-source in `generate_records_report.py`. Moving them out of script-locals makes both scripts importable consumers.

The `positives_only` parameter (default True) on the rendering functions came from a real-data observation: ranking stats by `|*_pts|` (absolute value) for celebratory Top Hitter callouts surfaced strikeouts — a hitter with 5 HR (+20 pts) and 7 K (-7 pts) would show "5 HR, 7 K" because |7| > |6 RBI|. Switching to signed-pts-desc (positive only) for "best" callouts shows "5 HR, 6 RBI" instead. The flag preserves the option to flip for future "worst performance" use cases.

### 3. New-record callouts via leaderboard recency tiebreak

Detection turned out to be elegant: query `mart_stat_leaderboard` for rank=1 rows where (season_year, matchup_period) match the just-recapped MP. The leaderboard's secondary sort by recency means the current MP gets rank 1 when it ties or beats the prior best — so finding rank 1 == current MP is sufficient. Rank 2 of the same partition is the prior record (giving us prior-holder context for "Prior: 38.4 pts by Player X (XYZ) in Week 12 of 2025"). No separate snapshot table needed.

### 4. `mart_stat_leaderboard.record_direction` extension

The leaderboard previously only emitted top-10 (DESC). Phase 5 added bottom-10 (ASC) for "worst" records detection, parameterized by a new `record_direction` column ('best' | 'worst'). Implementation is four ranked CTEs (best/worst × all_time/current_season) UNION'd, each with its own `row_number()` window. View materialization keeps storage at zero.

This is a single source of truth for both directions across all stats and grains. The records report explicitly filters `record_direction = 'best'` (it only surfaces top records); the new-record detection consumes both.

### 5. Polarity-aware filter rules for new-record detection

Not all (grain, stat, direction) combinations should fire as records. The user's filter rules:

| Grain | Stat type | Best (Most) | Worst (Least) |
|---|---|---|---|
| Team | Score (calc_*) | Yes | Yes |
| Team | Positively-weighted individual stat | Yes | Yes |
| Team | Negatively-weighted individual stat | Yes | **No** |
| Player | Score (calc_*) | Yes | **No** |
| Player | Individual stat | **No** | **No** |

Reasoning:
- "Least of negatives" = "fewest strikeouts at the plate" type records would mostly reward managers who left their lineup half-empty. Skipped.
- "Most of negatives" = "most ER allowed" — fun to call out as a notable bad-outing record, doesn't reward poor management.
- Player individual stats — the leaderboard's bottom-10 for player grain at most stats is a sea of zeros (anyone who didn't play). Not meaningful records.

Polarity is derived from `sign(points_per_unit)` in `stg_scoring_settings`. Stats not in the seed (e.g., `H` and `TB` are zero-weighted in this league since the seed scores 1B/2B/3B/HR individually) are 'neutral' and skipped.

**Watch-out**: the seed uses `1B`/`2B`/`3B` but the wide fct + leaderboard call those columns `SINGLES`/`DOUBLES`/`TRIPLES`. A `_SEED_TO_LEADERBOARD` translation dict in `get_stat_polarity()` maps between them so polarity lookups land. This kind of name mismatch would have silently filtered out singles/doubles/triples records as "neutral" if not caught.

### 6. Tied-record floor-noise filter

Allowing ties was the right call for v1 — strict-break-only would have missed perfect-game style records (e.g., team A becomes the 3rd team to record 5 QS in a single week). But every-stat ties at value=0 (situational pitching stats like CG/HLD/SV that are 0 most weeks for most teams) flooded the output with noise: "Tied Fewest Complete Games: Ghosts of Polo Grounds Past became the **420th team** to record 0 CG in a single week."

Filter: skip tied records at value=0 for individual stats. The natural floor (count can't go negative) means these are never strict breaks, so the filter only affects ties. Score-column floats can theoretically tie at 0 but practically don't.

This removed all 6 noise callouts from the most recent test week and left only the meaningful new record (the team total worst).

### 7. `eligibleSlots` from wrapper, not kona, for rostered players

Phase 4 added `eligibleSlots` to the extract payload via the kona endpoint. Phase 5 discovered (when implementing the wasted-performances display) that kona returns an empty array for rostered players — it only populates the field for FAs. The wrapper's `Player` object has `eligibleSlots` populated for everyone. Switched the rostered branch to read from wrapper; FA branch still uses kona's value. Required a re-extract of 2025 + 2026 to backfill rostered players' eligibleSlots.

The display filter (`filter_eligible_slots`) drops BE/IL/UTIL/IF/FA and slash-style flex shapes (`'2B/SS'` is a lineup slot, not a position eligibility). Collapses generic OF when LF/CF/RF specifically present and generic P when SP/RP present. Result: `['LF', 'OF', 'DH', 'UTIL', 'BE', 'IL']` → `['LF', 'DH']`; `['SS', '2B/SS', 'IF', 'UTIL', 'BE', 'IL']` → `['SS']`.

### 8. Negative-active-as-waste at the consumer query, not the mart

The Phase 4 wasted-points concept covered FA + bench points (production from inactive lineup spots). Phase 5 extended: a player who scored net-negative active points is doubly wasteful (could have benched them for 0 instead of taking the loss). New formula: `wasted_total = fa_pts + bench_pts + abs(min(0, active_pts))`.

Implementation lives at the `get_wasted_points` consumer query rather than the mart. The mart aggregates inactive contributions (FA + bench); active points come from `fct_weekly_player_performance` which the consumer already joins. Adding a `GREATEST(0, -active_pts)` to the SELECT and bumping the `wasted_points` total + ORDER BY to include it gives the new formula without touching `mart_wasted_points`. The mart stays focused on inactive contributions; the "doubly wasted" semantics live at the consumer surface.

The formatter renders a 3rd additive component when present: `17.0+0.9 waste pts (17.0 benched, 0.9 doubly wasted)`. Positive-active still shows as context (informative — they did contribute on other days) but doesn't add to the waste total.

### 9. `team_abbrev` plumbed end-to-end

The new player-callout shape (`Player (TeamAbbr), X.X pts -- ...`) needed compact team labels. ESPN's wrapper Team object has `team_abbrev` (e.g., `'SMEL'` for "Big Smell City Smelly Boys"); the extract didn't capture it pre-Phase-5.

Decision: pull through the data layer rather than maintain a manual seed. Three-line extract change + propagation through staging/intermediate/marts (added to GROUP BY in the rollup CTEs since it's constant per team_id, doesn't change row counts). One full re-extract + dbt full-refresh backfilled both 2025 and 2026.

The alternative (manual `team_abbreviations.csv` seed) would have given more display control but introduced a maintenance burden when teams join/leave. Going with ESPN's owner-set abbrev keeps the data layer self-maintaining.

### 10. Output structure: Top Wasted in Recap, Fair/Luck/Just at end of recap, no header

The user's final-form structure has Top Wasted Performances as a sub-element of the Matchup Recap (it's a week-level review concept), not its own top-level section. New Records, Tough Luck / Lucky Bastard / Fair-and-Just callouts live between the recap and the records sections. The luck callouts don't get a wrapping section header — they're free-floating callouts that only render when their conditions are met.

Section ordering:
```
[u][b]Week N Recap[/b][/u]
  best/worst team callouts (with contributors)
  Top Scorer / Top Hitter / Top Pitcher
  Top 5 Wasted Performances

[u][b]New Records[/b][/u]    (skipped entirely if no records broken)

  Tough Luck / Lucky Bastard / Fair-and-Just  (each only if true)

[u][b]Current Season Records[/b][/u]    (9 lines: 6 team + 3 player)

[u][b]All-Time League Records[/b][/u]    (9 lines: 6 team + 3 player)

[u][b]Additional Notes[/b][/u]    (only when LeagueNote.txt has content)
```

---

## What's in Snowflake (Current)

- **Database**: `ESPN_FANTASY`
- **Raw schema**: `RAW`
  - `BOX_SCORES` — `raw_json` shape now includes `home_team_abbrev`/`away_team_abbrev` per matchup AND populated `eligibleSlots` per player (rostered or FA). Pre-Phase-5 rows had empty eligibleSlots for rostered; Phase 5 re-extract populated them.
  - `SCORING_SETTINGS` — unchanged.
- **Analytics schema**: `ANALYTICS`
  - `STG_BOX_SCORES` — new columns: `team_abbrev`, `eligible_slots` (VARIANT).
  - `STG_PLAYER_STAT_BREAKDOWNS` — new column: `team_abbrev`.
  - `INT_PLAYER_DAILY_STATS`, `INT_PLAYER_WEEKLY_PERFORMANCE` — `team_abbrev` propagated.
  - `FCT_WEEKLY_PLAYER_PERFORMANCE`, `FCT_WEEKLY_TEAM_PERFORMANCE` — `team_abbrev` exposed.
  - `MART_STAT_LEADERBOARD` — `record_direction` column added; `CALCULATED_POINTS`/`CALCULATED_HITTING_PTS`/`CALCULATED_PITCHING_PTS` stat_names added; `team_abbrev` carried through. Roughly 4x as many rows as Phase 4 (best/worst × all_time/current_season; 10 each).
  - `MART_WASTED_POINTS` — unchanged.
  - All facts rebuilt from scratch via `dbt build --full-refresh` post Phase 5 backfill.
  - 2025 (full season) and 2026 (MP1–5) covered.

---

## Verification

End-to-end validation across the most recent run (2026 Week 5):

```sql
-- Records on calculated_*: leaderboard exposes both lenses, no regressions on platform side
SELECT entity_grain, stat_name, COUNT(*) AS n
FROM mart_stat_leaderboard
WHERE stat_name LIKE 'PLATFORM%' OR stat_name LIKE 'CALCULATED%'
GROUP BY 1, 2
ORDER BY 1, 2;
-- Returns 12 (entity_grain, stat_name) groups with 40 rows each
-- (10 ranks × 2 directions × 2 scopes), confirming both lenses present.

-- Player-grain rank-1 records exist for all 6 (stat × scope) combos:
SELECT stat_name, record_scope, display_name, season_year, matchup_period, ROUND(stat_value, 1) AS val
FROM mart_stat_leaderboard
WHERE entity_grain = 'player'
  AND stat_name IN ('CALCULATED_POINTS','CALCULATED_HITTING_PTS','CALCULATED_PITCHING_PTS')
  AND record_direction = 'best' AND rank = 1;
-- All-time top scorer/hitter/pitcher = Wheeler 66.7 / Kurtz 56.5 / Wheeler 66.7
-- Current-season top scorer/hitter/pitcher = Soriano 54.7 / Wood 47.7 / Soriano 54.7

-- Polarity filter at work in new-record detection:
-- 6 floor-zero ties suppressed (HBP, QS, SV, HLD, CG, 3B), 1 meaningful record fired
-- (New Worst Team Total Points: Atomic Alpaca Armada 127.0).

-- eligibleSlots populated for rostered post-fix:
SELECT player_name, eligible_slots
FROM stg_box_scores
WHERE season_year = 2026 AND scoring_period = 33 AND lineup_slot != 'FA'
LIMIT 5;
-- All non-empty arrays (e.g., ['RF', 'OF', 'UTIL', 'BE', 'IL']).
```

dbt build (full-refresh): **58 PASS / 0 ERROR / 0 WARN**.

Output script: full weekly summary renders cleanly end-to-end with all sections in correct order.

---

## Open Investigations Carried Forward

None. All Phase 5 work converged cleanly.

---

## Bookmarks for Future Work

### Phase 6.2 — Records module extraction (next)

Pull records-fetching logic out of `generate_summary.py` and `generate_records_report.py` into a single `output/records.py` module. Pure data-access functions: `get_all_time_records()`, `get_current_season_records()`, `get_records_set_this_week(season, matchup_period)`. Refactor both consumer scripts to use it.

This is a refactor-for-cleanliness move: currently records data access is split across two scripts and three call patterns (direct fct query in `generate_summary.format_records`; leaderboard query in `generate_records_report.get_record_holders`; new-record detection in `generate_summary.find_new_records`). Consolidating into one module simplifies and makes Phase 6.3 (Sheets writer) trivial to wire up.

Includes: migrate the team records pattern from direct fct queries to `mart_stat_leaderboard` reads (single source of truth across team and player records).

### Phase 6.3 — Google Sheets integration

Records output to a Google Sheet, archived weekly so the ESPN frontpage isn't the only system of record. Stack: `gspread` + `google-auth-oauthlib` for OAuth user-flow. One-time GCP project setup (Sheets API + Drive API enabled, OAuth consent screen, OAuth client). Two tabs initially: "All-Time Records" and "Current Season Records". Idempotent writes (clear and rewrite). Opt-in via `SHEETS_OUTPUT_ID` env var.

Naturally pairs with **log-path canonicalization** — the script currently writes logs to `os.path.dirname(__file__) + "../output/logs"`, which lands inside the worktree when run from a worktree. Should be a single canonical path regardless of execution location.

### Phase 7 — v1.0 portfolio prep

Single weekend's worth of work to ship a polished public release:
- `CHANGELOG.md` (keepachangelog format; map phases retroactively to semver: 0.1.0 = Phase 1 → 1.0.0 = this release).
- dbt docs: fill in description fields across all schema.yml files, add `exposures` for the output scripts, `dbt docs generate` + push `target/` to `gh-pages` branch for hosted lineage graphs.
- `README.md` rewrite as the entry point: 30-second pitch, sample output screenshot, architecture diagram (Mermaid), "notable engineering decisions" linking to phase docs, "what this demonstrates" recruiter-facing section, separate `SETUP.md` for the bring-your-own-credentials path.
- `ROADMAP.md` with Now / Next / Later / Won't Do buckets.
- Repo hygiene: replace stale top-level overview, comprehensive `.gitignore`, pinned `requirements.txt`, MIT or Apache 2.0 LICENSE.
- Tag `v1.0.0`, GitHub Release with changelog as release notes.
- Optional post-release: r/dbt, r/dataengineering, LinkedIn share.

### Backlog (not gating v1.0)

- **Conditional 3rd "Top Scorer" line**: Top Scorer is currently always rendered alongside Top Hitter and Top Pitcher even when redundant (a pure pitcher topping both Top Scorer AND Top Pitcher categories with the same line). Should only render when the overall winner had BOTH non-zero hitting AND pitching contributions (true two-way Ohtani case). The All-Time records section currently shows this redundancy: Top Scorer = Top Pitcher = Zack Wheeler 66.7 pts.
- **Migrate team records to leaderboard** (folded into Phase 6.2 above).
- **Extract performance optimizations**: multi-view single HTTP call (`?view=mMatchupScore&view=kona_player_info`), batched kona via `filterStatsForScoringPeriodIds`, parallel-fire wrapper calls. Backfill currently ~30 min; could drop to ~3-5 min.
- "Record set this week" callouts (was originally Phase 3.1; Phase 5 #3 shipped this).
- Rate-stat leaderboards with PA/AB minimums (avoid 1-AB OPS=3.000 outliers).
- Wire `owner_nicknames` seed into models.
- `fct_team_career_stats` mart.
- Investigate explicit `pointsAdjustment` field in ESPN API to split `platform_calculated_delta` into `commissioner_adjustment` + `derivation_delta`.
- Verify stat ID 30 (15 pts per, only 1 observed row) isn't a real scored stat we're missing.
- Multi-sink output abstraction (defer until 2nd non-Sheets sink emerges).
- GitHub Actions CI on PRs (`dbt test` + Python compile checks).
- Python tests for output formatters.
- Cross-platform adapters (Yahoo, Sleeper, etc.) — staging contract designed with this in mind.

---

## Migration Notes for Next Session

State at end of Phase 5.0:

- Worktree: `exciting-mahavira-d55580` on branch `claude/exciting-mahavira-d55580`. Merged to local `main` at commit `12fe32d`. **Local `main` is 2 commits ahead of `origin/main`** — push pending.
- Backfill complete: 2025 (full) and 2026 (MP1–5) re-extracted with `eligibleSlots` and `team_abbrev` populated. All facts rebuilt via `dbt build --full-refresh`.
- All dbt tests green: 58 PASS / 0 ERROR / 0 WARN.
- dbt docs catalog regenerated to `dbt_league/target/catalog.json`.
- `Phase 5.0 Handoff.md` (the Phase-4-to-5 handoff stub from before this work) deleted as part of this commit.
- Memory files updated: Phase 5 marked done in `project_phase_plan.md` with all six sub-themes captured plus the watch-outs (kona-rostered-eligibleSlots gap, seed-to-leaderboard name mapping for SINGLES/DOUBLES/TRIPLES). Two backlog items added (conditional 3rd Top Scorer line; migrate team records to leaderboard).

To resume in a fresh conversation:

> "Reading the project memory and Phase 5.0 docs. Phase 5.0 is shipped: records on calculated_*, shared formatters with Top Scorer/Hitter/Pitcher reformat, new-record callouts (broken + tied with polarity filter), eligibleSlots in wasted-performances, negative-active-as-waste, output restructure (Week labels, year-drop on current-season, player records added to records sections, Additional Notes header), and team_abbrev plumbed end-to-end. Starting Phase 6.2 — extract records-fetching logic into output/records.py module so Phase 6.3 (Google Sheets writer) has a clean dependency."

---

## Git History (commits expected through Phase 5.0)

After commit, history will include:

- (Phase 1.0 through 4.0, see prior phase docs)
- Phase 5.0: records on calculated_* + new-record callouts + eligible-slots display + negative-active-as-waste + recap/records restructure (commit `1230c03`). Specifically:
  - Extract: `eligibleSlots` from wrapper for rostered (kona returns empty); `team_abbrev` extracted to matchup dict.
  - dbt staging: `team_abbrev` + `eligible_slots` (VARIANT) on `stg_box_scores`; `team_abbrev` on `stg_player_stat_breakdowns`.
  - dbt intermediate: `team_abbrev` propagated through `int_player_daily_stats` and `int_player_weekly_performance`.
  - dbt marts: `team_abbrev` on both fact tables; `mart_stat_leaderboard` adds `calculated_*` to UNPIVOTs and `record_direction` ('best' | 'worst') dimension.
  - Output: new shared `formatters.py` module; `generate_summary.py` recap restructure + records-section player additions + new-record section + Additional Notes gating; `generate_records_report.py` consumes shared formatters and uses calculated_*.
- Merge commit `12fe32d`: Phase 5.0 to main.
- Add Phase 5.0 documentation (this commit).
