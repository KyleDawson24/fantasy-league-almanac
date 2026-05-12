# Phase 7 — North Star + Architecture Review

**Status:** Working doc, internal. Will be deleted (or superseded) when v1.0 ships. Read this top-to-bottom; the open questions at the end are the only thing requiring a response from you.

**Goal:** Anchor the dbt-vs-Python boundary, name the v1.0-worthy refactors, identify operational risk, and propose a test story — *before* we start rearranging code.

---

## Part 1 — North Star

A 1-page contract we hold ourselves to during the refactor. Every cleanup proposal below should make at least one of these answers easier to deliver crisply in an interview.

### What is this?

An ELT analytics pipeline for fantasy baseball. ESPN's API is the messy source; Snowflake + dbt is the modeling layer; Python is the presentation layer that produces a weekly recap, a records report, and a shared Google Sheet for league members.

The current public framing ("front-page generator") undersells the work. The repo is closer to a **fantasy baseball analytics pipeline** that produces multiple consumer-facing artifacts. The recap is one of three sinks; the engineering substance is the model.

### What does dbt own?

Things that are **analytical truths** about the league, expressible declaratively, queryable from any BI tool, and stable across consumers:

- Grains (player-week, team-week, per-stat-rank)
- Scoring derivation under current weights (`calculated_*`)
- Normalized facts and reusable marts
- Cross-season comparability
- "Is this stat a record candidate?" *if* we promote it (currently Python — see §3.2)
- Polarity *if* we promote it (currently Python — see §3.2)
- Anything that says "what is true about the league as data"

### What does Python own?

Things that are **presentation choices** specific to one consumer surface:

- BBCode rendering (recap, records report)
- Sheets row shape, header schema, sort cohorts
- Tie-collapse display thresholds (`INLINE_COLLAPSE_THRESHOLD`, `INLINE_TIER_LIMIT`)
- Contributor list display rules (top-3, names-vs-stats, counts-vs-pts)
- Stat label maps (`STAT_DISPLAY`, `STAT_ABBREV`)
- Narrative callouts (`league_notes.py` registry)
- Anything that says "how this appears to a human in this output"

### v1.0 promise

A reviewer cloning the repo can:

1. Read the README and understand the project's substance in 60 seconds.
2. Set up the pipeline with their own ESPN cookies + Snowflake creds in <30 minutes via `SETUP.md`.
3. Find the dbt models documented (descriptions + lineage on GitHub Pages).
4. Find the analytical seam between dbt and Python clearly stated.
5. Run the test suite (`pytest`) and have it pass without a Snowflake connection.
6. Track the version history via `CHANGELOG.md` and `git tag v1.0.0`.

### Explicitly deferred (v1.x or later)

- DuckDB / BigQuery target. v1.0 ships design notes only (see §3.3).
- MetricFlow Semantic Layer.
- GitHub Actions CI.
- Sheets formatting preservation (separate task).
- Multi-sink output abstraction (until a 2nd non-Sheets sink arrives).
- Hosted multi-tenant SaaS.
- "Conditional 3rd Top Scorer line" (pre-existing backlog).

---

## Part 2 — Architecture Review

### 2.1 — Module-by-module: what each owns

#### `output/records.py` — 1,028 lines, six+ concerns

Currently the busiest file in the repo. The module docstring says "pure data-access functions"; in practice it's data-access + polarity rules + filter rules + sort + tie-collapse + schedule labels + count helpers + an orchestrator. Six logical sections, all tightly coupled by shared imports rather than enforced module boundaries:

1. **Infrastructure** — `query_snowflake`, `SNOWFLAKE_CONFIG` ([records.py:45-98](output/records.py:45))
2. **Mart fetches** — `get_all_time_records`, `get_current_season_records`, `get_record_top_n`, `get_tracked_team_stats`, plus single + bulk contributor fetches ([records.py:101-189](output/records.py:101) and [records.py:677-782](output/records.py:677))
3. **Polarity + filter rules** — `get_stat_polarity`, `_IMPLICIT_POLARITY`, `get_effective_polarity`, `best_or_worst_label`, `get_always_tracked_stats`, `should_track_record`, `_orchestrator_filter` ([records.py:194-360](output/records.py:194) and [records.py:785-797](output/records.py:785))
4. **New-record detection** — `get_records_set_this_week` (combines fetches + polarity + counts) ([records.py:365-434](output/records.py:365))
5. **Count helpers** — `count_value_occurrences`, `league_history_count`, derived-stat expressions ([records.py:437-508](output/records.py:437))
6. **Schedule labels** — `load_schedule_lookup`, `format_week_label` ([records.py:629-655](output/records.py:629))
7. **Sort + tie-collapse** — `_sort_new_records`, `collapse_ties`, `_collapse_one_group`, `INLINE_COLLAPSE_THRESHOLD` ([records.py:538-566](output/records.py:538) and [records.py:881-1028](output/records.py:881))
8. **Orchestrator** — `get_records_with_contributors` (combines everything) ([records.py:807-878](output/records.py:807))
9. **Cross-cutting helper** — `ordinal` ([records.py:511-520](output/records.py:511))

**Tension:** the module docstring says "no formatting or display decisions in this module," but tie-collapse and sort orderings *are* presentation decisions (driven by the display cap). The orchestrator that calls them produces records with a 'contributors' key shaped specifically for the Sheets writer's downstream consumption.

#### `output/generate_summary.py` — 866 lines

Weekly recap orchestration. Mostly cohesive but has:
- A duplicate copy of `query_snowflake` + `SNOWFLAKE_CONFIG` ([generate_summary.py:38-58](output/generate_summary.py:38)) — same code as records.py.
- Top-of-file UTF-8 stdout reconfig ([generate_summary.py:14-19](output/generate_summary.py:14)) — duplicate of `generate_records_report.py`.
- Direct fct queries (`get_weekly_scores`, `get_player_contributions`, `get_wasted_points`) that arguably belong with the records module's data-access surface.
- Mixed concerns within: orchestration in `__main__`, data fetches as module functions, format_* functions, and the analysis functions (`find_tough_luck`, `find_lucky_bastard`, `check_fair_and_just`, `find_top_scorer/hitter/pitcher`) all live side-by-side.

#### `output/generate_records_report.py` — 267 lines

All-time records BBCode + Sheets opt-in trigger. Cleaner than `generate_summary.py`. Notable:
- `--no-sheets` flag already shipped (commit `360fa8f`).
- Has its own `STAT_ORDER` ([generate_records_report.py:70-82](output/generate_records_report.py:70)) — distinct from `records._DISPLAY_ORDER` but overlapping content.
- Has its own `_REPORT_EXCLUDED_STATS` set ([generate_records_report.py:91-95](output/generate_records_report.py:91)) — distinct from `records._NO_PLAYER_BREAKDOWN_STATS` and `records._TEAM_NON_SEED_STATS`.
- `INLINE_TIER_LIMIT = 3` ([generate_records_report.py:46](output/generate_records_report.py:46)) duplicates the value (and rough purpose) of `records.INLINE_COLLAPSE_THRESHOLD = 3`.

#### `output/formatters.py` — 380 lines

Mostly the right shape: stat-line renderers, label maps, value formatters, slot filtering, the contributor-list algorithm. Notable:
- `_SCORE_STAT_KEYS` ([formatters.py:211-214](output/formatters.py:211)) is defined here as a deliberate decoupling from records.py — the comment says "Hardcoded here rather than imported from records.py to keep formatters free of cross-module deps." That's defensible but creates drift risk: 3 different "what's a score column" definitions exist (this one, `records.SCORE_STAT_NAMES`, and `sheets_writer._SCORE_LABEL_ORDER`), and they don't all contain the same set of names.
- `format_contributors` ([formatters.py:238-291](output/formatters.py:238)) implements the same tier-collapse algorithm shape as `records.collapse_ties` — different inputs, same idea. Likely could share an underlying helper.

#### `output/sheets_writer.py` — 351 lines

Cohesive; one job (write 3 tabs). Uses records.py heavily as its data-access surface. Two operational notes:
- `_replace_tab` does `worksheet.clear() + update()` — wipes user formatting. Documented limitation per HANDOFF roadmap.
- Has `_SCORE_LABEL_ORDER` ([sheets_writer.py:70-75](output/sheets_writer.py:70)) — third definition of "what's a score column."

