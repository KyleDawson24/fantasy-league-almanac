# dbt_league -- the transform layer

dbt + Snowflake project that models raw fantasy-baseball extracts -- ESPN
first, and since the multi-league re-grain a second CBS league archived
alongside it -- into a star schema and its consumer surfaces (weekly
BBCode recap, all-time records report, legacy records sheet, Google
Sheets league almanacs for both books). The product story, sample
output, and engineering-decision log live in the
[repo root README](../README.md); this file documents the dbt project
itself: the layers, the conventions, and how to run and test it.

Browse the compiled catalog (lineage + column-level docs) at the
[hosted dbt docs site](https://kyledawson24.github.io/fantasy-league-almanac/).

## The DAG, top to bottom

```
RAW.* sources (29)         seeds (20)
   │                          │
   ▼                          │
staging/    (32) stg_*   1:1 reshapes of RAW; no business logic
   │                          │
   ▼                          │
intermediate/ (23) int_* business logic that isn't yet a contract:
   │                     the slot-validity filter + daily wide rollup
   ▼                          │
marts/core/ (22) dim_*   the star-schema contract layer: 11 dims
   │            fct_*    + 11 facts (daily / weekly / season grains,
   │                     active / inactive lenses, position points)
   ▼
marts/reporting/ (18) mart_*  consumer-facing report shapes:
   │                     leaderboard, benchmarks, matchup view,
   ▼                     roster snapshot, draft board, records
exposures (4)            the Python output scripts, declared in
                         models/exposures.yml
```

95 models in all. Counts here are regenerated at each release cut from
the parsed manifest (see [RELEASING.md](../RELEASING.md)); if you are
reading them mid-cycle, `dbt parse` and the manifest are the truth.

Layer conventions:

| Layer | Prefix | Default materialization | What belongs here |
|---|---|---|---|
| `staging/` | `stg_` | **table** (13 of 32 pin `view` themselves) | One model per raw-table *grain* (a multi-grain source like box_scores feeds several single-grain reshapes). Pure reshape: flatten VARIANT, type, rename. The only layer that reads `source()`. |
| `intermediate/` | `int_` | **table** (all 23 pin their own; 18 are views) | Business logic that isn't yet a consumer contract: the slot-validity filter and the wide daily point rollup. |
| `marts/core/` | `dim_` / `fct_` | table (3 weekly facts override to incremental; 11 thin dims/facts to view) | The contract layer. Grain-documented dimensions and facts that reporting marts and the Python output layer rely on. |
| `marts/reporting/` | `mart_` | table (6 of 18 override to view) | Report-shaped derivations over core: rankings, league aggregates, matchup context, snapshot joins. |

The staging/intermediate `table` defaults are deliberate and recent
(MLB-134): a view over fat JSON re-runs the whole reshape for every
consumer *and* every schema test that reads it. Snowflake absorbed that
cost quietly; the MLB-9 spike put a number on it on a laptop-class
engine -- 25-80s per test as views, 0.04-0.05s as tables. The reasoning
lives in `dbt_project.yml` next to the config.

> **Naming caveat.** Directory and prefix names here describe the
> *intended* topological order, and there are known edges that violate
> it -- staging models that read dims, intermediates that read core.
> They are catalogued with file:line evidence in
> [docs/dag-boundaries-DRAFT.md](../docs/dag-boundaries-DRAFT.md)
> (MLB-158 Phase A). Until that redraw lands, do not infer a model's
> dependencies from its prefix; read its `ref()`s.

One deliberate exception to "consumers read core," declared on the
`league_almanac` exposure rather than hidden: `stg_scoring_settings`
supplies points-per-unit for glossary callouts (no core surface carries
scoring weights yet). Two former exceptions were resolved by promotion --
`fct_player_position_pts` (born `int_player_position_pts`) and
`dim_team_owner` (born `int_team_owner_display`) moved into core once
consumer reads made them de-facto contracts, the same reasoning that
promoted `fct_player_daily_performance` in v1.1.0.

## Reading the DAG: the edges that look odd

The layering rule of thumb: an edge is healthy when the downstream model
consumes the upstream's **grain wholesale**; it's a smell when it skips a
layer to fetch a single field that a nearer layer already carries. Three
edges in this graph deserve the explanation up front:

**One raw table, three staging models.** The box-score JSON carries
more than one grain at once: per-player rows, and per-matchup
`home_score` / `away_score` -- ESPN's authoritative team totals,
slot-aware and inclusive of commissioner adjustments. Each grain gets
its own single-grain staging reshape: `stg_box_scores` (player rows),
`stg_matchup_scores` (final team score per matchup), and
`stg_matchup_pairs` (the who-played-whom spine). The team fact joins
the matchup-grain reshapes rather than re-deriving team totals as
SUM(players) -- the divergence between the two is the point, captured in
`platform_calculated_delta`. This is what keeps "only staging reads
`source()`" absolute without forcing one model to serve two grains.

**`stat_classification` fanning out three ways.** A config seed is a hub
by design -- one file, three layer-appropriate reads:
`stg_scoring_settings` joins it as the identity bridge (ESPN's raw
settings key stats by numeric ID; the seed maps ID → `stat_name`),
`int_player_daily` joins it for business logic (`is_counting`,
`stat_category` for the slot-validity filter), and `dim_stat` wraps it
as the consumer contract (everything downstream of core -- including
`mart_stat_leaderboard`'s compile-time UNPIVOT loop -- reads the seed
only through the dim). Collapsing any of these would invert a layer:
staging can't read a mart, and the intermediate shouldn't either.

**`stg_box_scores` → `mart_daily_roster_snapshot`** (staging feeding a
mart directly). The roster snapshot is a roster-*state* product, not a
performance product. The performance path
(`stg_player_stat_breakdowns` → `int_player_daily` → facts) inner-joins
stat breakdowns, which drops rostered players who did nothing that day
-- exactly the rows a roster archive must keep. So the mart branches
upstream of that filter and consumes the staging grain wholesale (every
rostered player-day), joining only slot metadata and owner display. An
intermediate pass-through here would add a node with no logic in it.

## The two scoring lenses

Every fact carries two parallel scoring columns:

- **`platform_*`** -- ESPN's own award: slot-aware at team grain, inclusive
  of commissioner adjustments. The arbiter of W/L outcomes.
- **`calculated_*`** -- the project's rules-normalized derivation: the
  current season's scoring weights applied to every stat line, including
  historical seasons. The only lens that makes cross-season records
  comparable.

Divergence between the lenses is a feature, not drift -- it isolates
commissioner adjustments and historical rule changes, and is captured
explicitly in `platform_calculated_delta` on the team active fact.

## Active / inactive symmetry

The player-week universe splits into `fct_weekly_{player,team}_active_performance`
(what the manager started -- fantasy reality) and
`fct_weekly_{player,team}_inactive_performance` (bench / IL / free-agent
production -- MLB reality). Together they cover the full universe; the
recap's wasted-points callouts and the leaderboard's active/inactive
partitions fall out of the split rather than being computed ad hoc.

## Two seed roots

The 18 seeds live in two directories, and `seed-paths` reads both:

```
seeds/          5   reference vocabulary -- stat maps, MLB team abbrevs,
                    record rules. Same for every league on a platform, so
                    it ships as real content.
league_config/ 13   user config -- calendar, franchise/owner registries,
                    naming overrides. Tracked content is BLANK templates;
                    per-file documentation is in league_config/README.md.
```

`DBT_LEAGUE_CONFIG` selects the second one (default `league_config`), which
is how `tools/demo.sh` builds off `demo/league_config/` -- a tracked
fixture holding a complete fake league -- without touching a real one.

dbt resolves seeds by filename, not path, so `ref()` is identical either
way and no model knows the split exists. The corollary is that both roots
may never carry the same filename, which is exactly why the demo fixture is
a *replacement* directory rather than an overlay.

## Seed-driven stat catalog

`seeds/stat_classification.csv` (99 rows) is the single source of truth
for every stat: identity, category, display labels, record-surfacing
rules, polarity, rate-stat qualifier thresholds. It drives, from one file:

- the wide per-stat columns and point contributions in `int_player_daily`,
- `mart_stat_leaderboard`'s compile-time Jinja UNPIVOT loop (via
  `dim_stat`, the thin contract view over the seed),
