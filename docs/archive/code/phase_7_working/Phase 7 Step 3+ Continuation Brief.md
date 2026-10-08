# Phase 7 Steps 3-5 Continuation Brief — for fresh chat

**Audience:** A fresh Claude Code session picking up Phase 7 where the prior chat (which shipped Step 2, the big architectural rearchitect) left off. This doc supersedes `Phase 7 Continuation Brief.md` (which covered Step 2). Trust this doc for current state.

**Status at this doc:** Step 2 of the execution plan complete. 21 commits shipped to `claude/optimistic-euclid-8ec80e`. Worktree: `.claude/worktrees/optimistic-euclid-8ec80e/`. Local branch is N commits ahead of `origin/main`; user has NOT authorized push.

---

## 1. Reading order for orientation

Spend 20-30 minutes here before touching code:

1. **This doc** — current state + Steps 3-5 plan + open items.
2. **`HANDOFF.md`** §1-3 (orientation), §6 (code map; some entries are stale post-Step-2 — flag and fix during Step 5).
3. **`Phase 7 Continuation Brief.md`** — historical record of Step 2's locked decisions. Useful for "why is X this way" questions but DON'T treat as current — Step 2 went substantially further than the brief outlined (e.g., the post-F seed-label cleanup, the rate-stat behavior expansion in G2+G4).
4. **`Phase 7 Architecture Review.md`** — pre-Step-2 analysis. Mostly superseded; §2.5's records.py split recommendation is the kickoff point for Step 3 but the recommended shape changed (3-module not 4 — polarity collapsed into stat_catalog).
5. **Memory files** at `~/.claude/projects/C--Users-kyled-projects-espn-league-manager/memory/`:
   - `MEMORY.md` (index)
   - `feedback_background_tasks.md` — **READ THIS** — `until [ -f *.exit_code ]; do sleep N; done` polling hangs; trust the system notification instead.

---

## 2. What's shipped in Phase 7 to date

22 commits, ending at `4859146`. Steps 2 + 3 complete.

```
4859146 Phase 7 Step 3: split records.py into data + logic + thin orchestrator
4d66f75 Phase 7 Step H: drop dead/holdover models + rewire active fact off scores chain
08b2736 Phase 7 Hpre: int_player_weekly_performance from int_player_daily + negative_points rollup
dd74daf Phase 7 Step G5: rewire get_wasted_points to fct_weekly_player_inactive_performance
332a2ed Phase 7 G3: rename Python refs to fct_weekly_team_active_performance
334fe7f Phase 7 G2 + G4: collapse polarity logic into stat_catalog; widen SCORE_STAT_NAMES
4b85b8b Phase 7 Step G1: rewire formatters.STAT_DISPLAY/STAT_ABBREV to stat_catalog
c5e0d24 Phase 7 post-F cleanup: cycles archaeology + 3 latent bug fixes
5f4550a Phase 7 Step F: seed-driven Jinja UNPIVOT + performance_status partition
05761a5 Phase 7 F-prep: exclude stat 30 from is_record_candidate
f23d508 Phase 7 Step E4: promote hr_per_9 / bb_per_9 to fct columns
52cf536 Phase 7 Step E3: fct_weekly_team_inactive_performance
07aa8e3 Phase 7 Step E2: rename team active fact + transitional compat view
8d9e5f9 Phase 7 Step E1: fct_weekly_player_inactive_performance
45eac88 Phase 7 Step D3: switch active fact filter to performance_status
e2767e8 Phase 7 Step D2: rename active fact + fix contributor tiebreak flakes
25bd5de Phase 7 Step D1: performance_status + wasted_bucket flags on weekly int
75624e8 Phase 7 Step C: int_player_daily wide-daily model
1f6d365 Phase 7 Step B2: seed-query helper module
073c262 Phase 7 B1 fix: preserve recap behavior + mart tiebreak
9b0c3e3 Phase 7 Step B1: stat_classification seed expansion (Y-scope)
20f78a2 Phase 7 Step A: golden-output baseline + row-count snapshot
```

### Current architecture (7 active models + 4 seeds)

