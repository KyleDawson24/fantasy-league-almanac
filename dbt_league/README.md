# dbt_league — the transform layer

dbt + Snowflake project that models raw ESPN Fantasy Baseball extracts into
a small star schema and four consumer surfaces (weekly BBCode recap,
all-time records report, legacy records sheet, Google Sheets league
almanac). The product story, sample output, and engineering-decision log
live in the [repo root README](../README.md); this file documents the dbt
project itself: the layers, the conventions, and how to run and test it.

Browse the compiled catalog (lineage + column-level docs) at the
[hosted dbt docs site](https://kyledawson24.github.io/fantasy-league-front-page/).

## The DAG, top to bottom

```
RAW.* sources (5)          seeds (4)
   │                          │
   ▼                          │
staging/        stg_*    1:1 reshapes of RAW; no business logic
   │                          │
   ▼                          │
intermediate/   int_*    business logic that isn't yet a contract:
   │                     the slot-validity filter + daily wide rollup
   ▼                          │
marts/core/     dim_*    the star-schema contract layer: 5 dims
   │            fct_*    + 8 facts (daily / weekly / season grains,
   │                     active / inactive lenses, position points)
   ▼
marts/reporting/ mart_*  consumer-facing report shapes: leaderboard,
   │                     benchmarks, matchup view, roster snapshot,
   ▼                     draft board
exposures (4)            the Python output scripts, declared in
                         models/exposures.yml
```

Layer conventions:

| Layer | Prefix | Default materialization | What belongs here |
|---|---|---|---|
| `staging/` | `stg_` | view | One model per raw table. Pure reshape: flatten VARIANT, type, rename. The only layer that reads `source()`. |
| `intermediate/` | `int_` | view | Business logic that isn't yet a consumer contract: the slot-validity filter and the wide daily point rollup. |
| `marts/core/` | `dim_` / `fct_` | table (facts override to incremental/view per model) | The contract layer. Grain-documented dimensions and facts that reporting marts and the Python output layer rely on. |
| `marts/reporting/` | `mart_` | table (most override to view) | Report-shaped derivations over core: rankings, league aggregates, matchup context, snapshot joins. |

One deliberate exception to "consumers read core," declared on the
`league_almanac` exposure rather than hidden: `stg_scoring_settings`
supplies points-per-unit for glossary callouts (no core surface carries
scoring weights yet). Two former exceptions were resolved by promotion —
`fct_player_position_pts` (born `int_player_position_pts`) and
`dim_team_owner` (born `int_team_owner_display`) moved into core once
consumer reads made them de-facto contracts, the same reasoning that
promoted `fct_player_daily_performance` in v1.1.0.

## The two scoring lenses

Every fact carries two parallel scoring columns:

- **`platform_*`** — ESPN's own award: slot-aware at team grain, inclusive
  of commissioner adjustments. The arbiter of W/L outcomes.
- **`calculated_*`** — the project's rules-normalized derivation: the
  current season's scoring weights applied to every stat line, including
  historical seasons. The only lens that makes cross-season records
  comparable.

Divergence between the lenses is a feature, not drift — it isolates
commissioner adjustments and historical rule changes, and is captured
explicitly in `platform_calculated_delta` on the team active fact.

## Active / inactive symmetry

The player-week universe splits into `fct_weekly_{player,team}_active_performance`
(what the manager started — fantasy reality) and
`fct_weekly_{player,team}_inactive_performance` (bench / IL / free-agent
production — MLB reality). Together they cover the full universe; the
recap's wasted-points callouts and the leaderboard's active/inactive
partitions fall out of the split rather than being computed ad hoc.

## Seed-driven stat catalog

`seeds/stat_classification.csv` (97 rows) is the single source of truth
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
are incremental with composite `unique_key`s and `on_schema_change: fail` —
the weekly extract-then-build cadence merges one matchup period at a time.
Models where determinism matters more than build cost are plain tables
(`fct_player_weekly_slot_performance`, `fct_player_position_pts`,
`fct_player_season_performance`, `mart_team_matchup` — frozen so
per-query float re-summation can't flip rounding-boundary values or
reshuffle optimal-team tie-breaks between reads). `mart_stat_leaderboard`
stays a view: rankings are retroactively mutable, and its consumers
round every displayed value at source.

## Testing

158 dbt data tests plus source-freshness contracts:

- **Generic tests** — every model carries a `dbt_utils.unique_combination_of_columns`
  grain test; keys and partitions carry `not_null` / `accepted_values`;
  staging FKs into the seed catalog carry `relationships`.
- **Singular tests** (`tests/`) — cross-model invariants that used to be
  run-when-you-remember analyses, now enforced on every build: the
  season-fact-vs-weekly-rollup fidelity check (grain completeness both
  directions + points within the documented rounding envelope), the
  performance_status full-partition check, and the eligibility-explosion
  guards (BE/IL leak = error; Trout/Soto/FA data canaries = warn).
- **Source freshness** — seasonal thresholds on the four settings-style
  raw tables (see `models/staging/sources.yml` for why `box_scores` has
  none yet).
- **Byte-diff goldens** (outside dbt, `../tests/`) — the almanac TSV
  fixture, recap BBCode, and records-report BBCode are regression-pinned;
  `pytest -m warehouse` from the repo root regenerates and diffs them.

## Running it

From the repo root (profile `dbt_league`, target schema `ANALYTICS`):

```bash
cd dbt_league
dbt deps                # first time only
dbt seed                # load the four seed CSVs
dbt build               # models + tests, incremental weekly
dbt build --full-refresh   # after backfills or seed schema changes
dbt source freshness    # settings-snapshot staleness check
dbt docs generate       # compiled catalog; --static for the hosted site
```

The weekly cadence is: extract (`python extract/extract.py`) → `dbt build`
→ the three output scripts. See [HANDOFF.md](../HANDOFF.md) for the full
operational runbook.

## Exposures

`models/exposures.yml` formally declares the four downstream consumers
(weekly recap, records report, legacy records sheet, league almanac) with
their complete upstream dependency lists, so the docs-site lineage runs
source → staging → core → reporting → deliverable with no dead ends. If a
script gains or drops a warehouse read, the exposure entry changes in the
same commit.
