# Phase 4.0 Handoff — ESPN Fantasy Baseball Front Page Generator

## What Changed Since Phase 3.3.1

Phase 4 ships **wasted points analysis** (bench, IL, free agent) along with two structural fixes that surfaced during the work:

1. **Free-agent stat extraction** — a new data source (kona_player_info, replacing mRoster as the universal stat endpoint) that returns the full MLB player universe, not just rostered players. FAs are identified by anti-join: any player with stats in kona but absent from the wrapper's box_scores lineup is, by definition, a free agent that day. No transaction-log dependency, no separate pipeline.
2. **Slot-stat-category validity filter** — ESPN's team-level scoring credits a player's stats only when the stat's category matches the slot type (a hitter's hitting stats only count from a hitting slot, etc.). mRoster was returning slot-aware `appliedTotal`; kona returns slot-blind. We now apply slot-validity ourselves at int_player_daily_stats. Toggleable via the new `strict_slot_validity` dbt var.
3. **Team-level `platform_points` sourced direct from the wrapper's `home_score`** instead of summed from player-level platform_points. This is ESPN's authoritative team total — slot-aware and inclusive of any commissioner manual adjustments. Player-level platform_points remains a direct-from-API passthrough (matching the "platform_* = no calculation" principle). The team-vs-player divergence (when slot misuse exists) is exposed as `platform_calculated_delta`.
4. **HBP stat-id collision fix** — the espn-api wrapper's `STATS_MAP` collapsed both stat ID 12 (batter HBP, +1) and 42 (pitcher HBP, -1) under the single name "HBP". The Phase 3.2 disambiguation CASE in int_player_daily_stats patched this for rostered active pitchers but failed for FAs (no role signal in `lineup_slot='FA'`) and for two-way Ohtani days (both stats summed under one name). Fixed at extract via `_STAT_ID_TO_NAME[42] = 'HBP_P'` override; the int CASE is now gone.

The new `mart_wasted_points` (view, two-bucket grain: FA + ROSTERED_INACTIVE) is the foundation for the Top Wasted Performances callout in the weekly summary.