#### `output/league_notes.py` — 220 lines

Registry pattern. Clean. Imports `records.query_snowflake` directly to issue 2 more queries in `build_ctx` ([league_notes.py:193-220](output/league_notes.py:193)), and uses several records.py helpers (`ordinal`, `league_history_count`, `format_week_label`).

#### dbt models

In good shape based on `mart_stat_leaderboard.sql`:
- Schema-yml descriptions are populated for the mart layer (some staging/intermediate gaps may exist; will check during 7.2).
- The mart's `record_direction` is intentionally direction-agnostic with polarity-aware filtering at the consumer side — the right call. Documented well.
- ~50 stat columns hardcoded into the team UNPIVOT and player UNPIVOT lists ([mart_stat_leaderboard.sql:141-153](dbt_league/models/marts/mart_stat_leaderboard.sql:141)). Adding/removing a tracked stat requires editing both lists. The "tracked-stats config seed/YAML" backlog item exists for this. This is the strongest case for the seed-driven config refactor.
- UNPIVOT is Snowflake-specific ([mart_stat_leaderboard.sql:23-27](dbt_league/models/marts/mart_stat_leaderboard.sql:23)) — already documented as a portability tax.

### 2.2 — Cross-cutting: duplication and drift catalog

| What | Where | Cost |
|---|---|---|
| `query_snowflake` + `SNOWFLAKE_CONFIG` | `records.py`, `generate_summary.py` | Two copies of same function. Connection-per-call pattern. ~15-20 handshakes per recap run. |
| UTF-8 stdout reconfig | both output scripts | Trivial duplication, but illustrates missing shared script-startup module. |
| `load_dotenv()` | both output scripts (and records.py at import time) | Same. |
| Schedule lookup loading | both output scripts | Each calls `records.load_schedule_lookup()` at orchestration time. |
| "What's a score column" | `records.SCORE_STAT_NAMES`, `formatters._SCORE_STAT_KEYS`, `sheets_writer._SCORE_LABEL_ORDER` | 3 lists, slightly different content (formatters includes PLATFORM_*, sheets uses display labels). High drift risk. |
| Display ordering | `records._DISPLAY_ORDER`, `generate_records_report.STAT_ORDER` | 2 lists with overlapping but distinct content. |
| Excluded-stats sets | `records._NO_PLAYER_BREAKDOWN_STATS`, `records._TEAM_NON_SEED_STATS`, `generate_records_report._REPORT_EXCLUDED_STATS` | 3 related sets, defined separately, easy to forget to update one. |
| Tie-collapse algorithm | `formatters.format_contributors`, `records.collapse_ties` | Same shape, different inputs. Could share an underlying helper. |
| Inline-tier threshold | `INLINE_COLLAPSE_THRESHOLD` (records), `INLINE_TIER_LIMIT` (records report) | Same value (3), same intent, defined twice. |
| Stat label table | `formatters.STAT_DISPLAY`, `STAT_ABBREV`, `HITTER_STAT_DISPLAY`, `PITCHER_STAT_DISPLAY`, `TOP_SCORER_STAT_DISPLAY` | 5 overlapping dicts. Adding a stat means editing all 5+ the mart UNPIVOT + the records constants. |

The display-table proliferation (last row) is the strongest case for the **tracked-stats config refactor** mentioned in the Phase 6.3.3 backlog. It's also the single change that would most credibly say "I thought about how this scales to 100 leagues."

### 2.3 — The dbt/Python boundary

**Current state:** the boundary is real but undocumented. Calls go to dbt for grains/scoring/aggregation; to Python for everything else. But several borderline pieces sit on the wrong side:

#### Polarity (currently Python)

- `get_stat_polarity` queries `stg_scoring_settings` for `points_per_unit` sign — a thin SQL call.
- `_IMPLICIT_POLARITY` ([records.py:235-263](output/records.py:235)) is a hardcoded Python dict augmenting it with stats not in the seed (rates, derived stats, score columns).
- `get_effective_polarity` merges them.

