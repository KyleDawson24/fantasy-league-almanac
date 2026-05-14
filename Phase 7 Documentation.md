# Phase 7 Handoff — ESPN Fantasy Baseball Front Page Generator

## What Changed Since Phase 6.3.3

Phase 7 is the v1.0 portfolio-prep phase. After six phases of building
the product, Phase 7 was about making it presentable: rearchitecting
the dbt layer for clarity, splitting the largest output module,
cleaning up repo hygiene, and writing the public documentation needed
for a v1.0 release.

Scope:

- **Architectural rearchitect of the dbt layer**: 9 active business-logic
  models → 7 (intermediate + marts), symmetric active/inactive split at
  both player and team grain, seed-driven Jinja UNPIVOT replacing the
  hand-maintained UNION block in `mart_stat_leaderboard`, and an
  intermediate consolidation that retired four models (`int_player_
  daily_stats`, `int_player_daily_scores`, `int_team_daily_scores`,
  `fct_weekly_player_scores`) plus the `mart_wasted_points` mart.
- **Stat catalog as single source of truth**: `stat_classification.csv`
  expanded from 5 columns to 13, absorbing what used to be runtime
  Python logic (polarity merge, always-tracked overrides). Drives both
  the mart's Jinja UNPIVOT loop AND the Python display / record-
  surfacing logic via `output/stat_catalog.py`.
- **`output/records.py` split**: 930-line module → three flat files
  (`records_data.py` for SQL access, `records_logic.py` for pure rules,
  `records.py` as thin orchestrator + backward-compat re-exports).
- **Repo hygiene**: UTF-16 LE → UTF-8 LF on `requirements.txt`, MIT
  LICENSE, 10 legacy `test_*.py` research scripts moved to
  `archive/research/`, four stale worktrees pruned.
- **Public docs**: CHANGELOG (12 versions retroactively mapped), ROADMAP
  (Now / Next / Later / Decided Against), README, SETUP, dbt docs polish
  (schemas across all layers + exposures + macros docs + seeds docs +
  overview), 39 commits worth of stale-reference cleanup across the
  internal HANDOFF and inline SQL comments.

Phase 7 also addressed a latent SQL bug (generic `P` slot misclassified
in platform hit/pitch split — fixed even though no current data hit
it; portability fix) and resolved a product call (keep week-net for
the doubly-wasted-pts recap callout rather than switching to per-day
gross negatives; new `negative_points` column remains as data-layer
artifact for future gross-negative analysis).

The phase did NOT touch the recap's section ordering, BBCode header
conventions, player-card shape, 17-col Sheets schema, or any
operationally-relied-on output behavior. Golden BBCode regression tests
(introduced as Phase 7 prep) confirmed byte-identical output before and
after every architectural change.

---

## Project Structure (Current)

