{% docs __overview__ %}

# Fantasy League Almanac

ELT pipeline for **two fantasy baseball leagues on two platforms with two
different formats**: an ESPN head-to-head points league, and a CBS
season-long points league whose history runs back to 2001. Extracts land
platform-native JSON in Snowflake RAW; dbt transforms it through staging /
intermediate / mart layers into one shared model; Python consumers render
weekly BBCode recaps, an all-time records report, and browsable Google
Sheets almanacs.

This dbt project is the transform layer. See the **Exposures** section in
the sidebar for the formally-declared consumers -- note that the declared
set is hand-maintained and currently narrower than the real output layer,
which `dbt_league/README.md` documents rather than papers over.

## Architecture at a glance

    ESPN Fantasy API          CBS Fantasy API         MLB Stats API
    (espn-api wrapper)        (token auth)            (public, universal)
        |                         |                        |
        +-------------------------+------------------------+
                                  v
        Python extractors -> Snowflake RAW (append-only JSON, 22 sources:
                             box scores, scoring/roster settings, team
                             owners, draft picks, transactions; CBS
                             rosters/standings/gamelogs/crosswalk; MLB
                             gamelogs, season stats, fielding, positions)
            -> dbt staging       (24) 1:1 reshape, no business logic;
                                      platform vocabulary canonicalized here
            -> dbt intermediate  (15) identity resolution, roster-stint and
                                      lineup-interval walk-backs, daily rollup
            -> dbt marts/core    (19) the contract layer: 8 dims + 11 facts
                                      (daily/weekly/season grains,
                                      active/inactive lenses, position points)
            -> dbt marts/reporting (16) consumer marts: seed-driven
                                      leaderboard, league benchmarks, matchup
                                      view, roster snapshot, draft board
            -> Python output layer (BBCode + Google Sheets almanacs)

The dbt project has **74 models** (32 views, 39 tables, 3 incremental),
**18 seeds**, **543 data tests** (532 generic + 11 singular), **22
sources**, and **4 declared exposures**. Browse the **Models** section in
the sidebar for full lineage and column-level docs; cross-model invariants
live as singular tests in `tests/`.

Counts here are regenerated from the parsed manifest at each release cut.
If you are reading this mid-cycle, `dbt parse` and the manifest are the
truth.

**A naming caveat worth reading before you infer anything from prefixes:**
platform vocabulary is translated at staging, but platform *work* does not
all disappear there. CBS serves no per-day scoring, so its roster stints,
lineup intervals and eligibility windows are reconstructed in dedicated
`int_cbs__*` models that land in the shared fact family. Thirteen models
below staging are platform-specific by design.

## Key concepts

**Active vs inactive facts.** Symmetric
`fct_weekly_{player,team}_active_performance` and
`fct_weekly_{player,team}_inactive_performance` split the player-week
universe by whether the player was started ("active") or benched / IL /
free agent ("inactive"). Active = fantasy reality (what the manager
played); inactive = MLB reality (what the player did regardless of
fantasy rostering). Together they cover the full player-week universe.

**`platform_*` vs `calculated_*` columns.** Two scoring lenses on every
fact:
- `platform_*` is the platform's reported score, slot-aware and inclusive
  of commissioner adjustments. The arbiter of W/L outcomes.
- `calculated_*` is the project's rules-normalized derivation under
  current-season scoring weights, applied universally including to
  historical rows. Use for cross-season comparison.

The divergence between the two (when slot misuse or scoring-rule
changes drift them apart) is captured in
`platform_calculated_delta` on the team active fact.

**Provenance and error bars.** Only 2026 is captured live. 2021-2025 is
reconstructed day by day from the transaction log; 2004-2020 is estimated
from start-share rates; 2001-2003 has no year-end roster anchors at all
and is the weakest stretch in the book. Every era carries its own measured
error rate, and the almanac prints it rather than hiding it -- see
`docs/user-guide/03-stat-sources-and-fidelity.md`.

**Seed-driven catalog.** `stat_classification.csv` (99 rows, 16 columns)
is the single source of truth for every stat the pipeline knows about:
identity, display, polarity, record-surfacing rules. Drives the mart
leaderboard's Jinja UNPIVOT loop AND the Python display logic. Adding
a tracked stat is a CSV edit + reseed -- no Python or mart-SQL change
required.

**`mart_stat_leaderboard`** is the consumer-facing records mart. Each
`(entity_grain, performance_status, stat_name, record_scope,
record_direction)` partition emits 10 ranked rows. The top-10 emission
is a visibility buffer: consumers display top-5 but the extra 5 ranks
let consumer-side tie-collapse logic detect tier saturation.

**Roster settings and roster history.** `dim_roster_slot_counts` reshapes
roster settings into one row per configured lineup slot, including
starter counts and position maximums. `mart_daily_roster_snapshot`
preserves the full roster shell from box scores so almanac tabs can count
rostered days and include zero-stat bench/IL players that do not survive
the stat-breakdown performance path.

## Where to look next

- **Models** sidebar -- per-model descriptions, columns, and lineage.
- **Sources** sidebar -- the 22 upstream RAW tables across all three
  feeds (ESPN, CBS, MLB Stats API).
- **Seeds** sidebar -- the 18 seed CSVs that feed the pipeline.
  `stat_classification` is the keystone and deserves a closer read.
- **Exposures** sidebar -- the declared downstream consumers
  (`weekly_recap`, `records_report`, `records_sheet`, `league_almanac`)
  with owner and description.

## Project documentation

The repository's root-level docs cover what the dbt catalog doesn't:

- **README.md** -- project overview, sample output, setup instructions,
  architecture diagram, notable engineering decisions.
- **SETUP.md** -- bring-your-own-credentials path for new users
  (ESPN cookies, Snowflake free tier, GCP OAuth for Sheets).
- **dbt_league/README.md** -- this project's own layer conventions, the
  DAG walkthrough, and the known gaps in the exposure declarations.
- **docs/platform-adapter-contract.md** -- the shape a new platform has to
  land data in.
- **CHANGELOG.md** -- version history, currently through v1.6.0.
- **ROADMAP.md** -- what's next (v1.x polish, v2.0 features, deliberate
  exclusions).
- **Phase X.Y Documentation.md** -- the historical phase record. Useful
  for archaeology on specific architectural decisions.

{% enddocs %}