**Could move to dbt:** add a `polarity` column to the `stat_classification` seed (or compute one in a new mart). Then add a `polarity` column to `mart_stat_leaderboard` via JOIN. Consumers just `WHERE polarity != 'neutral'`. Sign of `points_per_unit` for scored stats; explicit values from the seed for non-scored stats (rates, derived).

Pro: declarative, queryable from BI, removes 30+ lines of Python with implicit knowledge. Pro: mart row visibly carries its own meaning. Con: you'd still need consumer-side `direction` decisions (which directions to surface for which stats). That's a separate concern from polarity.

#### `should_track_record` (currently Python)

- Filter rule encoding "which (grain, stat, direction) combinations surface as records." Lives in [records.py:331-360](output/records.py:331).

**Could move to dbt:** add an `is_record_candidate` boolean column on `mart_stat_leaderboard`, computed as the polarity rule + the `is_always_tracked` seed flag. Consumers filter `WHERE is_record_candidate = true`.

Pro: BI dashboards can reuse the rule without re-implementing. Con: locks the rule into the mart — if the recap section wants different filter rules from the records report, the column doesn't help (today, both consumers happen to want the same thing, so this is hypothetical).

#### `is_always_tracked` (already dbt — good example)

[stat_classification.csv](dbt_league/seeds/stat_classification.csv) carries this flag; the mart could JOIN it but Python currently re-fetches via `get_always_tracked_stats`. If we add `polarity` and `is_record_candidate` columns, this fetch goes away — they'd both be on every mart row.

#### Tracked-stats config (currently three places)

- Hardcoded in mart UNPIVOT lists (twice).
- Hardcoded in `records.py` constants (`_PLAYER_CONTRIB_STATS`, etc.).
- Hardcoded in `formatters.py` display tables (`STAT_DISPLAY`, `STAT_ABBREV`).

**Could move to dbt:** the `stat_classification.csv` seed already exists. Extend it with `display_name`, `abbrev`, `polarity`, `is_always_tracked`, `is_record_candidate`, `category` (hitting/pitching), `is_derived` (PA, SB-CS, etc.), `derivation_expr`. Then drive the UNPIVOT from a Jinja loop over the seed in dbt; expose the seed via a query helper for Python label maps.

This is the **strongest "thinks at scale"** refactor in the codebase. It's also the most work — probably 4-6 hours including verification. Direct payoff for the README / interview-question story: "to add a tracked stat, you edit one CSV row. The mart picks it up via Jinja; Python label maps read from the seed."

#### What should NOT move to dbt

- Tie-collapse (display caps drive the algorithm)
- BBCode formatting
- Contributor row shape (top-3 player names vs top-3 stat names — output choice)
- Sheets row schema (17-col layout)
- Sort cohorts (the 4-cohort visual order)
- Stat-line rendering (.391/.462/1.174 -- 23 AB, 5 HR)
- League-flavor callout text and templates

### 2.4 — Operational risks

1. **Connection-per-query.** ~15-20 handshakes per script run. Trivial at 14 teams; broken at 100 leagues. Real signal for "thinks at enterprise scale."
2. **Sheets sink wipes user formatting.** `_replace_tab`'s `clear() + update()` pattern. Documented limitation. Separate task per HANDOFF.
3. **No tests on rendered output.** A records.py refactor today is risky — there's no automated regression check on the BBCode the user posts to ESPN every Sunday.
4. **Display-table drift.** Adding a tracked stat requires editing 5+ places. Easy to forget one and ship a recap with `{stat_name}` showing as raw uppercase.
5. **Three "what's a score column" definitions.** Identical bugs would silently diverge across surfaces.
6. **`SELECT *` against fcts** in `build_ctx` and `get_player_contributions`. Robust to column changes but a real cost at scale; not v1.0-blocking.
7. **`get_team_contributors` interpolates `stat_column` into SQL.** Safe today (input is enumerated), but a footgun if a new caller passes user input. Worth a comment hardening or a typed enum.

### 2.5 — Refactor candidates, ranked

Ordered by v1.0 ROI. Each entry includes which interview question it makes easier to answer crisply.

#### Tier 1 — strong v1.0 case (do these)