**Concrete validation cases:**
- Hosstros MP1 2026 = **374.25** (matches Phase 3.3.1 baseline → no regression)
- Island Daddys MP1 2026 = **382.42** (matches website; previously inflated to 398.52 by Sanoja's 16.1 hitting points credited from his RP slot)
- Intentional Walk to the Bar MP1 2026 = **482.88** (was 485.18 pre-fix; Ohtani's SP-day hitting line — 1H, 2BB, 1K from a day he also pitched 6 IP — correctly dropped from team total)

`platform_calculated_delta = 0` across all 14 teams for current-season data, confirming the slot-validity filter and current-weights derivation cleanly reproduce ESPN's scoring.

---

## Project Structure (Current)

```
espn-league-manager/
├── extract/
│   ├── extract.py                    # MODIFIED: kona endpoint, FA emission,
│   │                                 #   HBP_P override, eligibleSlots,
│   │                                 #   restructured logline
│   └── dump_stats_map.py             # unchanged
├── output/
│   ├── generate_summary.py           # MODIFIED: Top Wasted Performances section
│   ├── generate_records_report.py    # unchanged
│   ├── LeagueNote.txt                # gitignored
│   └── logs/
└── dbt_league/
    ├── dbt_project.yml               # MODIFIED: strict_slot_validity var
    ├── macros/
    │   └── rate_stats.sql
    ├── seeds/                        # unchanged
    └── models/
        ├── staging/
        │   ├── sources.yml
        │   ├── stg_box_scores.sql               # MODIFIED: dict-shape raw JSON,
        │   │                                    #   FA rows, lineup_slot_category,
        │   │                                    #   nullable team fields
        │   ├── stg_player_stat_breakdowns.sql   # MODIFIED: lineup_slot_category
        │   │                                    #   passthrough
        │   ├── stg_scoring_settings.sql
        │   └── schema.yml                       # MODIFIED: column docs, FA-aware
        │                                        #   tests, accepted_values for
        │                                        #   lineup_slot_category
        ├── intermediate/
        │   ├── int_team_daily_scores.sql        # MODIFIED: lineup_slot_category
        │   │                                    #   filter (FA-aware)
        │   ├── int_player_daily_scores.sql      # MODIFIED: same filter switch
        │   ├── int_player_daily_stats.sql       # MODIFIED: slot-validity filter
        │   │                                    #   w/ var toggle, removed HBP_P CASE
        │   ├── int_player_weekly_performance.sql
        │   └── schema.yml                       # MODIFIED: column docs
        └── marts/
            ├── fct_weekly_player_scores.sql
            ├── fct_weekly_player_performance.sql
            ├── fct_weekly_team_performance.sql  # MODIFIED: wrapper-direct
            │                                    #   platform_points + delta column
            ├── mart_stat_leaderboard.sql
            ├── mart_wasted_points.sql           # NEW: view, FA + ROSTERED_INACTIVE
            └── schema.yml                       # MODIFIED: docs for new mart +
                                                 #   updated team perf description
```

---

## What Was Built in Phase 4

### Extraction (`extract/extract.py`)

**New helpers**:
- `_STAT_ID_TO_NAME[42] = 'HBP_P'` override + `_log_stats_map_collisions()` runs at import. Surfaces any future espn-api STATS_MAP collisions as `[warn]` log lines (silent when none, which is the current state — HBP was the only one).
- `DEFAULT_POSITION_MAP` (11 entries, primary positions) and `LINEUP_SLOT_MAP` (38 entries, full lineup slot space). Loaded via defensive `getattr` from `espn_api.baseball.constant`. Used to translate kona's numeric IDs to readable strings.
- `fetch_all_player_stats(year, scoring_period)` — replaces `fetch_raw_player_stats`. Hits `view=kona_player_info` with `x-fantasy-filter: {limit: 1500, sortPercOwned}`, returns the full MLB universe with stats per player. Same `(statSplitTypeId == 5 AND scoringPeriodId == target)` sum-across-splits aggregation as the Phase 3.3.1 mRoster path; just sources from a superset endpoint.

**Modified**:
- `serialize_box_scores` — calls kona once per scoring_period, walks wrapper lineups for matchup structure (rostered_ids tracked), then anti-joins kona players against rostered_ids to emit FA rows. Returns `{"matchups": [...], "free_agents": [...]}` instead of bare matchups array.
- Player rows now carry `eligibleSlots` (list of readable slot names from kona). Plumbed through raw for the v2 follow-up where the output will display multi-position eligibility.
- Logline reformatted: `played in MLB: N (X rostered, Y FA) over Z games | X tracked by kona | fallbacks: K` (last segment only when non-zero).

### Staging

`stg_box_scores.sql`:
- Handles new dict-shape raw JSON via `coalesce(raw_json:matchups, raw_json)` — defends against the legacy bare-array shape if any pre-Phase-4 raw rows persist.
- New `free_agents` CTE flattens the FA array; UNION ALL with home/away player CTEs.
- FA rows have NULL `team_id`, `team_name`, `owner_name`, `home_away` (no fantasy team owns them).
- New `lineup_slot_category` column: derived three-value bucket — `'pitching'` (SP/RP/P), `'hitting'` (any other active slot), `'inactive'` (BE/IL/FA). Single derivation, consumed across multiple downstream models.

`stg_player_stat_breakdowns.sql`:
- Passes `lineup_slot_category` through.

`schema.yml`:
- `stg_box_scores`: nullable team fields documented; `lineup_slot_category` added with `accepted_values: ['pitching', 'hitting', 'inactive']` test; unique combination test broadened from `(season, player, sp, team)` to `(season, player, sp)` since a player can't simultaneously be on two rosters or be both rostered and FA.
- `stg_player_stat_breakdowns`: `team_id` no longer `not_null` (FA rows); unique combination test similarly broadened; `lineup_slot_category` documented; relationships test on `stat_name → stat_classification` migrated to the newer `arguments:` syntax.

### Intermediate

`int_player_daily_stats.sql`:
- HBP_P disambiguation CASE removed (extract-time override handles it).
- New slot-stat-category compatibility filter:
  ```
  where c.is_counting = true
    {% if var('strict_slot_validity', true) %}
    and (
      c.stat_category = d.lineup_slot_category
      or c.stat_category = 'fielding'
      or d.lineup_slot_category = 'inactive'
    )
    {% endif %}
  ```
  Inactive rows (BE/IL/FA) bypass the filter so wasted-points downstream sees full stat lines. Fielding-category stats pass through regardless (position-agnostic in scoring).

`int_team_daily_scores.sql` and `int_player_daily_scores.sql`:
- Filter switched from explicit slot enumeration (`lineup_slot not in ('BE', 'IL')`) to `lineup_slot_category != 'inactive'`. Pre-Phase-4 the old filter was sufficient because no FA rows existed in stg_box_scores; with FAs now flowing through, the new bucket-based filter cleanly excludes BE/IL/FA in one expression.

### Marts

`fct_weekly_team_performance.sql`:
- Team-level `platform_points` now sourced directly from the wrapper's `home_score`/`away_score` at the LAST scoring_period of each matchup_period (cumulative-through-SP semantics). New CTEs `last_sp_per_matchup` + `last_sp_matchups` + `team_platform_scores` do the lookup; `team_with_platform` joins it onto the player rollup.
- New columns:
  - `platform_calculated_delta` = `platform_points - calculated_points`. Captures all sources of platform-vs-calculated divergence: slot validity, current-vs-historical weight differences, commissioner adjustments. Documented as a known caveat that the column conflates these for now.
  - `player_rollup_platform_points` = SUM of `platform_hitting_pts + platform_pitching_pts` across active players. The old definition of `platform_points`. Surfaced for diagnostic comparison.
- `platform_hitting_pts` / `platform_pitching_pts` remain player rollups (the wrapper provides only a single team total, no breakdown). Rollups may not sum to `platform_points` for teams with slot misuse; documented in column descriptions.
- `matchup_pairs` CTE now uses `coalesce(raw_json:matchups, raw_json)` for shape compatibility.

`mart_wasted_points.sql` (NEW):
- View materialization (small dataset, deterministic from inputs, always-fresh on every dbt run).
- Reads `int_player_daily_stats where lineup_slot_category = 'inactive'` (the inverse filter).
- Two-bucket grain via CASE on `lineup_slot`: `'FA'` for free-agent days, `'ROSTERED_INACTIVE'` for bench (BE) and injured-list (IL) collapsed together (substantively equivalent for wasted-points analysis: rostered-but-didn't-contribute).
- Aggregates daily → weekly: total `wasted_points`, `wasted_hitting_pts`, `wasted_pitching_pts`, `days_in_bucket`. NULL team fields for FA bucket; team_name / team_id carried through for ROSTERED_INACTIVE.
- A player who switched buckets within a matchup_period (e.g., dropped from bench mid-week) produces multiple rows — distinguishable for transition-story analyses; collapsed at query time when the consumer wants a per-player total.
- Grain: `(season_year, matchup_period, player_id, wasted_bucket)`.

### dbt project config

`dbt_project.yml`:
- Declared `vars: { strict_slot_validity: true }` with explanatory comment. Discoverable, defaults to enforcing the slot-validity filter.

### Output (`output/generate_summary.py`)

- New `get_wasted_points(season_year, matchup_period, limit=5)` function. Combines per-player buckets via SUM, surfaces fa/bench split for the parenthetical attribution, applies team-label priority (active > bench > "Free Agent"), and joins fct_weekly_player_performance for partial-active context.
- New `format_wasted_points(wasted)` formatter. Produces:
  - `N. Player (MLB Team, Pos) -- Fantasy Team -- TOTAL [(BREAKDOWN)]`
  - TOTAL is `X+Y waste pts` when waste came from both buckets (the addition signals the split), else `X.X pts` (single source).
  - BREAKDOWN parenthetical lists each non-zero of `{X unowned, Y benched, Z active}`. Appears when there's anything to attribute (waste split across buckets, OR non-zero active context). Active threshold is `!= 0` not `> 0` so net-negative active stretches still appear (informative — the "doubly wasted" note for a future v2).
- Threaded through `generate_summary` signature and main block; section lands between Fair-and-Just and Current Season Records (last item in the weekly recap, before records sections start).

---

## Key Technical Decisions

### 1. Kona as the universal stat source — not "kona for FAs, mRoster for rostered"

The first instinct was a parallel pipeline: keep mRoster for rostered, add a kona path for FAs. We rejected it because kona returns a strict superset of mRoster's data — same `statSplitTypeId == 5 AND scoringPeriodId == K` per-game splits, same shape — plus FAs. One source for all stats means one code path, one DH-handling story, one place for any future stat-extraction bug to live and be fixed.

The cost was discovering mid-implementation that kona's `appliedTotal` is slot-blind whereas mRoster's was slot-aware. That cost manifested as the slot-validity filter we now apply at int_player_daily_stats — but that filter was structurally correct work we should have been doing anyway (mRoster was masking a latent bug).

### 2. FA determination by anti-join, not by trusting kona's `status` field

Kona's `filterStatus: ["FREEAGENT"]` (and the `status` field on each player) reflects **current** roster status, not status as of the queried scoring_period. A player who was FA in week 4 and got rostered in week 6 won't appear under the FA filter when querying week 4 today.

Solution: query kona without a status filter (full universe), then anti-join against the wrapper's `box_scores()` lineup for that scoring_period. Any player with stats in kona but not in any wrapper lineup is, by definition, a free agent on that day. Mid-season transactions handled correctly without any transaction log.

This is the third Phase-4 example of the project pattern: when an upstream data source has a known failure mode, sidestep it categorically rather than detect-and-patch.

### 3. Slot-stat-category validity filter at intermediate, with var toggle

ESPN's team-level scoring credits a stat only when the stat's category matches the slot type. A hitter slotted at RP contributes 0 to the team total despite having a non-zero `appliedTotal` on their player card. Kona's per-player `appliedTotal` is slot-blind; the filter has to live somewhere on our side.

Placement: `int_player_daily_stats`. Filters at the row level — per-stat per-day — so two-way players (Ohtani SP-day with both hitting + pitching stats) get partial credit naturally (pitching rows survive, hitting rows drop). No whole-player zeroing required.

The filter is gated by `var('strict_slot_validity', true)`. Keeps the door open for league rules ever changing (cross-slot credit, two-way exceptions, etc.). Inactive rows (BE/IL/FA) bypass the filter regardless so mart_wasted_points captures full stat lines.

### 4. `lineup_slot_category` derived once at staging

Three-value bucket (`'pitching'` | `'hitting'` | `'inactive'`) derived in `stg_box_scores` and propagated through to int. Multiple downstream consumers (`int_player_daily_stats` slot-validity filter, `int_team_daily_scores` and `int_player_daily_scores` active filters, `mart_wasted_points` inverse filter) all read the same column instead of each model re-deriving the slot taxonomy.

Future-proofing: when ESPN adds a new slot code or our league config changes, the bucket logic lives in one place. Single update propagates everywhere.

### 5. Team-level `platform_points` via wrapper `home_score` direct read (Path C)

Three options were evaluated for fixing the slot-validity inflation at the team level:
- **Path A**: Recompute team `platform_points` as `sum(slot-validated stat_points × current weights)`. Effectively makes platform = calculated under current weights.
- **Path B**: Apply slot-validity at extract time, zero out raw `points` for slot-mismatched players.
- **Path C** (chosen): Use the wrapper's per-team `home_score`/`away_score` from raw, which is ESPN's authoritative slot-aware team total (and includes any commissioner manual adjustments). Player-level platform_points stays slot-blind (the raw API value).

Path C wins on three counts:
- Matches ESPN's website exactly without any derivation on our side
- Honors the `platform_* = direct passthrough, no calculation` principle (codified below in conventions)
- Naturally exposes the rare commissioner-adjustment case via `platform_calculated_delta`

Cost: the "team_total = SUM(players)" invariant from the conventions doc no longer holds for `platform_points` specifically. That divergence is now meaningful and authoritative — captured in the new `platform_calculated_delta` and `player_rollup_platform_points` columns.

### 6. `platform_*` = direct API passthrough, `calculated_*` = derivation

A sharper definition of these prefixes than the project had previously codified:

- **`platform_*`**: lightweight, zero-calculation reads from the platform's API. No multiplication, no aggregation across stats, no slot-validity logic. Whatever ESPN's API said this entity scored, period. Includes any platform-side adjustments (commissioner overrides, slot-validity zeroing, etc.) without us needing to model them.
- **`calculated_*`**: our derivation under current-season weights with full slot-validity. Sums per-stat point contributions across players within a slot-and-category-valid filter. This is where math happens.

Net: `platform_*` reflects "what was scored at the time, by the platform's rules then-in-effect" and `calculated_*` reflects "what would be scored under today's rules and our model of the rules." Both have a place; conflating them via player-rollup-summed platform_points was the bug.

### 7. HBP_P fix at extract via STATS_MAP override

The espn-api wrapper's `STATS_MAP` collapses both stat ID 12 (batter HBP, +1) and 42 (pitcher HBP, -1) under the single name "HBP" in the breakdown VARIANT. Phase 3.2 patched this with a lineup-slot-based CASE in int_player_daily_stats: rewrite `stat_name='HBP'` → `'HBP_P'` when `lineup_slot IN ('SP', 'RP', 'P')`.

That patch broke for FAs (`lineup_slot='FA'` has no role signal) and was always wrong for two-way Ohtani days (both stats summed under one name; the slot decided which sign got applied to the sum). Fixing at extract — `_STAT_ID_TO_NAME[42] = 'HBP_P'` — decouples the seed from the wrapper's collision and routes the two stats correctly through the existing seed rows. The int CASE is gone.

The `_log_stats_map_collisions()` helper runs at import and surfaces any future espn-api STATS_MAP collisions as `[warn]` log lines so a regression doesn't sneak in silently.

### 8. `eligibleSlots` plumbed through extract for v2

Multi-position eligibility (Sanoja as 2B/RP, Ohtani as SP/DH) lives in kona's `eligibleSlots` array. Plumbed through the extract payload now (wraps the numeric IDs to readable strings via `LINEUP_SLOT_MAP`) so the next backfill captures it for free. Output script v1 uses primary `position` only; the v2 follow-up surfaces `eligible_slots` in staging and switches the wasted-performances display.

### 9. Two-bucket `mart_wasted_points` (BE+IL collapsed)

BE and IL are substantively the same wasted-points story for fantasy managers — both "rostered, didn't contribute." Collapsing them into `ROSTERED_INACTIVE` reduces visual noise in consumer outputs. Anyone wanting per-IL or per-BE detail can drop down to `int_player_daily_stats` directly.

Bucket naming chosen for clarity: `'FA'` and `'ROSTERED_INACTIVE'`. The output formatter further translates to "Free Agent" / fantasy team name for display.

### 10. The "Top Wasted Performances" rename

Originally drafted as "Top Wasted Points" — flipped to "Performances" because the leaderboard ranks individual player-week stories, not score columns. Consistent with "Top Hitter" / "Top Pitcher" framing (callouts on what specific players did, not abstract metric values).

---

## What's in Snowflake (Current)

- **Database**: `ESPN_FANTASY`
- **Raw schema**: `RAW`
  - `BOX_SCORES` — schema unchanged, but `raw_json` column now holds dict shape `{matchups: [...], free_agents: [...]}` for Phase-4 extracted rows. Pre-Phase-4 rows (if any) had bare matchups arrays; staging COALESCE handles both. Full backfill of 2025 + 2026 was performed end-of-Phase-4, so all rows are now in the new shape.
  - `SCORING_SETTINGS` — unchanged.
- **Analytics schema**: `ANALYTICS`
  - `STG_BOX_SCORES` — modified columns: nullable team fields, new `lineup_slot_category`.
  - `STG_PLAYER_STAT_BREAKDOWNS` — new `lineup_slot_category` column.
  - `INT_PLAYER_DAILY_STATS` — new `lineup_slot_category` column; row-level filtering applied per slot-validity rule.
  - `INT_TEAM_DAILY_SCORES`, `INT_PLAYER_DAILY_SCORES` — filter switched to bucket-based.
  - `FCT_WEEKLY_TEAM_PERFORMANCE` — new columns: `platform_calculated_delta`, `player_rollup_platform_points`.
  - `MART_WASTED_POINTS` — new view.
  - All other staging/intermediate/fact models unchanged structurally.
  - Both 2025 (full season) and 2026 (MP1-4 as of close-out) reprocessed end-to-end via `--year YYYY --all` extraction + `dbt build --full-refresh`. Every incremental fact rebuilt from scratch.

---

## Verification

Anchor checks (all confirmed before close-out):

```sql
SELECT team_name,
       ROUND(platform_points, 2)              AS platform_points,
       ROUND(player_rollup_platform_points, 2) AS player_rollup,
       ROUND(calculated_points, 2)            AS calculated_points,
       ROUND(platform_calculated_delta, 2)    AS delta
FROM ANALYTICS.FCT_WEEKLY_TEAM_PERFORMANCE
WHERE season_year = 2026 AND matchup_period = 1
ORDER BY platform_points DESC;
```

Results:
- **Hosstros**: platform = calculated = 374.25 (matches Phase 3.3.1 baseline → no regression for any team without slot misuse)
- **Island Daddys**: platform = calculated = 382.42 (matches website; previously inflated to 398.52 via player rollup of Sanoja's RP-slot hitting points)
- **Intentional Walk to the Bar**: platform = calculated = 482.88 (was 485.18 pre-fix; Ohtani's SP-day hitting line correctly dropped)
- All 14 teams: `delta = 0` (current-season scoring rules match our model exactly; no commissioner adjustments in MP1)

Coverage check confirmed:
- 2025 fully reprocessed (~22 matchup periods × 14 teams)
- 2026 reprocessed through MP4 (the latest completed period at close-out)
- `mart_wasted_points` populated for both seasons across both buckets

Output script run: `Top 5 Wasted Performances` section renders correctly with attribution parenthetical (single-source pure-FA rows: clean total; bench-with-active rows: `40.8 pts (40.8 benched, 1.4 active)` style breakdown).

---

## Open Investigations Carried Forward

None. All the design decisions converged cleanly during build; no unresolved questions blocking subsequent work.

---

## Bookmarks for Future Work

### Phase 5.0 — "First Final" Cut

User-defined scope: wrap remaining Phase 4 follow-ups and finalize the text-based outputs into a polished v1. Likely includes:

- **Eligible slots in wasted-performances display**. Extract already plumbs `eligibleSlots` through the raw payload. Need to surface in `stg_box_scores` (new column with COALESCE-to-NULL for legacy rows) and switch the format string in `format_wasted_points` from primary `position` to a comma-joined eligibleSlots list (filtering out BE/IL/UTIL noise per display preference).
- **Negative active points as waste**. The user clarified the long-term intent: a player who produces negative active points is *doubly* wasteful — you couldn't have done worse than 0 by benching them. Total wasted should become `fa_pts + bench_pts + abs(min(0, active_pts))`. Likely lives at the mart layer (new column or augmented `wasted_points` value, with a flag indicating whether negative-active is included). Output formatter math changes; format shape stays the same.
- **Output text formatting polish** more broadly — the user mentioned wanting to "nail down the visualizations of the outputs" after Phase 4. Front-page BBCode tightening, ordering tweaks, possibly a unified output-script module for shared connection management and formatters.

### Extract Performance Optimizations (deferred to Phase 4.x or later)

Current extract makes 2 sequential HTTP calls per scoring_period (wrapper for matchup structure + kona for stats). For weekly cadence this is fine (~5s for 1-3 SPs); for full-season backfills it's ~30 minutes. Optimizations on the table:

1. **Multi-view in a single HTTP call**: ESPN's API accepts `?view=mMatchupScore&view=kona_player_info` in one URL and returns both views combined. Halves network latency per SP.
2. **Batched kona via `filterStatsForScoringPeriodIds`**: Pass the full SP list for a matchup in one kona call, get one response with each player's stats[] containing entries for all requested SPs. Drops kona calls from N to 1 per matchup.
3. **Parallel-fire wrapper calls** via `concurrent.futures.ThreadPoolExecutor` or `asyncio`: same call count, ~Nx faster wall clock since calls are network-bound.

User wants to tackle this in a Phase 4.x optimization phase along with connection-management consolidation.

### Explicit `pointsAdjustment` Field Investigation

`platform_calculated_delta` currently conflates three sources of divergence: slot validity (now zero post-fix), current-vs-historical weight differences (zero for current season), and commissioner manual adjustments (rare). If ESPN exposes an explicit `pointsAdjustment` field on the matchup view, we could split the delta into a clean `commissioner_adjustment` column + a residual `derivation_delta`. Worth a brief raw-API probe.

### Other Backlog (from project_phase_plan)

- "Record set this week" callouts in weekly recap (originally Phase 3.1; deferred)
- Rate-stat leaderboards with PA/AB minimums
- Wire `owner_nicknames` seed into models
- `fct_team_career_stats` mart
- Verify stat ID 30 (15 pts per, only 1 observed row) isn't a real scored stat
- MetricFlow Semantic Layer

---

## Migration Notes for Next Session

State at end of Phase 4.0:

- Worktree: `nice-euclid-40ec87`. Ready to commit and merge.
- Backfill complete: 2025 (full) and 2026 (MP1-4) re-extracted under the kona pipeline. All facts rebuilt via `dbt build --full-refresh`.
- All dbt tests green (40 tests, no failures, no warnings beyond a previously-fixed deprecation).
- Memory files updated: Phase 4.0 moved to "Shipped" in `project_phase_plan.md`; new conventions added to `project_conventions.md` (platform_*-passthrough principle, FA-anti-join principle, var-toggleable filters).

To resume in a fresh conversation:

> "Reading the project memory and Phase 4.0 docs. Phase 4.0 (wasted points + kona migration + slot validity + path-C team platform_points) is shipped and verified. Starting Phase 5.0 — wrapping the remaining Phase 4 follow-ups (eligible_slots display, negative-active-as-waste) and polishing the text-based output. After Phase 5.0 we move to broader concerns."

---

## Git History (commits expected through Phase 4.0)

After commit, history will include:

- (Phase 1.0 through 3.3.1, see prior phase docs)
- Phase 4.0: wasted points + kona migration + slot validity. Specifically:
  - Extract: kona endpoint replaces mRoster, FA emission via anti-join, HBP_P STATS_MAP override, eligibleSlots plumbed, restructured logline
  - dbt: lineup_slot_category staging column, slot-validity filter at int with var toggle, HBP_P CASE removed, team-level platform_points via wrapper direct read, platform_calculated_delta column, mart_wasted_points view, BE/IL/FA filter unified across active models
  - Output: Top Wasted Performances section in generate_summary.py
- Add Phase 4.0 documentation