```
fantasy-league-front-page/
├── .env                            # gitignored; ESPN cookies + warehouse creds
├── .env.example                    # template
├── .gitignore
├── LICENSE                         # MIT (Phase 7 Step 4)
├── README.md                       # public front door (Phase 7 Step 5)
├── SETUP.md                        # bring-your-own-credentials guide
├── CHANGELOG.md                    # 12-version semver mapping
├── ROADMAP.md                      # Now/Next/Later/Decided Against
├── HANDOFF.md                      # internal handoff; tribal knowledge
├── Phase 1.0 .. Phase 7 Documentation.md
│                                   # decision log per phase
├── requirements.txt                # UTF-8 LF (Phase 7 Step 4 conversion)
├── extract/
│   └── extract.py                  # ESPN API → Snowflake RAW
├── output/
│   ├── db.py                       # Snowflake connection wrapper
│   ├── stat_catalog.py             # 6 lru_cached accessors over the seed
│   ├── records.py                  # orchestrators + re-exports (was 930
│   │                                 lines pre-Phase-7)
│   ├── records_data.py             # SQL access layer (Phase 7 Step 3)
│   ├── records_logic.py            # pure consumer-side rules (Phase 7 Step 3)
│   ├── formatters.py               # BBCode + value rendering
│   ├── league_notes.py             # color callouts (no-hitters, milestones)
│   ├── generate_summary.py         # Weekly recap
│   ├── generate_records_report.py  # All-time records report + opt-in Sheets
│   ├── sheets_writer.py            # Google Sheets sink
│   └── logs/                       # gitignored; timestamped output snapshots
├── tests/
│   ├── test_records_pure.py        # 50+ pure-function tests for records logic
│   ├── test_formatters.py          # 50+ pure tests for BBCode rendering
│   ├── test_stat_catalog.py        # seed-helper unit + warehouse tests
│   ├── test_golden_output.py       # byte-diff regression on actual BBCode
│   ├── capture_row_counts.py       # mart row-count diagnostic
│   └── fixtures/
│       ├── baseline_summary_week6.txt
│       ├── baseline_records_report.txt
│       └── mart_row_counts.json
├── tools/
│   └── analyze_negative_metrics.py # P1 #2 investigation tool (gross-vs-net)
├── archive/                        # historical reference; tracked
│   ├── README.md
│   ├── chunk{3,4,5}_smoke.py       # Phase 6.3.3 smoke tests
│   ├── diag_sheet_filter.py
│   ├── phase_6.3.3_chunk{1,2}_build.log
│   ├── *.json                      # older debug payloads
│   ├── phase_7_working/            # Phase 7 cross-session scaffolding
│   │                                 (architecture review, continuation
│   │                                 briefs, kickoff handoff -- not
│   │                                 canonical; this doc is)
│   └── research/                   # gitignored; relocated legacy test
│                                     scripts from repo root
└── dbt_league/
    ├── dbt_project.yml
    ├── packages.yml                # dbt_utils
    ├── seeds/
    │   ├── schema.yml              # Phase 7 Step 5: seed docs + tests
    │   ├── stat_classification.csv # 97 rows, 13 cols (Phase 7 B1 expansion)
    │   ├── matchup_schedule.csv
    │   ├── player_nicknames.csv
    │   └── owner_nicknames.csv     # staged for v1.x dim_player work
    ├── macros/
    │   ├── schema.yml              # Phase 7 Step 5: macro docs
    │   └── rate_stats.sql          # 10 grain-agnostic rate-stat macros
    └── models/
        ├── exposures.yml           # Phase 7: formal consumer declarations
        ├── overview.md             # Phase 7: dbt docs catalog landing page
        ├── staging/
        │   ├── schema.yml
        │   ├── sources.yml
        │   ├── stg_box_scores.sql
        │   ├── stg_player_stat_breakdowns.sql
        │   └── stg_scoring_settings.sql
        ├── intermediate/
        │   ├── schema.yml
        │   ├── int_player_daily.sql              # NEW Phase 7 Step C;
        │   │                                       wide-daily model
        │   │                                       (consolidates the retired
        │   │                                       int_player_daily_stats +
        │   │                                       int_player_daily_scores)
        │   └── int_player_weekly_performance.sql
        └── marts/
            ├── schema.yml
            ├── fct_weekly_player_active_performance.sql
            ├── fct_weekly_player_inactive_performance.sql # NEW
            ├── fct_weekly_team_active_performance.sql
            ├── fct_weekly_team_inactive_performance.sql   # NEW
            └── mart_stat_leaderboard.sql         # rewritten as seed-driven
                                                    Jinja UNPIVOT (Phase 7 F)
```

10 active dbt models (3 staging + 2 intermediate + 5 marts), down from
12 pre-Phase-7 in absolute terms but with two new symmetric inactive
facts added; the 9 → 7 business-logic-model framing in the rest of
this doc counts int+marts only, which is the more meaningful number.

---

## What Was Built in Phase 7

Phase 7 was executed across an 8-step dbt rearchitect (Steps A through
H) plus three broader-scope steps (records.py split, repo hygiene,
public docs). Each had its own commit cluster.

### Step A: Golden-output regression baseline

Pinned a Week 6 2026 BBCode summary and the full records report to
`tests/fixtures/baseline_*.txt`. Wrote `tests/test_golden_output.py`
which subprocess-runs the output scripts and byte-diffs against the
baselines. This safety net was the entire reason every subsequent
rearchitect step could ship confidently — if Step E inadvertently
broke a contributor line ordering, the golden test would catch it
before commit.

Marked as `@pytest.mark.warehouse` so it only runs when explicitly
selected (`pytest -m warehouse`). Each test takes ~30-60s end-to-end
because it hits Snowflake.

### Step B1+B2: Stat catalog seed expansion

`stat_classification.csv` was 5 columns pre-Phase-7. B1 expanded to 13,
absorbing what had been runtime Python logic:

- `display_name`, `abbrev` — moved from `formatters.STAT_DISPLAY` and
  `formatters.STAT_ABBREV` dicts
- `polarity` — pre-merged from `records.get_stat_polarity()` (which read
  `stg_scoring_settings.points_per_unit` sign) plus
  `records._IMPLICIT_POLARITY` (hardcoded overrides for rate stats,
  derived stats, score columns)
- `is_record_candidate` — pre-merged from
  `should_track_record('team', stat, 'most', polarity_map, always_tracked)`
- `is_derived`, `derivation_expr` — for the four derived counting stats
  (PA, SB_CS, W_L, SV_BLSV) so the eventual Jinja UNPIVOT loop could
  inline their expressions
- `is_always_tracked` — moved from `records.get_always_tracked_stats()`

17 new rows added for stats that appeared in the mart leaderboard
UNPIVOT but weren't in the seed: the 4 derived stats, 6 rate stats
(ERA/WHIP/K_PER_9/K_PER_BB/HR_PER_9/BB_PER_9), 1 mart-only
(WASTED_POINTS), and 6 score columns (CALCULATED_*, PLATFORM_*).

B2 added `output/stat_catalog.py` with 6 `lru_cached` accessors —
`get_display_map()`, `get_abbrev_map()`, `get_polarity_map()`,
`get_always_tracked()`, `get_record_candidates()`, `get_derived_exprs()`
— that consumers read from. The mart leaderboard SQL and the Python
output scripts both read from the same single source.

### Step C: `int_player_daily` wide-daily model

Pre-Phase-7 had two daily models: `int_player_daily_stats` (long-form
per-stat-per-day) and `int_player_daily_scores` (per-player platform
totals). Step C added `int_player_daily` (wide-daily, one row per
(season, scoring_period, team, player, lineup_slot)) that consolidated
both: counting stats, per-stat `*_pts` columns, platform totals,
display metadata.

Materialized as view (daily layer reads fine live at ~600K rows; the
table cost lives at the weekly layer).

### Steps D1-D3: Active fact rename + filter switch

D1 added `performance_status` and `wasted_bucket` flag columns to
`int_player_weekly_performance` so downstream facts could filter on
explicit semantic names rather than `lineup_slot NOT IN (...)`.

D2 renamed `fct_weekly_player_performance` →
`fct_weekly_player_active_performance` (making the active/inactive
symmetry that E1/E3 added explicit). A transitional compat view at the
old name kept Python consumers working between D2 and G3's rewire; it
was dropped in Step H.

D3 switched the active fact's filter from the slot-enumeration form
to `performance_status = 'active'`. Same row set; cleaner SQL.

D2 also added `display_name` as a tiebreak token in the
`get_team_contributors` query and in the mart's tiebreak chain. Without
it, contributors-with-equal-stat-value rows came back in
implementation-defined order, which flipped on `--full-refresh`.

### Steps E1-E4: Inactive facts + rate stat promotion

E1 added `fct_weekly_player_inactive_performance` (symmetric counterpart
to the active fact, for BE/IL/FA-slot performance). Grain includes
`wasted_bucket` so a player who appears as both ROSTERED_INACTIVE on
team A AND FA in the same matchup_period (rare: drop mid-week) gets
two rows.

E2 renamed the team active fact (`fct_weekly_team_performance` →
`fct_weekly_team_active_performance`); same transitional-compat-view
pattern as D2.

E3 added `fct_weekly_team_inactive_performance` (team-grain rollup of
the player-inactive fact). Two row flavors per matchup: N
ROSTERED_INACTIVE rows (one per fantasy team) plus 1 FA row (league-
wide; team_id NULL).

E4 promoted HR/9 and BB/9 from mart-inline calculations to actual
columns on the team active fact, matching the existing era/whip/k_per_9
treatment. Made them addressable by name in the seed-driven Jinja
UNPIVOT that Step F introduced.

### Step F: Seed-driven Jinja UNPIVOT for `mart_stat_leaderboard`

The leaderboard's wide-to-long pivot was pre-Phase-7 a hand-maintained
~50-row UNION ALL block in SQL. Step F replaced it with a Jinja loop
over `stat_classification` (filtered to `is_record_candidate=true`)
that emits the UNION at compile time. Adding a tracked stat is now a
CSV row.