**A. Connection-management consolidation** (~2 hrs)
- Single Snowflake connection per script. Pass `conn` into query functions.
- Move `query_snowflake` + `SNOWFLAKE_CONFIG` to a new `output/db.py`.
- Remove the duplicate copy from `generate_summary.py`.
- Helps answer: "If you had to make this usable for 100 leagues, what would you change first?" → "I'd start with connection pooling; the current per-call pattern is acceptable at 14 teams but burns ~15-20 handshakes per script."

**B. Records.py split into 3-4 modules with stable public API** (~3-4 hrs)
- Split into: `records/data.py` (mart fetches + bulk contributors), `records/polarity.py` (polarity + filter rules + always-tracked), `records/presentation.py` (collapse + sort + ordinal + schedule labels), `records/orchestrator.py` (`get_records_with_contributors`, `get_records_set_this_week`).
- Keep `output/records.py` as a re-export shim during transition (or directly update imports — given there are only 3 consumers, direct is probably fine).
- Helps answer: "What is the canonical grain of each mart, and why?" + "Which business logic belongs in dbt, and which belongs in Python?" → reviewer can navigate via filenames; the boundary is enforced by module structure.

**C. Output-script boilerplate factoring** (~30 min)
- New `output/_setup.py` (or fold into `db.py`): UTF-8 stdout reconfig + `load_dotenv` + opening a session-level Snowflake connection + loading `schedule_lookup` once.
- Both output scripts call `_setup.session()` at start.
- Tiny but cleans up the top of every script.

**D. Tracked-stats seed expansion** (~4-6 hrs)
- Extend `stat_classification.csv` with `display_name`, `abbrev`, `polarity`, `is_record_candidate`, `category`, `is_derived`, `derivation_expr`.
- Drive `mart_stat_leaderboard` UNPIVOT from a Jinja loop over the seed.
- Replace `formatters.STAT_DISPLAY` / `STAT_ABBREV` lookups with a query helper that reads the seed.
- Helps answer: "What parts of this are overbuilt for one league, and what parts are intentionally scalable?" → "the stat catalog is a single CSV; every consumer reads from it. Adding a stat is one row." This is the single change that most credibly demonstrates enterprise mindset.

**E. Test scaffolding** (~2-3 hrs)
- New `tests/` directory at repo root, distinct from `archive/`.
- Pure-function tests on `format_contributors`, `collapse_ties`, `should_track_record`, `_player_stat_value`, `format_hitter_stats_line`, `format_pitcher_stats_line`, polarity logic, `_collapse_one_group`. Total: ~15-25 tests.
- Optional: 2-3 golden-output tests that pin a fixture mart row → expected BBCode block.
- `pytest` runnable without Snowflake. `requirements.txt` adds `pytest`.
- Helps answer: "What would you test if you only had one day to improve reliability?" → "I'd add golden-output tests on the BBCode renderers; refactoring records.py without them was the riskiest part of this project."

#### Tier 2 — defensible v1.0 case (consider)

**F. Polarity to dbt** (~1.5 hrs)
- Add `polarity` column to `stat_classification.csv`. Add a JOIN in `mart_stat_leaderboard` to surface it as a column. Replace `get_effective_polarity` + `_IMPLICIT_POLARITY` with a single mart-row read.
- Pro: declarative, removes 30 lines of Python. Con: marginal for v1.0 unless paired with (D).

**G. Single-source score-column constant** (~30 min)
- Pick one canonical place (probably `formatters.SCORE_STATS = {...}`) and import everywhere. Delete the other two.

**H. Single-source display-order list** (~30 min)
- Same pattern. Pick one canonical ordering, import.

#### Tier 3 — defer to v1.x

**I. `is_record_candidate` column on mart** — only worth doing if multiple consumers grow, and it locks the rule. Defer.

**J. DuckDB POC** — design notes only for v1.0. v1.x.

**K. Sheets formatting preservation** — separate task per HANDOFF. v1.x.

**L. Conditional 3rd Top Scorer line** — long-standing backlog. v1.x.

**M. SELECT * tightening** — premature optimization at current scale. Note in ROADMAP.

**N. Multi-sink output abstraction** — deferred until 2nd non-Sheets sink emerges. v2.0.