- the Python display/polarity logic (`output/stat_catalog.py` reads
  `dim_stat` once per process).

Adding a tracked stat is a CSV row + `dbt seed --full-refresh -s
stat_classification`; no SQL or Python changes.

## Incremental strategy

The three weekly facts (`fct_player_weekly_active_performance`,
`fct_player_weekly_inactive_performance`, `fct_team_weekly_active_performance`)
are incremental with composite `unique_key`s and `on_schema_change: fail` --
the weekly extract-then-build cadence merges one matchup period at a time.
Models where determinism matters more than build cost are plain tables
(`fct_player_weekly_slot_performance`, `fct_player_position_pts`,
`fct_player_season_performance`, `mart_team_matchup` -- frozen so
per-query float re-summation can't flip rounding-boundary values or
reshuffle optimal-team tie-breaks between reads). `mart_stat_leaderboard`
stays a view: rankings are retroactively mutable, and its consumers
round every displayed value at source.

## Testing

717 dbt data tests (693 generic + 24 singular) plus source-freshness
contracts:

- **Generic tests** (693) -- every model carries a
  `dbt_utils.unique_combination_of_columns` grain test; keys and
  partitions carry `not_null` / `accepted_values`; staging FKs into the
  seed catalog carry `relationships`.
- **Singular tests** (24, in `dbt_league/tests/`) -- cross-model
  invariants that used to be run-when-you-remember analyses, now
  enforced on every build: the season-fact-vs-weekly-rollup fidelity
  check (grain completeness both directions + points within the
  documented rounding envelope), the performance_status full-partition
  check, the eligibility-explosion guards (BE/IL leak = error;
  Trout/Soto/FA data canaries = warn), the CBS attribution/fanout and
  scoring-feed checks, and the franchise/team display-resolution
  anchors.