The mart picks up four source CTEs (team_active, team_inactive,
player_active, player_inactive) and union-alls them with a
`performance_status` partition column. Consumers default-filter to
active; the inactive rows are carried for ad-hoc analysis.

### Steps G1-G5: Consumer rewires

G1 rewired `formatters.STAT_DISPLAY` and `STAT_ABBREV` to read from
`stat_catalog.get_display_map()` / `get_abbrev_map()` instead of
hardcoded dicts.

G2 + G4 collapsed `records.get_effective_polarity()` and
`records._IMPLICIT_POLARITY` into the seed (the seed now stores the
already-merged polarity value). Also widened `SCORE_STAT_NAMES` to
include PLATFORM_* (the formatters already had them; the
SCORE_STAT_NAMES omission was a latent rendering bug that surfaced
once any platform record bubbled up).

G3 renamed Python references from `fct_weekly_team_performance` to
`fct_weekly_team_active_performance` and removed the transitional
compat view.

G5 rewired `get_wasted_points` to read from
`fct_weekly_player_inactive_performance` instead of the now-retiring
`mart_wasted_points`.

### Step H: Drop dead models

After all the rewires, six models had no consumers and got dropped:
`int_team_daily_scores`, `int_player_daily_scores`,
`int_player_daily_stats`, `fct_weekly_player_scores`,
`mart_wasted_points`, `fct_weekly_team_performance` (the compat view).

H also rewired the active fact off the legacy scores chain — pulled
`platform_points`, `platform_hitting_pts`, `platform_pitching_pts`,
`display_name` from `int_player_weekly_performance` directly. The
"join `fct_weekly_player_scores` for platform totals" pipeline step
was eliminated.

### Step 3: `output/records.py` split

`records.py` was 930 lines and 22 functions, mixing SQL access with
pure consumer-side rules and high-level orchestration. Split into:

- `records_data.py` (377 lines) — 10 Snowflake-querying functions:
  bulk rank-1 record fetches, contributor lookups, league-history
  counts, schedule lookup. Plus `_player_stat_value` and
  `_NO_PLAYER_BREAKDOWN_STATS` (moved here from logic since their only
  caller, `get_team_contributors_bulk`, lives in data).