### 2.6 — Test story for v1.0

**Goal:** make refactoring records.py a non-scary operation. Not full coverage; just enough to catch regressions on the user-facing output.

**Layout:**

```
tests/
  __init__.py
  conftest.py                      pytest fixtures
  fixtures/
    sample_leaderboard_rows.json   for collapse / orchestrator tests
    sample_player_row.json         wide row for formatter tests
    sample_recap_state.json        for golden-output tests
    expected_recap.txt             pinned BBCode reference
  unit/
    test_polarity.py
    test_collapse.py
    test_filter_rules.py           should_track_record + variants
    test_formatters.py             format_*_stats_line, format_contributors
    test_records_helpers.py        ordinal, _player_stat_value, etc.
  integration/
    test_recap_golden.py           feeds fixture, asserts BBCode matches
                                   (deferred if it fights us; nice-to-have)
```

**What's worth testing:**

| Test | Why |
|---|---|
| `format_contributors` with various tie patterns | Most algorithmically complex pure function. Refactor magnet. |
| `collapse_ties` with single-tier, multi-tier, saturated cases | Same. |
| `should_track_record` for player/team × positive/negative/always-tracked | Captures the polarity filter rule. |
| `get_effective_polarity` merge logic | Captures the seed + implicit augment. |
| `format_hitter_stats_line` / `format_pitcher_stats_line` | The user-facing output we don't want to silently break. |
| `_player_stat_value` for derived stats (PA, SB_CS, W_L, SV_BLSV) | Easy to break when refactoring. |
| `ordinal(11)`, `ordinal(112)`, `ordinal(1)` | Trivial but cheap. |
| `format_week_label` for regular + playoff weeks | Covers seed-driven naming. |
| `fmt_ip(7)`, `fmt_ip(0)` | Baseball notation correctness. |

**What we won't test in v1.0:**

- Snowflake queries (require live connection or DuckDB POC — defer)
- The full `generate_summary.py` end-to-end (golden-output is optional v1.0 stretch)
- The Sheets writer (gspread integration; defer)

**`requirements.txt` change:** `pytest>=7.0` (test-only dep — could put in a `requirements-dev.txt` if we want strict separation, but for portfolio purposes a single requirements file with a comment is fine).

### 2.7 — Portability sketch (informational, not v1.0 work)

You asked what the practical lift would look like. Quick read:

**The high-friction Snowflake-specific pieces:**
- `UNPIVOT` in `mart_stat_leaderboard` (BigQuery has it; DuckDB has it; syntax varies)
- `LATERAL FLATTEN` for unpacking `kona_player_info` JSON in staging
- `VARIANT` columns
- `MERGE` semantics for incremental fcts (Snowflake uses MERGE; DuckDB uses INSERT OR REPLACE; BigQuery uses MERGE but with subtle differences)
- Some date functions

**You would NOT need net-new models per warehouse** if you:
1. Wrap warehouse-specific SQL behind dbt macros (e.g. `{{ unpivot_rate_stats() }}`).
2. Keep raw → staging contracts warehouse-agnostic (a JSON column is a JSON column; the unpacking is the macro layer).
3. Use `{{ adapter.dispatch() }}` for the genuinely incompatible cases.

**Realistic v1.x path:** isolate the high-friction pieces into a `macros/portability/` subdirectory (1 day). Then port one model family at a time when there's a reason to (DuckDB POC for local-dev demo would be a reasonable v1.x driver).

**For v1.0:** add a "Portability" section to README that says "the staging layer carries warehouse-specific SQL; marts and intermediates are mostly portable. Targeting DuckDB or BigQuery would require macro wrapping for ~5 specific patterns." That's the honest framing. No code change required.

---

## Part 3 — Open questions for you

These are the discrete decisions that unlock the refactor list. Each one is a small decision; pick a posture and we move.

### Q1: Records.py split shape
Tier 1 (B) above proposes 4 sub-modules: `data`, `polarity`, `presentation`, `orchestrator`. Are you good with that breakdown, or do you want a more conservative 2-module split (e.g. `data` vs `everything else`), or a more aggressive 5-6 module split (separate `collapse`, `schedule`, `counts`)?