```
int_player_daily ──► int_player_weekly_performance
                       │
                       ▼
        ┌──────────────┴──────────────┐
        ▼                             ▼
fct_weekly_player_active     fct_weekly_player_inactive
        │                             │
        ▼                             ▼
fct_weekly_team_active       fct_weekly_team_inactive
        │                             │
        └─────────────┬───────────────┘
                      ▼
              mart_stat_leaderboard
              (seed-driven Jinja UNPIVOT
               of all 4 facts via UNION)
```

Down from 17 models pre-Phase-7. Symmetric active/inactive split at both grains. `negative_points` rollup column on all 4 facts. `performance_status` partition on the mart.

### Verification state at exit

- `dbt build --exclude-resource-type seed`: 63 PASS / 0 ERROR / 0 WARN.
- `pytest tests/`: **112 passed, 15 deselected** (warehouse marker excluded).
- `pytest tests/ -m warehouse`: **15 passed** (2 BBCode golden + 13 stat_catalog).
- All row counts pinned in `tests/fixtures/mart_row_counts.json`.

### Key new components shipped in Step 2

- **`dbt_league/seeds/stat_classification.csv`** — 97 rows, 13 columns. Single source of truth for stat metadata (display_name, abbrev, polarity, is_record_candidate, is_derived, derivation_expr).
- **`tools/regen_stat_classification.py`** — idempotent regen tool that rebuilds the seed CSV from Python truth. Run when STAT_DISPLAY/STAT_ABBREV equivalents change.
- **`output/stat_catalog.py`** — 6 lru_cached accessors over the seed (get_display_map, get_abbrev_map, get_polarity_map, get_always_tracked, get_record_candidates, get_derived_exprs). The seed-query helper consumers wire to.
- **`tests/test_stat_catalog.py`** — 3 pure + 13 warehouse tests.
- **`tests/test_golden_output.py`** — BBCode regression net (warehouse-marked).
- **`tests/capture_row_counts.py`** — checkpoint diagnostic for mart row counts.

---

## 3. Open architectural decisions from Step 2

Three small followups identified during Step 2; none blocking Step 3. List for your awareness:

### 3.1 `is_always_tracked` semantic conflation
The column currently does double duty:
  - (a) "force-surface in recap's new-records section even if polarity rule wouldn't" (the active downstream effect)
  - (b) "this stat is meaningful and shouldn't be filtered as noise"

For new-in-B1 rows, (b) is true but we want (a) off in v1.0. Set `is_always_tracked=false`; lose (b)'s semantic. Documented in `tools/regen_stat_classification.py`'s comment header. Fix: split into `is_record_force_surface` + `is_tracked`. Deferred to v1.x.

### 3.2 NEGATIVE_POINTS not yet in the seed
The column exists on all 4 facts (post-Hpre) but `NEGATIVE_POINTS` is NOT in the stat_classification seed as `is_record_candidate=true`. Adding it would surface "Most/Fewest Negative Points" callouts in the recap's new-records section — a real consumer-facing behavior expansion. **User's call to make in v1.x or sooner.**

### 3.3 Float-precision wobble at the display layer
The Atomic Alpacas Week 5 calculated_points line ping-ponged 126.9 ↔ 127.0 across Hpre and H. Underlying value is ~126.95; one-decimal rounding lands on opposite sides depending on summation order. Cosmetic; functionally identical. Fix: `ROUND(x, 1)` at the fact layer would eliminate the wobble entirely. Defer to v1.x or a small polish commit if it ever matters again.

### 3.4 Stat 30 = Hit for the Cycle promotion
Discovered during F-prep (15-pt scoring weight; 2 real cycle candidates over 2 seasons). Seed labels it correctly now but `is_record_candidate=false` (no fct column exists). v1.x: promote to a tracked stat with a `league_notes.py` "First cycle of the season!" callout — exactly the kind of color content the registry was built for.

---

## 4. Step 3: records.py split — SHIPPED (commit `4859146`)

`output/records.py` was 930 lines / 22 functions before this step. After Step 2's cleanup (polarity logic moved to stat_catalog; consumer queries simplified; consumer-side filters preserved), the natural seam was data-access vs everything-else.

### What landed