- `records_logic.py` (382 lines) — pure consumer-side rules:
  `should_track_record`, `best_or_worst_label`, `format_week_label`,
  `ordinal`, `_sort_new_records`, `collapse_ties`,
  `_collapse_one_group`, plus `SCORE_STAT_NAMES` and
  `INLINE_COLLAPSE_THRESHOLD`. One explicit `from records_data import
  count_value_occurrences` for the saturated-tier backfill (honest
  logic→data edge; no cycle since data doesn't import from logic).
- `records.py` (238 lines) — the two workflow orchestrators
  (`get_records_set_this_week`, `get_records_with_contributors`) plus
  explicit named re-exports so the four consumer scripts and
  `tests/test_records_pure.py` keep working without import changes.

Re-exports use explicit names rather than `from records_data import *`
because tests reference underscored private helpers (`_player_stat_value`,
`_collapse_one_group`, `_orchestrator_filter`, `_sort_new_records`)
that `*` skips.

### Step 4: Repo hygiene

- `requirements.txt`: UTF-16 LE with CRLF → UTF-8 with LF, re-sorted
  alphabetically, added `pytest>=7.0` (was missing despite being used
  by `pytest.ini`-driven tests). Caught a stray embedded BOM mid-file
  from a prior pip-freeze paste; stripped.
- 10 legacy root-level `test_*.py` research scripts (test_espn.py,
  test_kona_returns.py, etc. — live API hitters not safe for CI) moved
  to `archive/research/` (local-only; gitignored). Repo root is now
  clean of one-off research artifacts.
- `.gitignore`: replaced root-anchored `/test_*` rule with
  `archive/research/`. Comment cleaned up.
- MIT LICENSE added.
- Four stale worktrees pruned (`distracted-swirles-7aee21`,
  `happy-elion-8a788e`, `wizardly-wozniak-683b30`, `phase-7-v1.0`).
  All commits already reachable from
  `claude/optimistic-euclid-8ec80e`.

### Step 5: Public docs

Substantive creative work:

- **CHANGELOG.md** — keepachangelog.com 1.1.0 format. 12-version
  semver mapping from 0.1.0 (Phase 1) → 1.0.0 (Phase 7). Dates anchored
  to commit-add dates of each `Phase X.Y Documentation.md`.
- **ROADMAP.md** — Now / Next / Later / Decided Against framing.
  Includes the v1.x flagship item `dim_player + fct_player_career`
  per a separate review pass.
- **README.md** — recruiter/peer-facing front door. Sample BBCode
  output, Mermaid architecture diagram, 5 notable engineering decisions
  with phase doc links, "what this demonstrates" inventory.
- **SETUP.md** — bring-your-own-credentials walkthrough. 10 sections
  including ESPN cookies, Snowflake provisioning, dbt profile, first
  run, optional Google Sheets sink.
- **dbt docs polish** — `schema.yml` across staging / intermediate /
  marts (with description + tests on every model and most columns),
  `exposures.yml` for the three downstream consumers, `overview.md`
  for the dbt docs catalog landing page, `macros/schema.yml` for the
  rate-stat macros, `seeds/schema.yml` for the 4 seed files.
- **Stale-reference cleanup** — multi-commit sweep removing
  present-tense references to retired models (`int_player_daily_stats`,
  `mart_wasted_points`, `fct_weekly_player_scores`, etc.) from
  HANDOFF.md, inline SQL comments, `dbt_project.yml` comments, and
  three Python consumer files.

Two architectural fixes also landed during Step 5:

- **P1 fix: generic `P` slot misclassification.** `int_player_daily`
  was checking only `('SP', 'RP')` when splitting platform_points into
  hitting/pitching; `stg_box_scores` defines pitcher slots as
  `('SP', 'RP', 'P')`. Latent in this league (zero P-slot rows in data)
  but a real correctness/portability fix.
- **Architecture rewire: `get_wasted_points` reaches up one layer.**
  Per a separate review pass, `generate_summary.py`'s `player_meta`
  CTE was reaching into `stg_box_scores` directly for pro_team,
  position, eligible_slots. Added `eligible_slots` pass-through to
  `int_player_daily` (the other two were already there); switched the
  CTE source. Reach moves one layer up; consistent with the v1.x
  `dim_player` work that absorbs this concern formally.

---

## Key Technical Decisions

### Active/inactive symmetric fact split rather than single fact with status column

**Options considered:**
- (a) One fact (`fct_weekly_player_performance`) with `performance_status`
  column; consumers filter as needed
- (b) Two symmetric facts (`fct_weekly_player_active_performance` +
  `fct_weekly_player_inactive_performance`)

**Chosen:** (b).

**Why:** The active and inactive halves diverge in what columns make
sense. Active facts carry `platform_*` columns (ESPN's reported team
total includes only active-slot contributions; SUM(active player
platform_pts) is meaningful). Inactive facts can't carry platform_*
meaningfully — the column would always be misleading. Forcing one fact
to host columns where half the rows are NULL-by-definition is the
classic "anti-pattern of putting the union-shaped data into a single
relation."

Inactive facts also have a different grain (carry `wasted_bucket`).
Single-fact would require the bucket to be NULL on active rows,
producing the same anti-pattern.

The split makes consumer queries cleaner:
`SELECT * FROM fct_weekly_player_active_performance WHERE ...` reads
better than filtering by status column every time. And
`mart_stat_leaderboard` can union them with explicit semantic columns
attached to each side.

### Seed-driven Jinja UNPIVOT vs hand-maintained UNION

**Options considered:**
- (a) Keep hand-maintained UNION (one block per stat in mart SQL)
- (b) Jinja loop over seed; emit UNION at compile time
- (c) Snowflake-native UNPIVOT (a real SQL statement, not a Jinja
  expansion)

**Chosen:** (b).

**Why:** (a) is what we had pre-Phase-7. Adding a tracked stat required
editing ~5 separate SQL spots (the mart, the formatters dicts, the
records polarity overrides, etc.). High coordination cost; easy to
forget.

(c) would be cleanest from a SQL portability angle but Snowflake's
UNPIVOT statement has limitations on multi-grain or conditional logic
that the rankings need. The mart has per-stat where-clauses (rate
stats only at team grain, etc.) that don't slot into native UNPIVOT.

(b) — Jinja loop over the seed — gives compile-time SQL generation
(no runtime cost) while letting the seed be the single source of truth.
The mart re-emits its UNION block from the seed via `run_query`;
adding a stat is a CSV row + reseed.

Downside: dbt-runtime-dependent in a way that bites cross-warehouse
ports (the Jinja loop fires at compile time, so changing seeds requires
`dbt seed && dbt run` rather than the more idiomatic `dbt build`). Cost
acceptable for v1.0.

### `records.py` 3-module split

**Options considered:**
- (a) Leave as one file (930 lines, but currently working)
- (b) 3-module flat split: data / logic / orchestrator
- (c) 4-module split: data / logic / orchestrators-as-separate / re-exports

**Chosen:** (b).

**Why:** The natural seam after Step 2's cleanup (polarity moved to
seed; runtime logic simplified) was data-access vs everything-else.
Within "everything-else," the workflow orchestrators (`get_records_
set_this_week`, `get_records_with_contributors`) compose data calls
with pure logic helpers — not enough surface area to warrant their
own module. (c)'s separation would have added a module for two
functions.

