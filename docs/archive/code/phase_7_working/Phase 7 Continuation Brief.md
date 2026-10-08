# Phase 7 Continuation Brief — for fresh chat

**Audience:** A fresh Claude Code session picking up Phase 7 where the prior chat left off. This doc supersedes Phase 7 Handoff.md (which was the *initial* brief, written before architectural decisions were locked) and partially supersedes Phase 7 Architecture Review.md (which captures the analysis but predates the symmetric-fact decision and several other locks).

**Lifecycle:** Internal working doc. Not public-facing. Gets superseded by `Phase 7 Documentation.md` when v1.0 ships, per the established phase-doc convention. Read top-to-bottom once; thereafter jump to the section you need.

**Status as of this doc:** Step 1 of the execution plan complete. Commits `56e3c32` + `457a7a9` shipped to `claude/phase-7-v1.0`. Local main is 7 commits ahead of `origin/main`; user has not authorized push. Working tree clean.

---

## 1. Reading order for orientation

Spend ~30-45 minutes here before touching code:

1. **This doc** (you're here) — Phase 7 status + locked architectural decisions + execution plan.
2. **`HANDOFF.md`** — master project reference. §1-3 (orientation), §6 (code map), §10 (roadmap; partially superseded by what we've decided).
3. **`Phase 6.3.3 Documentation.md`** — most recent shipped phase; gives the architectural snapshot Phase 7 is rearchitecting.
4. **`Phase 7 Architecture Review.md`** — analysis that led to current decisions. Several sections superseded — flagged inline below.
5. **`Phase 7 Handoff.md`** — original Phase 7 brief from before this chat started. Original 7.1-7.6 chunk plan (CHANGELOG-first) is **superseded** by the refactor-first plan in §5 of this doc. Setup decisions in its §2 still hold (license=MIT default, project name TBD).
6. **Memory files** at `~/.claude/projects/C--Users-kyled-projects-espn-league-manager/memory/`:
   - `MEMORY.md` (index)
   - `user_role.md` (portfolio context — Senior Data Analyst / Analytics Lead targeting; project doubles as recreational use)
   - `project_phase_plan.md` (cadence; current phase = 7.0)
   - `project_conventions.md` (the patterns; lead with these)
   - `feedback_documentation_source_of_truth.md` (when docs disagree, phase docs win)
   - `feedback_test_running_side_effects.md` (Sheets-suppression idiom; now superseded by the `--no-sheets` flag shipped in commit `360fa8f`)

---

## 2. What's already shipped this phase

### Commit `56e3c32` — Phase 7 prep: test scaffold, connection consolidation, output polish

**Test scaffold** (new):
- `pytest.ini` — scopes discovery to `tests/` so the legacy gitignored research scripts at repo root (`test_espn.py`, `test_kona_returns.py`, etc.) don't get picked up.
- `tests/__init__.py`, `tests/conftest.py` — sys.path injection so test files can `import records`, `import formatters`, etc.
- `tests/test_formatters.py` (53 tests) — covers `fmt_value`, `fmt_avg`, `fmt_ip`, `fmt_record_value`, `filter_eligible_slots`, `format_contributors`, `format_hitter_stats_line`, `format_pitcher_stats_line`, `format_top_scorer_stats_line`.
- `tests/test_records_pure.py` (61 tests) — covers `ordinal`, `_player_stat_value` (incl. PA/SB_CS/W_L/SV_BLSV derived stats), `best_or_worst_label`, `should_track_record` (all polarity + always-tracked combinations), `_orchestrator_filter`, `_collapse_one_group` (non-saturated cases — saturated requires Snowflake mock and was deferred), `collapse_ties`, `get_effective_polarity` merge logic (with `monkeypatch` on `get_stat_polarity`).
- **All 114 tests pass in ~0.5s. No Snowflake required.**

**Connection consolidation + boilerplate factoring** (`output/db.py`, new):
- Lazy-singleton Snowflake connection with `atexit` cleanup. Replaces the per-call open/close pattern that was duplicated in `records.py` and `generate_summary.py`. Saves ~15-20 handshakes per script run.
- `db.init()` — idempotent script startup: utf-8 stdout reconfig + `load_dotenv` + warm Snowflake config. Output scripts call once at top.
- `db.query_snowflake()` — sole Snowflake entry point now. Library modules (`records.py`, `league_notes.py`) get it via `from db import query_snowflake`. The legacy entry point `records.query_snowflake` still resolves because of Python's `from X import Y` re-export semantics, so `league_notes.py`'s call doesn't need updating.
- `db.close()` — explicit cleanup; rarely needed (atexit handles it).

**Output script polish** (`generate_summary.py`):
- Wasted-Performances headline: was `"X+Y+Z waste pts"` (additive); now `"X.X wasted pts"` (sum), with components in the breakdown parenthetical.
- Breakdown wording: `"doubly wasted"` → `"negative X.X active"`. Clearer framing — these are real active points that happened to be negative; the column is still named `doubly_wasted_pts` for backward compat with the row-dict consumers but the user-facing label changed.
- New Records section: blank lines between entries removed.

**`.gitignore` fix**:
- `test_*` → `/test_*`. The original pattern matched files at any depth, blocking legitimate `tests/` files. Now scoped to repo root only, so the legacy research scripts stay ignored but `tests/` works.

**Net diff**: +1061 / −102. Three output scripts shed ~70 lines of duplicated boilerplate.

**Verified**: All 114 tests pass post-refactor. End-to-end smoke runs of both output scripts successful (recap clean, records report `--no-sheets` clean, sheets sink suppression message confirms `SHEETS_OUTPUT_ID` env-var gating intact).

### Commit `457a7a9` — Phase 7 architecture review (working doc)

`Phase 7 Architecture Review.md` — internal working doc capturing the analysis that led to the locked decisions. Sections that are still load-bearing:
- Module-by-module review (with file:line citations) — accurate as of pre-refactor state.
- Cross-cutting duplication catalog (9 items) — accurate.
- Operational risks (7 items) — accurate.
- Test story — accurate (executed via the test scaffold above).
- Portability sketch — accurate (informational; deferred).

Sections **superseded** by the chat conversation that followed:
- §2.5 refactor candidate ranking — Q2 settled to "full"; Q3 to "yes"; the symmetric-fact extension below isn't in the doc.
- §3 open questions — see §3 of this doc for current Q-status.

The Phase 7 Architecture Review doc commit message includes a note flagging that decisions evolved beyond it, but the body of the doc is unchanged.

---

## 3. Locked architectural decisions

### 3.1 The end-state DAG

```
                                    ┌── stat_classification ──┐
                                    │   (extended seed:        │
                                    │    + display_name        │
                                    │    + abbrev              │
                                    │    + polarity            │
                                    │    + is_record_candidate │
                                    │    + category            │
                                    │    + is_derived          │
                                    │    + derivation_expr)    │
                                    │                          │
                                    │   stg_scoring_settings   │
                                    │            │             │
                                    ▼            ▼             ▼
raw.box_scores ──► stg_box_scores ──► stg_player_stat_breakdowns
                          │                       │
                          └────────┬──────────────┘
                                   ▼
                            int_player_daily              ◄── consolidates today's
                                   │                          int_player_daily_stats +
                                   │                          int_player_daily_scores;
                                   │                          wide row with counting +
                                   │                          per-stat *_pts + platform
                                   │                          points + slot info +
                                   │                          slot-validity flag +
                                   │                          NEW: negative_points col
                                   │                          (per-day sum of negative
                                   │                          point days for that player)
                                   ▼
                    int_player_weekly_performance        ◄── COMPREHENSIVE; both
                    (TABLE materialization)                  active AND inactive rows
                                   │                          with performance_status
                                   │                          flag. Queryable for
                                   │                          ad-hoc analytics.
                                   ▼
        ┌──────────────────────────┴──────────────────────────┐
        ▼                                                     ▼
fct_weekly_player_active_performance     fct_weekly_player_inactive_performance
  - team_id NOT NULL                       - team_id NULLABLE (FAs have no team)
  - active filter                          - inactive filter
  - all stat columns wide                  - all stat columns wide
  - calculated_* + platform_*              - calculated_* (no platform_* since
  - negative_points                          inactive players don't get a
                                              platform score for the team)
                                           - wasted_bucket dim ('FA' /
                                              'ROSTERED_INACTIVE')
        │                                                     │
        ▼                                                     ▼
fct_weekly_team_active_performance       fct_weekly_team_inactive_performance
  - SUM rollup of active players           - SUM rollup of inactive players
  - PRESERVES platform_points              - team_id NULLABLE (the FA bucket
     wrapper-direct exception                rolls up to a NULL-team row;
     (read from home_score, NOT a            ROSTERED_INACTIVE has team_id)
     player rollup; captures               - wasted_bucket dim retained
     commissioner adjustments)
        │                                                     │
        └──────────────────────────┬──────────────────────────┘
                                   ▼
                       mart_stat_leaderboard
                       UNPIVOT all 4 facts; new partition column:
                       performance_status ∈ {active, inactive}.
                       Existing partitions kept: entity_grain
                       (team/player), record_scope (all_time/
                       current_season), record_direction (most/
                       fewest). All consumers default-filter to
                       performance_status = 'active' for v1.0.
```

**Comparison with current state**: 17 models → 13 models. Symmetric structure. Every layer serving a clear purpose.

### 3.2 Models being dropped (4)

- **`int_team_daily_scores`** — dead; nothing consumes it. Pre-Phase-3.1 holdover.
- **`int_player_daily_scores`** — collapsed into new `int_player_daily`.
- **`int_player_daily_stats`** — collapsed into new `int_player_daily`.
- **`fct_weekly_player_scores`** — Phase 3.1 thin wrapper; `fct_weekly_player_active_performance` reads platform_* directly from `int_player_daily` (which carries them as columns).
- **`mart_wasted_points`** — replaced by symmetric `fct_weekly_player_inactive_performance` + `fct_weekly_team_inactive_performance`.

### 3.3 Models being renamed (2)

- `fct_weekly_player_performance` → `fct_weekly_player_active_performance`
- `fct_weekly_team_performance` → `fct_weekly_team_active_performance`

User accepted the verbose `_active_performance` / `_inactive_performance` suffix because "performance" connotes "stats + calculated + platform pts in one wide row" — meaningful term that justifies the length.

### 3.4 Models being added (5 new + 1 expanded)

- **NEW** `int_player_daily` — daily wide row consolidating today's two int models. Joins `stg_scoring_settings` + `stat_classification`, derives per-stat *_pts, applies slot-validity flag (does NOT filter — both downstream facts apply the inverse filter at their level). NEW column: `negative_points` per scoring_period (sum of all negative-point contributions from that player on that day).
- **EXPANDED** `int_player_weekly_performance` — currently exists but only carries active rows (and feeds the active fact). New shape: comprehensive — both active and inactive rows with `performance_status` flag. **Materialized as TABLE, not view** (downstream analytical consumers may query directly; dbt convention allows int as table when needed).
- **NEW** `fct_weekly_player_inactive_performance` — wide stats per inactive player-week with `wasted_bucket` dim. team_id NULLABLE.
- **NEW** `fct_weekly_team_active_performance` — same as today's fct_weekly_team_performance (rename only).
- **NEW** `fct_weekly_team_inactive_performance` — SUM rollup of player inactive. team_id NULLABLE for the FA bucket aggregate row.
- **EXPANDED** `mart_stat_leaderboard` — consumes all 4 facts via UNION; gains `performance_status` partition column.

### 3.5 Cross-cutting decisions locked

- **Q2 = full**: tracked-stats seed expansion. The `stat_classification.csv` seed gets new columns (`display_name`, `abbrev`, `polarity`, `is_record_candidate`, `category`, `is_derived`, `derivation_expr`). The mart UNPIVOT lists become Jinja loops over the seed (no more hand-maintained ~50-stat lists). Python label maps in `formatters.STAT_DISPLAY` / `STAT_ABBREV` get derived from the same seed via a query helper. Eliminates 5+ places where stat names are currently enumerated.

- **Q3 = yes**: polarity to dbt. Bundled with Q2 — the `polarity` column on `stat_classification` flows into `mart_stat_leaderboard` via JOIN. `_IMPLICIT_POLARITY` and `get_effective_polarity` in `records.py` collapse into a 1-line query. `should_track_record` may also become a column on the mart (`is_record_candidate`).

- **`negative_points` (column name)**: the chosen name for the per-day sum of negative-point contributions. Lives on `int_player_daily` and rolls up to weekly via `int_player_weekly_performance`. Replaces the "doubly_wasted_pts" concept — that concept now lives on the *active* fact (a player who was started AND produced net-negative points has a `negative_points > 0` value). A player who scored 100 pts in a week with one −5 day has `calculated_points = 100` AND `negative_points = 5`. Different metrics; both surfaced.

- **Doubly-wasted calc**: previously computed in `get_wasted_points` SQL inside the recap script, joining mart_wasted_points + fct_weekly_player_performance. Post-refactor: `negative_points` lives directly on `fct_weekly_player_active_performance`, so the recap formatter consumes it cleanly without a cross-fact JOIN.

- **Inactive records skipped from BBCode for v1.0**: the leaderboard mart will carry inactive records, but consumers (recap, records report) default-filter to `performance_status = 'active'`. Future "Wasted Records" sections are a small consumer change, not a re-architect.

- **Big-bang migration**: build new structure → `dbt build --full-refresh` → run output scripts → spot-check vs current week's recap. With pure-function tests + visual diff verification, no step-by-step gain at our scale.

- **Single nickname join at staging**: `mart_wasted_points`'s redundant `player_nicknames` re-join (lines 78-80) AND `fct_weekly_player_scores`'s redundant re-join (lines 31-33) get dropped. Phase 3.1 *intended* `display_name` to propagate from `stg_box_scores`; these were holdovers. (Note: `mart_wasted_points` is being dropped entirely; `fct_weekly_player_scores` is being dropped entirely. So these "drop the re-join" tasks are subsumed.)

- **platform_points wrapper-direct exception PRESERVED**: `fct_weekly_team_active_performance.platform_points` continues to be sourced direct from the wrapper's `home_score`/`away_score`, NOT a player rollup. Captures commissioner adjustments + ESPN's W/L authority. Documented in HANDOFF §7. The `platform_calculated_delta` diagnostic column comes along.

- **`int_player_weekly_performance` filter semantics**: carries every player-week row regardless of status. Active fact filters `WHERE lineup_slot_category != 'inactive' AND team_id IS NOT NULL`. Inactive fact filters `WHERE lineup_slot_category = 'inactive'`. Both inherit the wide stats columns from the int.

### 3.6 What stays the same (don't break)

Per HANDOFF.md §11 + decisions made this chat:

- Section ordering in `generate_summary.py` (locked Phase 5)
- Header conventions (`[u][b]Section[/b][/u]` + `[b]Label[/b]: value`)
- Player-card shape (`Player (TeamAbbr), X.X pts -- {stats}`)
- "Records show owner names; recap doesn't"
- "Week N" / playoff-round-name display
- 17-col Sheets schema across 3 tabs
- `SHEETS_OUTPUT_ID` env-var opt-in
- `--no-sheets` CLI flag (shipped in commit `360fa8f`)
- `LeagueNote.txt` verbatim append under "Additional Notes"
- mart's `most`/`fewest` direction values (Phase 6.3.3 decision — direction-agnostic with polarity-aware labels at consumer)
- `platform_*` vs `calculated_*` distinction (load-bearing per HANDOFF §7)
- `is_abnormal` filtering at the leaderboard
- `is_always_tracked` seed flag (already a good example of seed-driven config — the Q2 expansion generalizes this pattern)
- Active-only filter on the active fact (the symmetric inactive fact is the new home for what's currently in `mart_wasted_points`)

---

## 4. Open question status

| Q | Status | Notes |
|---|---|---|
| Q1: records.py split shape | **Deferred** | Wait until after Step 2's dbt rearchitect lands. Polarity logic moves to dbt (Q3); label maps come from the seed (Q2). The remaining content's natural seams will be much more obvious post-refactor than they are now. |
| Q2: tracked-stats seed expansion | **Settled: full** | Bundled with Step 2. |
| Q3: polarity to dbt | **Settled: yes** | Bundled with Step 2 (Q2 carries it). |
| Q4: test scope | **Partially settled** | Pure-function tests done (114 passing). Golden-output tests added during Step 2 verification — pin a fixture mart row state, assert BBCode matches expected. Catches behavioral regressions when output scripts get updated. |
| Q5: project name | **Deferred** | Settle when we get to README chunk in Step 5. Options from Phase 7 Handoff §2 still on the table: `espn-fantasy-baseball-pipeline` (descriptive), `Diamond Cuts` / `Box Score` / `Bullpen` (punchy), league-flavored. |
| Q6: execution order | **Settled** | See §5. |

---

## 5. Execution plan (5 steps)

### Step 1 — Phase 7 prep ✅ DONE

Commits `56e3c32` + `457a7a9`. See §2 above.

### Step 2 — The bundled architecture rearchitect (the big chunk; estimated 10-15 hours)

The substance of Phase 7. Bundled because the pieces are interdependent: the seed expansion (Q2) drives the symmetric-fact wide column lists; the symmetric facts inherit polarity (Q3) from the seed; the leaderboard's new `performance_status` partition consumes both new facts.

**Sub-chunks, suggested order:**

1. **Tracked-stats seed expansion** (~3-4 hr). Extend `dbt_league/seeds/stat_classification.csv` with new columns (`display_name`, `abbrev`, `polarity`, `is_record_candidate`, `category`, `is_derived`, `derivation_expr`). Update `dbt_project.yml` `column_types`. `dbt seed --full-refresh`. Build a small Python helper (probably in `output/db.py` or new `output/stat_catalog.py`) to query the seed and return label dicts for `formatters.STAT_DISPLAY` / `STAT_ABBREV` consumers. Verify: existing `mart_stat_leaderboard` still builds (unchanged at this point); seed query helper returns the right shape.

2. **Build `int_player_daily`** (~2 hr). Combines the responsibilities of today's `int_player_daily_stats` + `int_player_daily_scores`. Wide row per (season_year, scoring_period, team_id, player_id, lineup_slot). Carries: counting stats (long → wide via UNPIVOT or explicit CASE), per-stat *_pts (joining `stg_scoring_settings`), platform points (from kona's `appliedTotal`), lineup_slot_category, slot-validity flag, and `negative_points` (sum of point contributions where contribution < 0 on this scoring_period). Doesn't filter. Materialize as view.

3. **Expand `int_player_weekly_performance`** (~2-3 hr). Currently active-only and feeds the active fact. New shape: comprehensive — both active and inactive rows with `performance_status` flag. Materialize as TABLE (not view; downstream consumers may query for analytics). Carries the wide stats columns derived from the seed-driven Jinja loop (no more hand-maintained ~50-stat lists).

4. **Build the 4 facts** (~3-4 hr).
   - `fct_weekly_player_active_performance` (rename of today's `fct_weekly_player_performance`; filter `lineup_slot_category != 'inactive' AND team_id IS NOT NULL`; consumes `int_player_weekly_performance`).
   - `fct_weekly_player_inactive_performance` (filter `lineup_slot_category = 'inactive'`; team_id NULLABLE; carries wasted_bucket dim).
   - `fct_weekly_team_active_performance` (rename of today's `fct_weekly_team_performance`; SUM rollup; PRESERVES platform_points wrapper-direct exception).
   - `fct_weekly_team_inactive_performance` (NEW; SUM rollup of player inactive; team_id NULLABLE for FA bucket aggregate).
   - All four use Jinja-looped column lists from the seed.
   - Schema.yml entries for all four.

5. **Update `mart_stat_leaderboard`** (~1 hr). UNPIVOT all 4 facts via UNION. Add `performance_status` partition column. Existing partitions retained: `entity_grain`, `record_scope`, `record_direction`. Leaderboard remains a view.

6. **Drop dead/holdover models** (~30 min). Delete `int_team_daily_scores`, `int_player_daily_scores`, `int_player_daily_stats`, `fct_weekly_player_scores`, `mart_wasted_points`, plus their schema.yml entries.

7. **Update output scripts** (~1-2 hr).
   - `records.py`: replace `_IMPLICIT_POLARITY` + `get_effective_polarity` with single mart-row read (via the new `polarity` column). Replace `should_track_record` with mart-column filter (or query helper). Simplify polarity-related code.
   - `formatters.py`: `STAT_DISPLAY`, `STAT_ABBREV`, `_SCORE_STAT_KEYS` — derive from seed-query helper instead of hardcoding. Same module shape; just data source changes.
   - `generate_summary.py`: `get_wasted_points` rewritten to consume `fct_weekly_player_inactive_performance` + `fct_weekly_team_inactive_performance` (no more JOIN with mart_wasted_points). The `negative_points` column is now directly on the active fact, so the doubly-wasted calc simplifies.
   - `sheets_writer.py`: add `WHERE performance_status = 'active'` to its leaderboard read (default behavior; v1.0 doesn't surface inactive records).
   - `generate_records_report.py`: same default filter.
   - `league_notes.py`: any callouts that hit `mart_wasted_points` switch to the new fact.

8. **Add golden-output tests** (~1-2 hr). Pin a fixture mart row state → expected BBCode block. One test per script. Catches behavioral regressions when consumers get rewired.

9. **Verification** (~2 hr).
   - `dbt build --full-refresh` — should be clean (target: 60+ PASS / 0 ERROR / 0 WARN).
   - `pytest tests/` — all 114 existing pure-function tests + new ones pass.
   - Run both output scripts; spot-check BBCode output vs current Week 6 recap. Visual diff check for any unintended changes.
   - `--no-sheets` flag still suppresses correctly.
   - Sheets writer (with real SHEETS_OUTPUT_ID, user's call when to run) — verify 17-col schema preserved.

**Single bundled commit** for Step 2 (per HANDOFF git hygiene: "one bundled phase commit + a doc commit per phase"). Doc commit happens at Step 5.

### Step 3 — Records.py split (Q1) (~3-4 hr)

Post-Step-2, the natural seams in `records.py` will be different from what they are today (polarity logic moved to dbt; label maps come from seed). My pre-refactor recommendation in §2.5 of the architecture review was a 4-module split (data / polarity / presentation / orchestrator). After Step 2, "polarity" is a much smaller module (might fold into "data"); a 3-module split (data / collapse-and-presentation / orchestrator) might be cleaner. Make this call WITH the user — they explicitly want to follow the refactor decisions, not have them dropped on them.

Single commit.

### Step 4 — Repo hygiene (~2 hr)

- `requirements.txt` UTF-8 fix (currently UTF-16 LE; *nix-hostile). Convert + add `pytest>=7.0` + verify `pip install -r requirements.txt` works clean.
- Move root-level `test_*.py` research scripts (test_espn.py, test_kona_returns.py, etc.) into `archive/research/` or similar. They're gitignored so the move is local-only; update HANDOFF.md and any references.
- Worktree cleanup: `git worktree remove` for `happy-elion-8a788e`, `distracted-swirles-7aee21`, `wizardly-wozniak-683b30` (per HANDOFF §8). `phase-3.2` is long-lived; leave alone.
- `.gitignore` audit — anything missing from cached state? OAuth tokens, dbt logs, etc. (already covered by current .gitignore based on initial read.)
- Add `LICENSE` — MIT per Phase 7 Handoff setup decision §2; **confirm with user before writing**.

Single commit.

### Step 5 — Public docs (~5-7 hr)

- `CHANGELOG.md` — keepachangelog format. Map phases retroactively to semver per Phase 7 Handoff §3.
- `README.md` rewrite — biggest creative chunk. The architecture diagram from §3.1 of this doc becomes the Mermaid diagram. The "active = fantasy reality, inactive = MLB reality" framing the user offered is a great section anchor. **Project name (Q5) must be settled before README header.**
- `SETUP.md` — bring-your-own-credentials path for new users.
- `ROADMAP.md` — Now / Next / Later / Won't Do; pull from HANDOFF.md §10 with v1.0 lens.
- dbt docs polish — fill in remaining `description` fields in schema.yml across all layers. Add `exposures` for the output scripts. `dbt docs generate` + push `target/` to `gh-pages` branch + enable Pages.
- Tag `v1.0.0` + GitHub Release with changelog as release notes.

Single doc commit per Phase 7 convention.

---

## 6. Implementation notes for Step 2

Selected design notes for the rearchitect; not exhaustive — many calls will surface during implementation.

### Tracked-stats seed schema

Proposed new columns on `stat_classification.csv`. Existing columns: `stat_id`, `stat_name`, `stat_category`, `is_counting`, `is_always_tracked`, `notes`. New columns:

- `display_name` (str) — full display string used in BBCode and Sheets headers (e.g., `'Home Runs'`, `'Strikeouts (Pitcher)'`). Replaces formatters.STAT_DISPLAY.
- `abbrev` (str) — short form (e.g., `'HR'`, `'K'`). Replaces formatters.STAT_ABBREV.
- `polarity` (str: `'positive'` | `'negative'` | `'neutral'`) — whether more is good. Derives from sign of points_per_unit for scored stats; explicit for non-scored. Replaces records._IMPLICIT_POLARITY.
- `is_record_candidate` (bool) — does this stat surface as a record (in current consumer logic)? Replaces records.should_track_record's polarity rules.
- `category` (str: `'hitting'` | `'pitching'`) — for grouping in display ordering.
- `is_derived` (bool) — true for PA, SB_CS, W_L, SV_BLSV (computed inline at mart, no fct column).
- `derivation_expr` (str, nullable) — for derived stats, the inline expression (e.g., `'ab + b_bb + hbp + sf'` for PA). Lets the mart UNPIVOT loop read the expression and build the column.

Update `dbt_project.yml` seed `column_types` accordingly.

The Jinja loop pattern in mart_stat_leaderboard becomes something like:

```sql
{% set stat_rows = run_query("SELECT stat_name, derivation_expr FROM " ~ ref('stat_classification')) %}
unpivot (stat_value for stat_name in (
    {%- for row in stat_rows -%}
        {%- if row['DERIVATION_EXPR'] -%}
            ({{ row['DERIVATION_EXPR'] }}) as {{ row['STAT_NAME'].lower() }}{% if not loop.last %},{% endif %}
        {%- else -%}
            {{ row['STAT_NAME'].lower() }}{% if not loop.last %},{% endif %}
        {%- endif -%}
    {% endfor %}
))
```

(Sketch only; verify the exact Jinja + Snowflake UNPIVOT syntax during implementation.)

### `negative_points` derivation

At `int_player_daily`, after computing per-stat *_pts:

```sql
sum(case when {{ stat_name }}_pts < 0 then {{ stat_name }}_pts else 0 end) as negative_pts_from_{{ stat_name }}
```

Or simpler: aggregate `negative_points` as the sum of negative individual stat-pts contributions for that scoring_period. Then weekly rollup is `SUM(negative_points)` across the player's scoring periods in the matchup.

Decision: the user said "I'm not positive how to handle [the week's net positive but with one negative day]". My read: surface both — `calculated_points` (week net) AND `negative_points` (week gross damage). The recap formatter decides how to interpret.

### Migration strategy for existing data

Big-bang per §3.5. The full-refresh rebuild will:
- Drop and rebuild all marts (views, instant)
- Drop and rebuild `int_player_weekly_performance` as a table (slow first time; ~3-5 min for the current 2 seasons of data)
- Drop and rebuild the 4 facts as incremental tables (similar)

Total dbt build full-refresh time: estimate 5-10 minutes. Acceptable for a one-time migration.

The user's existing weekly cadence picks up immediately after — the new fact names are different but the consumers (output scripts) get updated in the same commit.

### Verification approach

- `dbt build --full-refresh` clean (target: 60+ PASS / 0 ERROR / 0 WARN).
- `pytest tests/` clean (target: 114+ tests passing; new tests added per Step 2.8).
- Run `python output/generate_summary.py` — visual spot-check vs current Week 6 recap (the BBCode the user has been posting). Look for: section ordering preserved, all sections render, no UnicodeEncodeErrors, polarity-aware Best/Worst labels still right, playoff round names render correctly when applicable, doubly-wasted-via-negative_points renders as expected.
- Run `python output/generate_records_report.py --no-sheets` — visual spot-check. Suppression message confirms the gating still works.
- Optional but recommended: run with real `SHEETS_OUTPUT_ID` (user's call) — confirm 17-col schema preserved, all 3 tabs populated, no per-row drift from current state.

---

## 7. Verification approach (general)

The pattern that's worked through prior phases (per HANDOFF §12):

1. **Make changes in the worktree branch** (not main).
2. **`dbt build --full-refresh`** if dbt models changed. 60+ PASS / 0 ERROR / 0 WARN expected.
3. **`pytest tests/`** clean.
4. **Run the relevant Python script with sinks suppressed**:
   - For BBCode-only verification: `python output/generate_records_report.py --no-sheets`.
   - For `generate_summary.py`: no opt-in sinks, runs as-is.
5. **Spot-check the output**: section ordering, polarity labels right, no encoding errors, etc.
6. **Diff review**: `git diff` should be minimal-and-focused.
7. **Commit + (separate) doc commit**.

---

## 8. The user's continued operational dependence

The user runs this every Sunday for their 14-team H2H league. Any Phase 7 change that touches user-facing behavior surfaces immediately the following week. Don't break:

- BBCode output shape (recap + records report)
- Sheets schema (17 cols × 3 tabs; gated by SHEETS_OUTPUT_ID)
- The `--no-sheets` flag behavior
- LeagueNote.txt verbatim appending
- Section ordering in the recap

If a change touches these, surface it before merging. The user is the most important QA on this project.

### What the user does NOT do

Per HANDOFF §11:
- Doesn't run dbt against prod (`dev` target IS the operational target).
- Doesn't read the dbt docs catalog (yet — that's Step 5).
- Doesn't review every diff line-by-line. Trusts the verification (`dbt build` clean + smoke tests + spot-check of one BBCode output) more than the diff. Don't skip verification.

---

## 9. Don't-touch list (operational + collaboration)

**Operational** (per HANDOFF §11 + chat decisions):
- Section ordering in `generate_summary.py`
- Header conventions, player-card shape, "Records show owner names; recap doesn't"
- Sheets schema (17 cols × 3 tabs)
- `SHEETS_OUTPUT_ID` opt-in, `--no-sheets` suppression
- `LeagueNote.txt` verbatim append
- mart's `most`/`fewest` direction values
- `platform_*` vs `calculated_*` distinction
- `is_abnormal` filtering
- platform_points wrapper-direct exception in team active fact

**Collaboration** (chat-derived; matches user's stated preferences):
- **Don't push to `origin/main`** without explicit user permission. Currently 7 commits ahead.
- **Don't make solo refactor decisions** on Q1 (records.py split shape) or other open architectural choices. The user explicitly wants to follow what's happening, not have it dropped on them.
- **Conversational walkthroughs > sole-author dumps** for any chunk involving design choices. Big chunks of mechanical work are fine (e.g., updating ~50 stat columns from a seed) but the design layer should be collaborative.
- **Don't commit without confirmation**. The user has been explicit about wanting to review before commits land.

---

## 10. Worktree state + cleanup plans

Current `git worktree list`:
- `C:/Users/kyled/projects/espn-league-manager` — main branch
- `.claude/worktrees/distracted-swirles-7aee21/` — stale (clean up in Step 4)
- `.claude/worktrees/happy-elion-8a788e/` — where the prior chat worked; stale once we move to phase-7-v1.0 (clean up in Step 4)
- `.claude/worktrees/phase-3.2/` — long-lived per HANDOFF §8; leave alone
- `.claude/worktrees/phase-7-v1.0/` — **active for Phase 7**
- `.claude/worktrees/wizardly-wozniak-683b30/` — stale (clean up in Step 4)

For Step 4 cleanup: `git worktree remove <path>`. If files locked (dbt logs), use `--force`.

---

## 11. What's NOT in scope for Phase 7

For clarity, so the new chat doesn't get scope creep:

- **No new product features**. Phase 7 is polish + presentation. New features go to v1.x.
- **No mart-layer changes** beyond the rearchitect described in §3 + cleanup in §3.2.
- **No public socializing** until v1.0 ships AND user explicitly green-lights post-release.
- **No Sheets formatting preservation** — separate task (HANDOFF §10 NOW item #2).
- **No cross-platform / DuckDB / BigQuery** — design notes only in README; v1.x for actual port.
- **No GitHub Actions CI** — v1.x.
- **No inactive records in BBCode** — leaderboard mart will carry them but consumers default-filter to active.
- **No multi-sink output abstraction** — defer until 2nd non-Sheets sink emerges.
- **No conditional 3rd "Top Scorer" line** — pre-existing backlog (HANDOFF §10 #14); v1.x.

---

## 12. Suggested kickoff prompt for the new chat

Paste this into the new chat:

```
Phase 7 v1.0 portfolio prep — continuing from prior chat. Read these in
order before doing anything:

1. Phase 7 Continuation Brief.md  ← master for current phase state
2. HANDOFF.md                     ← project master reference
3. Phase 7 Handoff.md             ← original Phase 7 brief (partially superseded)
4. Phase 7 Architecture Review.md ← analysis (partially superseded)
5. Memory files at ~/.claude/projects/.../memory/

Worktree: .claude/worktrees/phase-7-v1.0/
Branch:   claude/phase-7-v1.0
Status:   Step 1 of execution plan complete (commits 56e3c32 + 457a7a9).
          Ready to start Step 2: the bundled architecture rearchitect
          (tracked-stats seed expansion + polarity to dbt + symmetric
          active/inactive facts + holdovers cleanup).

I want to walk through the implementation chunk-by-chunk. Don't dump a
finished refactor on me — ask before making big design decisions. The
prior chat established this pattern works well for me.

Don't push to origin/main without explicit permission. Don't commit
without showing me the change first. The locked decisions in §3 of the
Continuation Brief are settled — proceed on those without
re-litigating; everything else, ask.

Suggested first move: read the Continuation Brief, then propose the
sub-chunk ordering for Step 2. The brief lists 9 sub-chunks (§5 Step 2);
you may have a different read on which to do first. Propose your read
and we'll lock it in before you start writing SQL.
```

---

## 13. Quick references

- `Phase 6.3.3 Documentation.md` — most recent shipped phase doc; good model for what a thorough phase doc looks like
- `Phase 5.0 Documentation.md` — best-shaped older phase doc
- `archive/chunk{3,4,5}_smoke.py` — Phase 6.3.3 smoke tests; useful for re-verification post any future refactor
- `output/db.py` — new module; the canonical Snowflake entry point post-Phase-7-prep
- `pytest.ini` + `tests/` — the test scaffold; 114 tests passing
- Memory files at `~/.claude/projects/C--Users-kyled-projects-espn-league-manager/memory/` — user role, project conventions, feedback patterns

---

## TL;DR

- Step 1 done. Ready to start Step 2 (the dbt rearchitect).
- All architectural decisions in §3 are locked. Don't re-litigate.
- Q1 (records.py split shape) is deferred until after Step 2.
- Be conversational, not sole-author. Walk through implementation chunks.
- Don't push or commit without explicit permission.
- Verify via `dbt build --full-refresh` + `pytest tests/` + visual BBCode spot-check.

Welcome.