- **`output/records_data.py`** (377 lines) — 10 Snowflake-querying functions + their config constants. Imports only `from db import query_snowflake`. Also holds `_player_stat_value` and `_NO_PLAYER_BREAKDOWN_STATS` (see deviation 1 below).
- **`output/records_logic.py`** (382 lines) — pure consumer-side filter rules (`should_track_record`, `_orchestrator_filter`), presentation helpers (`best_or_worst_label`, `format_week_label`, `ordinal`, `_sort_new_records`), and tie-collapse (`collapse_ties`, `_collapse_one_group`, `INLINE_COLLAPSE_THRESHOLD`). One explicit `from records_data import count_value_occurrences` for the saturated-tier backfill (see deviation 2 below).
- **`output/records.py`** (238 lines) — the two workflow orchestrators (`get_records_set_this_week`, `get_records_with_contributors`) plus explicit named re-exports so consumers and tests keep working unchanged.

Re-exports cover:
- `records.foo()` (4 consumer scripts: generate_summary, generate_records_report, sheets_writer, league_notes)
- `from records import ordinal` (generate_summary.py:505)
- `records.query_snowflake` (league_notes.py:196)
- `records._player_stat_value` / `_collapse_one_group` / `_orchestrator_filter` / `_sort_new_records` (tests/test_records_pure.py reaches into these)

Used explicit named imports rather than `from records_data import *` etc. — `*` skips underscored helpers, which the test file relies on.

### Two deviations from the original §4 inventory

The plan written above this section before execution put two symbols in different modules than where they actually landed:

1. **`_player_stat_value` (+ `_NO_PLAYER_BREAKDOWN_STATS`)** ended up in `records_data.py`, not `records_logic.py`. It's pure, but its only caller is `get_team_contributors_bulk` in records_data — cohesion over the "pure-fn home" rule. Pure tests resolve through re-export and pass unchanged.