(a) was defensible but loses the "I refactored a god module" portfolio
talking point. Phase 7 is portfolio-prep; the split signals the
discipline.

Tests reference underscored private helpers, so re-exports use
explicit named imports (not `from X import *`) — `*` skips underscored
symbols.

### Stay week-net for doubly_wasted_pts (vs switching to gross-per-day)

**Options considered:**
- (a) Keep week-net: `GREATEST(0, -SUM(active_platform_points))` —
  the current calc. Player at +95 net for the week with one -5 day
  contributes 0 to wasted.
- (b) Switch to gross-per-day: SUM of per-day negative magnitudes via
  the new `negative_points` column. Same player would contribute 5
  ("you should have benched him on that day").

**Chosen:** (a).

**Why:** The recap's framing is "this player hurt you overall for the
week." Switching to gross-per-day shifts the framing to "you should
have known to bench him on this specific day" — too harsh for an
amateur fantasy league where day-by-day lineup management isn't the
norm. The separate review-pass investigation pulled a top-10 by gross-
negative-active and showed 9 of 10 were relievers (the classic "one
bad outing + several decent ones" pattern); surfacing gross would
spam the recap with reliever callouts.

The `negative_points` column added to all four fact tables remains as
a data-layer artifact for future analysis. If a consumer ever wants
the gross framing, the data is there.

### Public-docs current-state framing (drop Phase prefixes from schema.yml)

**Options considered:**
- (a) Keep Phase X.Y prefixes in dbt schema.yml descriptions as
  change-log markers (e.g., "Phase 6.3.3 chunk 3 added this column")
- (b) Drop Phase prefixes; describe what each model/column IS in
  current state

**Chosen:** (b).

**Why:** Schema.yml descriptions render in the dbt docs catalog, which
is consumed by current and future users (whoever's exploring the data
model). They want to know what each thing IS, not what it changed
from. The historical change record is preserved in the
`Phase X.Y Documentation.md` files; cross-referencing them as
chronological markers in schema.yml is double-bookkeeping that
confuses the catalog reader.

After v1.0 ships, future schema changes might justify brief "Note:
previously called X" comments to help users migrating between versions
— but for the v1.0 baseline, current-state framing throughout.

### "Decided Against" rather than "Won't Do" in ROADMAP

**Options considered:**
- (a) Standard "Won't Do" framing (common in roadmap conventions)
- (b) "Decided Against" — softer phrasing

**Chosen:** (b).

**Why:** Per user feedback during ROADMAP review. "Won't Do" reads as
dismissive without context; "Decided Against" implies "we considered
this and the answer is no for these reasons," which is what the
section actually contains. Cosmetic but matches the project's tone.

---

## What's in Snowflake (Current)

**Schemas:**
- `RAW`: append-only landing zone. Box scores JSON, scoring settings,
  matchup schedule. `extract.py` writes here.
- `ANALYTICS`: dbt-managed. Staging + intermediate + marts materializes
  here.

**dbt models** (10 total):

Staging (3, view):
- `stg_box_scores`
- `stg_player_stat_breakdowns`
- `stg_scoring_settings`

Intermediate (2, view):
- `int_player_daily`
- `int_player_weekly_performance`

Marts (5, table/view mix):
- `fct_weekly_player_active_performance` (incremental)
- `fct_weekly_player_inactive_performance` (incremental)
- `fct_weekly_team_active_performance` (incremental)
- `fct_weekly_team_inactive_performance` (table)
- `mart_stat_leaderboard` (view)

**dbt tests:** 67 data tests across schema.yml files. Notable:
- `accepted_values` on `mart_stat_leaderboard.{entity_grain,
  performance_status, stat_name, record_scope, record_direction}` —
  catches any unexpected partition value.
- `dbt_utils.unique_combination_of_columns` on every fact's grain.
- 15 seed-level tests (added Phase 7 Step 5).
- 2 byte-diff golden BBCode regression tests (added Phase 7 Step A).

---

## Verification

At exit (commit `49625d5` — last technical commit before this doc and
the v1.0.0 tag):

- `dbt build --exclude-resource-type seed`: 45 PASS / 0 ERROR.
- `pytest tests/`: 112 PASS, 15 deselected.
- `pytest tests/ -m warehouse`: 15 PASS (2 golden BBCode + 13
  `stat_catalog` warehouse tests).
- Both golden BBCode regression tests confirm byte-identical output
  pre- and post-rearchitect.

---

## Open Investigations Carried Forward

Items surfaced during Phase 7 that didn't ship in v1.0:

1. **`is_always_tracked` semantic conflation.** The seed column does
   double duty: (a) "force-surface in recap regardless of polarity" and
   (b) "this stat is meaningful and shouldn't be filtered as noise."
   For new-in-B1 rows, (b) is true but (a) should be off. Set
   `is_always_tracked=false` for v1.0; the semantic conflict remains
   for v1.x cleanup. Fix: split into `is_record_force_surface` +
   `is_tracked`.

2. **`NEGATIVE_POINTS` not yet a record candidate.** The column exists
   on all four facts (post-Hpre) but isn't flagged
   `is_record_candidate=true`. Surfacing "Most/Fewest Negative Points"
   callouts would be a real consumer-facing expansion.

3. **Float-precision wobble at display layer.** Some
   `calculated_points` values ping-pong by 0.1 across `--full-refresh`
   runs (summation order varies). Cosmetic; `ROUND(x, 1)` at the fact
   layer would fix.

4. **Stat 30 = Hit for the Cycle.** Discovered during F-prep (15-pt
   scoring weight, 2 real candidates over 2 seasons). Seed labels it
   correctly now but `is_record_candidate=false` (no fct column).
   v1.x: promote to tracked stat with a `league_notes.py` callout.

5. **`PLATFORM_*` in `SCORE_STAT_NAMES`.** Phase 7 G2+G4 widened
   `SCORE_STAT_NAMES` to include PLATFORM_*; this may surface platform
   records alongside calculated records in the recap, muddying the
   "calculated = normalized lens; platform = ESPN authority" framing.
   Product call, deferred for separate discussion.

6. **Inactive fact grain edge case.** `fct_weekly_player_inactive_
   performance` is keyed by `(season_year, matchup_period, player_id,
   wasted_bucket)`, using `max(team_id)` for the edge case where a
   player appears under multiple teams in one bucket within a matchup
   (mid-week drop + re-add on a different team). Rare; ROADMAPed.

---

## Bookmarks for Future Work

See `ROADMAP.md` for the full forward-looking record. Top v1.x flagship:

- **`dim_player` + `fct_player_career`.** Build the player-as-entity
  layer the project hasn't had yet. Absorbs the `get_wasted_points`
  staging-reach concern formally; unlocks "career milestone" callouts
  in the recap; sets foundation for player-profile analytics.
  Framed as a v1.x proof-of-progress — scaffolding ships value even
  if full functionality lands in v2.x.

v2.0 candidates clustered around portability:
- Yahoo / Sleeper extract paths (cross-platform extract)
- DuckDB target (no-cloud-warehouse alternative)
- Externalized tracked-stats config (cross-league portability)

---

## Migration Notes for Next Session

Working environment:
- Branch: `claude/optimistic-euclid-8ec80e`
- Worktree: `.claude/worktrees/optimistic-euclid-8ec80e/`
- Is being pushed to `origin/main` alongside the v1.0.0 tag as the
  closing step of Phase 7.

Conventions established in Phase 7 that should persist:
- `git mv` for tracked-file renames so history survives.
- Explicit named re-exports (not `*`) when consumer surfaces need to
  preserve underscored private symbols.
- "Current-state framing" for schema.yml descriptions; chronology
  belongs in CHANGELOG and phase docs.
- "Decided Against" not "Won't Do" in ROADMAP.
- Verification per change: `dbt build --select <model>+` + warehouse
  pytest for any change touching consumer scripts.

External coordination:
- A separate review pass reviewed Phase 7 Step 2 architecture
  pre-execution and reviewed the wasted-points staging-reach question
  during Step 5. Its input shaped the `dim_player` v1.x flagship
  framing.

---

## Git History (commits in Phase 7)

39 commits on `claude/optimistic-euclid-8ec80e` (oldest first):

```
56e3c32 Phase 7 prep: test scaffold, connection consolidation, output polish
457a7a9 Phase 7 architecture review (working doc)
dbc0318 Phase 7 continuation brief (working doc) for fresh-chat resumption
20f78a2 Phase 7 Step A: golden-output baseline + row-count snapshot
9b0c3e3 Phase 7 Step B1: stat_classification seed expansion (Y-scope)
073c262 Phase 7 B1 fix: preserve recap behavior + mart tiebreak
1f6d365 Phase 7 Step B2: seed-query helper module
75624e8 Phase 7 Step C: int_player_daily wide-daily model
25bd5de Phase 7 Step D1: performance_status + wasted_bucket flags on weekly int
e2767e8 Phase 7 Step D2: rename active fact + fix contributor tiebreak flakes
45eac88 Phase 7 Step D3: switch active fact filter to performance_status
8d9e5f9 Phase 7 Step E1: fct_weekly_player_inactive_performance
07aa8e3 Phase 7 Step E2: rename team active fact + transitional compat view
52cf536 Phase 7 Step E3: fct_weekly_team_inactive_performance
f23d508 Phase 7 Step E4: promote hr_per_9 / bb_per_9 to fct columns
05761a5 Phase 7 F-prep: exclude stat 30 from is_record_candidate
5f4550a Phase 7 Step F: seed-driven Jinja UNPIVOT + performance_status partition
c5e0d24 Phase 7 post-F cleanup: cycles archaeology + 3 latent bug fixes
4b85b8b Phase 7 Step G1: rewire formatters.STAT_DISPLAY/STAT_ABBREV to stat_catalog
334fe7f Phase 7 G2 + G4: collapse polarity logic into stat_catalog; widen SCORE_STAT_NAMES
332a2ed Phase 7 G3: rename Python refs to fct_weekly_team_active_performance
dd74daf Phase 7 Step G5: rewire get_wasted_points to fct_weekly_player_inactive_performance
08b2736 Phase 7 Hpre: int_player_weekly_performance from int_player_daily + negative_points rollup
4d66f75 Phase 7 Step H: drop dead/holdover models + rewire active fact off scores chain
9a895d9 Phase 7 Steps 3-5 continuation brief for fresh-chat resumption
4859146 Phase 7 Step 3: split records.py into data + logic + thin orchestrator
1e87286 Phase 7 Step 3 brief update: §4 SHIPPED + deviations + v1.x note
73bc195 Phase 7 Step 4 safe-tier: requirements.txt UTF-8 + relocate research scripts
a3e23e1 Phase 7 Step 4: add MIT LICENSE
7ce6bb4 Phase 7 Step 5: archive Phase 7 working docs
6c17e9c Phase 7 Step 5: add CHANGELOG.md
5b0483b Phase 7 Step 5: add ROADMAP.md
0fb733f Phase 7 Step 5: dbt schema.yml cleanup pass
b46112a Phase 7 architecture: rewire wasted-points consumer + add exposures + player-mart ROADMAP
2e2b757 Phase 7 architecture: fix generic-P-slot misclassification in platform hit/pitch split
284774e Phase 7 architecture: stale model-reference cleanup + delete broken regen tool
3f25824 Phase 7 Step 5: add dbt_league/seeds/schema.yml
be83e79 ROADMAP: add v1.x "auto-populate matchup_schedule from ESPN settings"
894edff Phase 7 Step 5: add models/overview.md (dbt docs catalog landing page)
27c70ae Phase 7 Step 5: add dbt_league/macros/schema.yml
4ce027a Phase 7 Step 5: add SETUP.md + fix exposures.yml repo URLs
68d186f Phase 7 Step 5: add README.md
49625d5 overview.md: align H1 with README's "Fantasy Beat Reporter" branding
```

(This doc and the v1.0.0 tag will close the phase.)