My recommendation: **4-module split**. The seams are clear and "5+ modules" starts to feel like splitting for splitting's sake.

### Q2: Tracked-stats seed expansion (Tier 1, item D)
This is the biggest "thinks at scale" refactor and the most work (~4-6 hrs). It's also the single change that most credibly demonstrates enterprise mindset for portfolio purposes. Three postures:

- **Full**: extend the seed, drive UNPIVOT from Jinja, drive Python label maps from a query helper. Eliminates 80% of cross-cutting display/stat duplication.
- **Half**: extend the seed with the new columns, but only consume them from Python (label maps read from seed; UNPIVOT stays hardcoded). Smaller blast radius; less convincing as a refactor story.
- **Defer**: leave hardcoded; note in ROADMAP. Fastest path to ship.

My recommendation: **full** — this is the highest-leverage v1.0 refactor for your portfolio narrative, and the seed-driven mart UNPIVOT is the thing a dbt reviewer would actually notice.

### Q3: Polarity to dbt (Tier 2, item F)
Promote `polarity` to a `stat_classification.csv` column + mart column? Or leave the `_IMPLICIT_POLARITY` Python dict?

My recommendation: **promote**, but only if Q2 is "full" — they bundle naturally (the seed extension is the same edit). If Q2 is "half" or "defer," leave polarity in Python; it's not worth a dedicated refactor on its own.

### Q4: Test story scope (Tier 1, item E)
Three postures:

- **Pure-function only**: ~15-25 unit tests on the algorithmic + formatter pieces. No live data dependencies. Fastest path.
- **Pure + golden-output**: add 2-3 fixture-driven tests that pin BBCode output. Catches more regressions but more brittle (any visible change requires updating the fixture).
- **Pure + golden + integration**: add a DuckDB-backed integration test that runs the full pipeline locally. v1.x, not v1.0.

My recommendation: **pure + golden-output**. Two golden-output tests (one for each script) gives strong refactor confidence. The brittleness is acceptable because the user-facing output is locked (per HANDOFF §11).

### Q5: Project name
You'll need to decide this when we get to the README. Worth thinking now — the name shapes the framing. Options from the Phase 7 Handoff:

- Descriptive: `espn-fantasy-baseball-pipeline`, `fantasy-baseball-elt`
- Punchy: `Diamond Cuts`, `Box Score`, `Bullpen`
- League-flavored: a nod at "Baseball Buns in the Sun"
- Tech-forward: `dbt-fantasy-frontpage`

My read: descriptive names show up better in recruiter-facing search; punchy names show up better in conversation. Given the dual-goal (portfolio + recreational), I'd lean **`espn-fantasy-baseball-pipeline`** for the repo name and let punchy/league-flavored versions live in the README header. But genuinely your call.

### Q6: Order of execution
Assuming you say "go" on the Tier 1 list, my proposed order:

1. **Test scaffolding** (E) — first, so subsequent refactors have a safety net.
2. **Connection consolidation + boilerplate factoring** (A + C) — small, well-bounded, builds the `db.py` / `_setup.py` modules everything else uses.
3. **Records.py split** (B) — the big surgery. Tests caught any regressions.
4. **Tracked-stats seed expansion** (D, if approved) — the most work; goes last so the smaller pieces are already shipped if we run out of time.

Then onto public docs (README / SETUP / CHANGELOG / ROADMAP / LICENSE / tag).

Reasonable? Anything you want re-ordered?

---

## Appendix — what we're NOT changing

For clarity:

- Section ordering, header conventions, player-card shape, "records show owner names; recap doesn't," `Week N` / playoff-round-name display, 17-col Sheets schema, `SHEETS_OUTPUT_ID` opt-in, `LeagueNote.txt` verbatim append. All locked per HANDOFF §11.
- The mart's `most`/`fewest` direction values. Phase 6.3.3 made this call deliberately.
- The `platform_*` vs `calculated_*` distinction. Load-bearing per HANDOFF §7.
- The `is_abnormal` filtering at the leaderboard. Don't bypass.
- The `is_always_tracked` seed flag mechanism. Already a good example of the pattern we want.