2. **`collapse_ties` / `_collapse_one_group`** stayed in records_logic but introduced an explicit `from records_data import count_value_occurrences`. The brief promised "0 Snowflake queries" in logic, which is true non-transitively but not at the import-graph level — `_collapse_one_group` needs the fct count to backfill saturated tiers (where the visible tier hits the mart's top-10 cap). Honest one-way edge; no cycle (records_data does not import from records_logic).

### Open question for v1.x

The logic→data import in deviation 2 could be replaced with dependency injection: `collapse_ties(records, max_n=5, count_fn=None)`, with the orchestrator passing `count_value_occurrences` in. That would keep records_logic truly pure and enable pure-test coverage of the saturated-tier branch (currently `tests/test_records_pure.py::TestCollapseOneGroupNonSaturated` covers the non-saturated path only; the saturated path is exercised solely via the warehouse-marked golden BBCode test). The injection signature change wouldn't actually break existing tests — `count_fn` would default to `None` with a fallback to visible `tier_size`, so all current call sites stay valid. Deferred because the current edge is explicit and cycle-free, and the saturated path is well-covered by the BBCode regression.

### Verification at exit

- `pytest tests/`: 112 passed, 15 deselected.
- `pytest tests/ -m warehouse`: 15 passed (both golden BBCode tests unchanged byte-for-byte).
- No `--no-sheets` spot-check run: `test_golden_output` subprocess-runs both scripts end-to-end and diffs against pinned baselines, which is strictly stronger.

---

## 5. Step 4: repo hygiene (~2 hr)

Per the original Continuation Brief §5 Step 4, unchanged:

- `requirements.txt` UTF-8 fix (currently UTF-16 LE; *nix-hostile). Convert + add `pytest>=7.0` + verify `pip install -r requirements.txt` works clean.
- Move root-level `test_*.py` research scripts (test_espn.py, test_kona_returns.py, etc.) into `archive/research/` or similar. Gitignored so the move is local-only; update HANDOFF.md.
- Worktree cleanup per HANDOFF §8: `git worktree remove` for stale ones. **Leave `optimistic-euclid-8ec80e/` alone — it's where this work happened.** Leave `phase-3.2/` alone (long-lived).
- `.gitignore` audit — anything missing from cached state?
- Add `LICENSE` — MIT per the original Phase 7 Handoff §2. **Confirm with user before writing.**

Single commit.

---

## 6. Step 5: public docs (~5-7 hr)

Per the original brief, unchanged:

- `CHANGELOG.md` — keepachangelog format. Map phases retroactively to semver.
- `README.md` rewrite — biggest creative chunk. Architecture diagram from §2 of this doc becomes the Mermaid diagram. The "active = fantasy reality, inactive = MLB reality" framing the user offered is a great section anchor. **Project name (Phase 7 Handoff §2 Q5) must be settled before README header.** Options: `espn-fantasy-baseball-pipeline` (descriptive), `Diamond Cuts` / `Box Score` / `Bullpen` (punchy), league-flavored.
- `SETUP.md` — bring-your-own-credentials path for new users.
- `ROADMAP.md` — Now / Next / Later / Won't Do. Pull from HANDOFF §10 + the v1.x candidates in §3 of this doc.
- dbt docs polish — fill remaining `description` fields. Add `exposures` for output scripts. `dbt docs generate` + push `target/` to `gh-pages` + enable Pages.
- Phase 7 Documentation.md — per the convention, write the final retrospective doc.
- Tag `v1.0.0` + GitHub Release.

Single doc commit per phase convention.

---

## 7. Verification approach (general; per HANDOFF §12)

1. Make changes in the worktree branch (not main).
2. `dbt build --full-refresh` if dbt models changed.
3. `pytest tests/` clean (target: 112+).
4. `pytest tests/ -m warehouse` clean (target: 15+).
5. Optional spot-check: `python output/generate_records_report.py --no-sheets` end-to-end.
6. Diff review.
7. Commit + (separate) doc commit per phase.

For background commands (dbt build, pytest -m warehouse): use `run_in_background: true` and trust the system's task-completion notification. **DON'T POLL.** See `feedback_background_tasks.md` in memory.

---

## 8. Don't-touch list

**Operational** (per HANDOFF §11 + Step 2 decisions):
- Section ordering in `generate_summary.py`
- Header conventions, player-card shape
- "Records show owner names; recap doesn't"
- 17-col Sheets schema across 3 tabs
- `SHEETS_OUTPUT_ID` opt-in, `--no-sheets` suppression
- `LeagueNote.txt` verbatim append
- mart's `most`/`fewest` direction values
- `platform_*` vs `calculated_*` distinction
- platform_points wrapper-direct exception on team active fact (per HANDOFF §7)
- The `negative_points` semantic (per-day-platform-level-summed magnitude per the Phase 7 alignment exchange).

**Collaboration**:
- **Don't push to `origin/main`** without explicit permission. Currently many commits ahead.
- **Don't commit without confirmation** when uncertain. User has been clear about wanting to review.
- **Conversational walkthroughs > sole-author dumps** for any chunk involving design choices.
- **Auto mode is active by default** — proceed autonomously on low-risk work; ask before destructive ops or design changes.

---

## 9. Worktree state

Current `git worktree list`:
- `C:/Users/kyled/projects/espn-league-manager` — main branch (untouched throughout Phase 7).
- `.claude/worktrees/optimistic-euclid-8ec80e/` — **active for Phase 7** (where all 22 commits landed).
- `.claude/worktrees/phase-3.2/` — long-lived per HANDOFF §8; leave alone.
- Possibly stale: `distracted-swirles-7aee21`, `happy-elion-8a788e`, `wizardly-wozniak-683b30`, `phase-7-v1.0` — Step 4 cleanup candidates.

---

## 10. Quick references

- `output/stat_catalog.py` — seed-query helper module (NEW post-Step-2; primary consumer wire-up point).
- `tools/regen_stat_classification.py` — seed-regen tool (run when Python truth changes; idempotent).
- `tests/test_golden_output.py` + `tests/fixtures/baseline_*.txt` — golden BBCode regression net.
- `tests/capture_row_counts.py` + `tests/fixtures/mart_row_counts.json` — mart row-count diagnostic.
- `tests/test_stat_catalog.py` — seed-helper unit + warehouse tests.

---

## 11. TL;DR for the new chat

- Steps 2 + 3 done (22 commits on `claude/optimistic-euclid-8ec80e`; ending at `4859146`).
- Architecture: 17 dbt models → 7. Seed-driven catalog. Symmetric active/inactive facts. records.py split 3 ways with backward-compat re-exports.
- Steps 4 (repo hygiene, ~2 hr) and 5 (public docs, ~5-7 hr) remain.
- Step 3 retro in §4 above includes a deferred v1.x question (inject `count_fn` into `collapse_ties` vs. the current cross-module import).
- Don't push. Don't commit without showing diff first. Don't poll background tasks (see memory).

Welcome.