- **Source freshness** -- seasonal thresholds on the four settings-style
  raw tables (see `models/staging/sources.yml` for why `box_scores` has
  none yet).
- **Byte-diff goldens** (outside dbt, `../tests/`) -- the ESPN and CBS
  almanac TSV fixtures, recap BBCode, and records-report BBCode are
  regression-pinned; `pytest -m warehouse` from the repo root diffs them
  against your warehouse. **These corpora are private** (they render
  real owner names, so they live on the maintainer's machine and the
  private dev remote only). In a fresh public clone the tests that need
  them *skip* rather than fail -- see "Which tests need what" in
  [SETUP.md](../SETUP.md).

## Running it

Profile `dbt_league`, target schema `ANALYTICS`. Commands are grouped by
what they actually need and what they actually touch -- the same three
tiers [SETUP.md](../SETUP.md) uses.

**Tier 1 -- offline.** Works in any clone, no credentials, touches
nothing:

```bash
cd dbt_league
dbt deps                # install dbt_utils; first time only
dbt parse               # validates refs, schema YAML, Jinja, doc() resolution
dbt compile             # renders SQL to target/ without executing it
```

`dbt parse` is what CI runs, and it catches most classes of "I broke the
dbt project" without opening a connection.

**Tier 2 -- live, read-only.** Needs Snowflake credentials; reads the
warehouse but writes nothing to it:

```bash
dbt debug               # connection check -- expect "All checks passed!"
dbt source freshness    # settings-snapshot staleness check
dbt docs generate       # compiled catalog; --static for the hosted site
dbt ls                  # resolve node selection without running it
```

**Tier 3 -- mutation.** Writes to the warehouse. Deliberate ceremony, not
a dev-loop reflex:

```bash
dbt seed                # load the 20 seed CSVs
dbt build               # models + tests, incremental weekly
dbt build --full-refresh   # after backfills or seed schema changes
dbt seed --full-refresh    # after a seed's columns change
```

The weekly cadence is: extract (`python extract/extract.py`) → `dbt build`
→ the three output scripts. See [HANDOFF.md](../docs/archive/HANDOFF.md) for
the full operational runbook.

## Exposures

`models/exposures.yml` declares four downstream consumers (weekly recap,
records report, legacy records sheet, league almanac) with their upstream
dependency lists, so the docs-site lineage runs source → staging → core →
reporting → deliverable. If a script gains or drops a warehouse read, the
exposure entry changes in the same commit.

> **Known gap, not yet fixed.** The declared set is incomplete:
> `output/generate_season_report.py` is a production consumer with no
> exposure entry, and the `league_almanac` exposure predates the CBS
> book, so it does not enumerate the CBS-side reads. The declarations are
> hand-maintained and nothing currently tests them against the code --
> that contract test is deliberately deferred until the graph stops
> moving (MLB-158 Phase C). Treat the lineage site as indicative, not
> authoritative, until then.
