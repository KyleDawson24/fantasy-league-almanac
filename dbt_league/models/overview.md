{% docs __overview__ %}

# Fantasy Beat Reporter

ELT pipeline for an ESPN Fantasy Baseball head-to-head points league.
Extracts box-score data from ESPN's API, transforms it through staging /
intermediate / mart layers in Snowflake, and generates weekly BBCode
recaps plus an all-time records report for posting on the league's front
page.

This dbt project is the transform layer. It feeds two Python output
scripts (`generate_summary.py` for the weekly recap,
`generate_records_report.py` for the all-time records dump) and an
optional Google Sheets sink. See the **Exposures** section in the
sidebar for the formally-declared consumers.

## Architecture at a glance

    ESPN Fantasy API (espn-api wrapper)
        -> Python extractor
        -> Snowflake RAW.BOX_SCORES (append-only JSON)
        -> dbt staging       (1:1 reshape, no business logic)
        -> dbt intermediate  (slot-validity filter; daily + weekly wide rollups)
        -> dbt marts         (active/inactive symmetric facts;
                              seed-driven leaderboard)
        -> Python output scripts (BBCode + Google Sheets)

The dbt project has 10 models total: 3 staging, 2 intermediate, 5 marts.
Browse the **Models** section in the sidebar for full lineage and
column-level docs.

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
- `platform_*` is ESPN's reported score, slot-aware and inclusive of
  commissioner adjustments. The arbiter of W/L outcomes.
- `calculated_*` is the project's rules-normalized derivation under
  current-season scoring weights, applied universally including to
  historical rows. Use for cross-season comparison.

The divergence between the two (when slot misuse or scoring-rule
changes drift them apart) is captured in
`platform_calculated_delta` on the team active fact.

**Seed-driven catalog.** `stat_classification.csv` (97 rows, 13 columns)
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

## Where to look next

- **Models** sidebar -- per-model descriptions, columns, and lineage.
- **Sources** sidebar -- the upstream RAW tables (ESPN box scores,
  scoring settings).
- **Seeds** sidebar -- the four seed CSVs that feed the pipeline.
  `stat_classification` is the Phase 7 keystone and deserves a closer
  read.
- **Exposures** sidebar -- the three downstream consumers
  (`weekly_recap`, `records_report`, `records_sheet`) with owner and
  description.

## Project documentation

The repository's root-level docs cover what the dbt catalog doesn't:

- **README.md** -- project overview, sample output, setup instructions,
  architecture diagram, notable engineering decisions.
- **SETUP.md** -- bring-your-own-credentials path for new users
  (ESPN cookies, Snowflake free tier, GCP OAuth for Sheets).
- **CHANGELOG.md** -- version history mapped retroactively from Phase 1
  (v0.1.0) through Phase 7 (v1.0.0).
- **ROADMAP.md** -- what's next (v1.x polish, v2.0 features, deliberate
  exclusions).
- **Phase X.Y Documentation.md** -- the historical phase record. Useful
  for archaeology on specific architectural decisions.

{% enddocs %}
