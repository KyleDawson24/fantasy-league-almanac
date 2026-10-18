# DAG boundaries and model homes

Status: Phase B1 implemented 2026-10-16 and B2 implemented 2026-10-18 (section 7): the eleven moves, layer metadata, the seed join and the two identity input projections are in place. Measured from the manifest: zero backward edges, eight registered reporting-to-staging skips. B3, B4 and Phase C (enforcement) are pending. The exposure declarations describe current production reads.

## 1. Layer contract

The dependency order is staging -> identity -> reconstruction -> convergence -> core -> reporting. An edge below is written as reader -> dependency. A model may read its own layer or any earlier layer. Every read of a later layer is a violation. Reporting may not read staging directly without an exact-edge exception documented here. Same-layer reads are normal; dbt still rejects cycles. Sources and seeds are upstream inputs available as needed.

| Layer | May read | Responsibility |
|---|---|---|
| Staging | Sources and seeds; same-layer staging fan-outs | Flatten, type and normalize captured records while preserving their grain. |
| Identity | Staging, seeds, identity | Resolve player, owner and franchise identity, with ambiguity and override provenance. |
| Reconstruction | Staging, identity, reconstruction; upstream sources/seeds where needed | Rebuild intervals, calendars and attributed history using resolved identity. |
| Convergence | Reconstruction, identity, staging, convergence; upstream seeds | Combine platform-specific observations into common contracts. |
| Core | All earlier layers plus core; upstream seeds | Publish dimensions and facts with explicit grains and stable interfaces. |
| Reporting | Identity, reconstruction, convergence, core, reporting; upstream seeds | Consumer-shaped aggregates and independent reconciliation. Direct staging reads require the register in section 4. |

The staging shorthand "sources and seeds" does not prohibit same-layer fan-outs. In particular, splitting a multi-grain source into staging models does not require duplicating its extraction SQL. The eleven existing staging-to-staging edges are permitted. Seed-backed vocabularies are not a seventh model layer.

## 2. Two design decisions

**Resolve identity before reconstructing history.** The question of which player or franchise a row describes is upstream of the question of what happened on a given date. The identity layer owns candidate selection, contextual overrides and ambiguity. Reconstruction consumes those decisions and preserves evidence quality. Native source identifiers such as MLBAM IDs need no new name-matching step. Raw names may remain as audit labels, but must not compete with a resolved key as an independent identity decision.

**Keep every existing model name.** Names are interfaces for SQL readers, Python consumers, tests, exposures and documentation. A dimension remains a dimension when it moves into identity. Moving a file does not require changing its relation name or its ref() calls. All 106 existing names stay; changing a prefix would add consumer churn without improving the graph.

**Ruling, 2026-10-18.** The August rename riders are dead, not parked: names are interfaces. Four names now contradict their layer by prefix (`int_matchup_season_standard`, `mart_period_standings` and `mart_daily_roster_snapshot` in core; `stg_cbs__team_owners` in identity). The layer is `meta.dag_layer`, not the prefix, and the [dbt README](../dbt_league/README.md#reading-the-dag-the-edges-that-look-odd) says so in one sentence. No model, column, relation or file is renamed; the two B2 models are new names, not renames.

The physical layout keeps the four existing directory buckets. Identity, reconstruction and convergence are explicit semantic layers within intermediate. Phase B will set `meta.dag_layer` through dbt project model configuration, with per-model overrides for identity and convergence. Existing intermediate files stay in place; eight other models move into intermediate and three move into core. Layer metadata, this table and parsed dependencies agree on the six-layer order. A later six-folder reorganization would be separate work, not hidden inside these eleven moves.

## 3. All current models and their target homes

Measured from a successful parse at Phase A: 106 models and 287 unique dependencies (225 model, 31 seed, 31 source). After B1 the seed join turns one model edge into a seed edge: 224 model, 32 seed, 31 source. B2 adds the two identity input projections, both in `models/intermediate`: `int_cbs__resolved_identity_events.sql` and `int_cbs__resolved_identity_anchors.sql`. That makes 108 models and 289 unique dependencies (226 model, 32 seed, 31 source). Directory counts were 35 staging, 23 intermediate, 28 core and 20 reporting before B1, 34, 30, 24 and 18 after it, and are 34, 32, 24 and 18 after B2. Layer counts, read from `meta.dag_layer`, are 34 staging, 16 identity, 12 reconstruction, 4 convergence, 24 core and 18 reporting. "Moved in B1" means the file crossed an existing directory bucket; "stays" does not mean unchanged semantic classification; "added in B2" marks the two new models.

| Model | Current directory | Target layer | Action |
|---|---|---|---|
| `dim_franchise` | `models/intermediate` | identity | moved in B1 |
| `dim_franchise_identity` | `models/intermediate` | identity | moved in B1 |
| `dim_franchise_season` | `models/intermediate` | identity | moved in B1 |
| `dim_league_format` | `models/marts/core` | core | stays |
| `dim_league_scoring_rule` | `models/marts/core` | core | stays |
| `dim_lineup_slot` | `models/marts/core` | core | stays |
| `dim_matchup_period` | `models/marts/core` | core | stays |
| `dim_mlb_team` | `models/marts/core` | core | stays |
| `dim_owner` | `models/intermediate` | identity | moved in B1 |
| `dim_player_identity` | `models/intermediate` | identity | moved in B1 |
| `dim_roster_slot_counts` | `models/marts/core` | core | stays |
| `dim_stat` | `models/marts/core` | core | stays |
| `dim_team_owner` | `models/intermediate` | identity | moved in B1 |
| `dim_team_season_standing` | `models/marts/core` | core | stays |
| `fct_cbs_player_game_attribution` | `models/intermediate` | reconstruction | moved in B1 |
| `fct_player_daily_performance` | `models/marts/core` | core | stays |
| `fct_player_daily_production` | `models/marts/core` | core | stays |
| `fct_player_position_pts` | `models/marts/core` | core | stays |
| `fct_player_roster_day` | `models/marts/core` | core | stays |
| `fct_player_season_performance` | `models/marts/core` | core | stays |
| `fct_player_weekly_active_performance` | `models/marts/core` | core | stays |
| `fct_player_weekly_inactive_performance` | `models/marts/core` | core | stays |
| `fct_player_weekly_slot_performance` | `models/marts/core` | core | stays |
| `fct_roster_stints` | `models/marts/core` | core | stays |
| `fct_team_period_standing` | `models/marts/core` | core | stays |
| `fct_team_season_performance` | `models/marts/core` | core | stays |
| `fct_team_weekly_active_performance` | `models/marts/core` | core | stays |
| `fct_team_weekly_inactive_performance` | `models/marts/core` | core | stays |
| `int_cbs__draft_picks` | `models/intermediate` | reconstruction | stays |
| `int_cbs__eligibility_windows` | `models/intermediate` | reconstruction | stays |
| `int_cbs__lineup_intervals` | `models/intermediate` | reconstruction | stays |
| `int_cbs__player_daily` | `models/intermediate` | convergence | stays |
| `int_cbs__player_game_points` | `models/intermediate` | reconstruction | stays |
| `int_cbs__player_name_ids` | `models/intermediate` | identity | stays |
| `int_cbs__player_season_stats` | `models/intermediate` | convergence | stays |
| `int_cbs__resolved_identity_anchors` | `models/intermediate` | identity | added in B2 (R3) |
| `int_cbs__resolved_identity_events` | `models/intermediate` | identity | added in B2 (R2) |
| `int_cbs__roster_stints` | `models/intermediate` | reconstruction | stays |
| `int_cbs__roster_stints_effective` | `models/intermediate` | reconstruction | stays |
| `int_cbs__team_owner_season` | `models/intermediate` | identity | stays |
| `int_franchise_current_teams` | `models/intermediate` | identity | stays |
| `int_franchise_registry` | `models/intermediate` | identity | stays |
| `int_franchise_season_points` | `models/intermediate` | convergence | stays |
| `int_franchise_seasons` | `models/intermediate` | identity | stays |
| `int_league_season_closure` | `models/intermediate` | reconstruction | stays |
| `int_matchup_period_evidence` | `models/intermediate` | reconstruction | stays |
| `int_matchup_period_membership` | `models/intermediate` | reconstruction | stays |
| `int_matchup_period_shape` | `models/intermediate` | reconstruction | stays |
| `int_matchup_season_derivation` | `models/intermediate` | reconstruction | stays |
| `int_matchup_season_standard` | `models/marts/core` | core | moved in B1 |
| `int_player_daily` | `models/intermediate` | convergence | stays |
| `int_player_identity_candidates` | `models/intermediate` | identity | stays |
| `int_player_identity_context` | `models/intermediate` | identity | stays |
| `mart_cbs_draft_recap` | `models/marts/reporting` | reporting | stays |
| `mart_cbs_draft_zip_fill` | `models/marts/reporting` | reporting | stays |
| `mart_daily_roster_snapshot` | `models/marts/core` | core | moved in B1 |
| `mart_draft_board` | `models/marts/reporting` | reporting | stays |
| `mart_franchise_rivalry` | `models/marts/reporting` | reporting | stays |
| `mart_franchise_rivalry_axes` | `models/marts/reporting` | reporting | stays |
| `mart_league_weekly_benchmarks` | `models/marts/reporting` | reporting | stays |
| `mart_period_standings` | `models/marts/core` | core | moved in B1 |
| `mart_player_career_records` | `models/marts/reporting` | reporting | stays |
| `mart_player_fpts_reconciliation` | `models/marts/reporting` | reporting | stays |
| `mart_player_season_records` | `models/marts/reporting` | reporting | stays |
| `mart_stat_leaderboard` | `models/marts/reporting` | reporting | stays |
| `mart_team_acquisition_channels` | `models/marts/reporting` | reporting | stays |
| `mart_team_all_play` | `models/marts/reporting` | reporting | stays |
| `mart_team_alltime` | `models/marts/reporting` | reporting | stays |
| `mart_team_matchup` | `models/marts/reporting` | reporting | stays |
| `mart_team_points_reconciliation` | `models/marts/reporting` | reporting | stays |
| `mart_team_position_eligibility` | `models/marts/reporting` | reporting | stays |
| `mart_team_season_standings` | `models/marts/reporting` | reporting | stays |
| `mart_team_slot_production` | `models/marts/reporting` | reporting | stays |
| `stg_box_scores` | `models/staging` | staging | stays |
| `stg_box_scores__free_agents` | `models/staging` | staging | stays |
| `stg_box_scores__matchups` | `models/staging` | staging | stays |
| `stg_box_scores__season_points_players` | `models/staging` | staging | stays |
| `stg_box_scores__team_rosters` | `models/staging` | staging | stays |
| `stg_cbs__draft` | `models/staging` | staging | stays |
| `stg_cbs__league_settings` | `models/staging` | staging | stays |
| `stg_cbs__mlbam_crosswalk` | `models/staging` | staging | stays |
| `stg_cbs__player_season_stats` | `models/staging` | staging | stays |
| `stg_cbs__rosters` | `models/staging` | staging | stays |
| `stg_cbs__rosters__teams` | `models/staging` | staging | stays |
| `stg_cbs__scoring_settings` | `models/staging` | staging | stays |
| `stg_cbs__standings` | `models/staging` | staging | stays |
| `stg_cbs__team_owners` | `models/intermediate` | identity | moved in B1 |
| `stg_cbs__ui_rosters` | `models/staging` | staging | stays |
| `stg_cbs__ui_standings` | `models/staging` | staging | stays |
| `stg_cbs__ui_transactions` | `models/staging` | staging | stays |
| `stg_divisions` | `models/staging` | staging | stays |
| `stg_draft` | `models/staging` | staging | stays |
| `stg_draft_settings` | `models/staging` | staging | stays |
| `stg_matchup_pairs` | `models/staging` | staging | stays |
| `stg_matchup_schedule` | `models/staging` | staging | stays |
| `stg_matchup_scores` | `models/staging` | staging | stays |
| `stg_mlb__fielding_seasons` | `models/staging` | staging | stays |
| `stg_mlb__game_positions` | `models/staging` | staging | stays |
| `stg_mlb__player_game` | `models/staging` | staging | stays |
| `stg_mlb__season_calendar` | `models/staging` | staging | stays |
| `stg_player_stat_breakdowns` | `models/staging` | staging | stays |
| `stg_roster_settings` | `models/staging` | staging | stays |
| `stg_schedule_settings` | `models/staging` | staging | stays |
| `stg_scoring_settings` | `models/staging` | staging | stays |
| `stg_team_owners` | `models/staging` | staging | stays |
| `stg_team_standings` | `models/staging` | staging | stays |
| `stg_transaction_coverage` | `models/staging` | staging | stays |
| `stg_transactions` | `models/staging` | staging | stays |

## 4. Violations and disposition

Under the current four-bucket order there are 12 backward edges: 2 staging reads of later buckets, 7 intermediate reads of core, and 3 core reads of reporting. Ten reporting-to-staging edges bring the clarified-rule total to **22**. An earlier review counted 27 by additionally counting five forward core-to-intermediate edges whose transitive dependencies include core; its strict variant counted 33 by adding six ordinary forward edges. Those eleven forward edges do not independently violate the clarified rule. The table below preserves all 33 review cases and states each disposition.

After the target assignment, there is **one backward edge and eight kind-d reporting-to-staging edges**, nine raw violations. The eight exact edges below are documented exceptions. B1 rewired the remaining backward edge, and the measured result since then is **zero backward edges, eight registered reporting-to-staging skips**. The eight are long forward skips from reporting straight to staging, registered because they bypass identity and convergence on purpose; nobody reads a later layer. This is not a claim that all direct staging reads disappear.

| Reader | Reads | Current evidence | Phase B disposition |
|---|---|---|---|
| `dim_franchise` | `int_franchise_registry` | `dbt_league/models/marts/core/dim_franchise.sql:34` | Move `dim_franchise` to identity. Keep names and ref() calls. |
| `dim_franchise_season` | `int_franchise_seasons` | `dbt_league/models/marts/core/dim_franchise_season.sql:36` | Move `dim_franchise_season` to identity. Keep names and ref() calls. |
| `dim_league_format` | `mart_period_standings` | `dbt_league/models/marts/core/dim_league_format.sql:51` | Move `mart_period_standings` to core. Keep names and ref() calls. |
| `dim_matchup_period` | `int_matchup_period_evidence` | `dbt_league/models/marts/core/dim_matchup_period.sql:143` | Permitted forward or same-layer read under the clarified rule; no rewire. |
| `dim_matchup_period` | `int_matchup_period_shape` | `dbt_league/models/marts/core/dim_matchup_period.sql:83` | Permitted forward or same-layer read under the clarified rule; no rewire. |
| `dim_player_identity` | `int_player_identity_candidates` | `dbt_league/models/marts/core/dim_player_identity.sql:58` | Move `dim_player_identity` to identity. Keep names and ref() calls. |
| `fct_cbs_player_game_attribution` | `int_cbs__lineup_intervals` | `dbt_league/models/marts/core/fct_cbs_player_game_attribution.sql:297` | Move `fct_cbs_player_game_attribution` to reconstruction. Keep names and ref() calls. |
| `fct_cbs_player_game_attribution` | `int_cbs__player_game_points` | `dbt_league/models/marts/core/fct_cbs_player_game_attribution.sql:52` | Move `fct_cbs_player_game_attribution` to reconstruction. Keep names and ref() calls. |
| `fct_cbs_player_game_attribution` | `int_cbs__roster_stints_effective` | `dbt_league/models/marts/core/fct_cbs_player_game_attribution.sql:95` | Move `fct_cbs_player_game_attribution` to reconstruction. Keep names and ref() calls. |
| `fct_cbs_player_game_attribution` | `int_player_identity_context` | `dbt_league/models/marts/core/fct_cbs_player_game_attribution.sql:126` | Move `fct_cbs_player_game_attribution` to reconstruction. Keep names and ref() calls. |
| `fct_player_daily_performance` | `int_player_daily` | `dbt_league/models/marts/core/fct_player_daily_performance.sql:120` | Permitted forward or same-layer read under the clarified rule; no rewire. |
| `fct_player_roster_day` | `int_cbs__roster_stints` | `dbt_league/models/marts/core/fct_player_roster_day.sql:169` | Permitted forward or same-layer read under the clarified rule; no rewire. |
| `fct_player_roster_day` | `mart_daily_roster_snapshot` | `dbt_league/models/marts/core/fct_player_roster_day.sql:58` | Move `mart_daily_roster_snapshot` to core. Keep names and ref() calls. |
| `fct_team_period_standing` | `mart_period_standings` | `dbt_league/models/marts/core/fct_team_period_standing.sql:121` | Move `mart_period_standings` to core. Keep names and ref() calls. |
| `int_cbs__lineup_intervals` | `dim_player_identity` | `dbt_league/models/intermediate/int_cbs__lineup_intervals.sql:77` | Move `dim_player_identity` to identity. Keep names and ref() calls. |
| `int_cbs__player_daily` | `dim_team_owner` | `dbt_league/models/intermediate/int_cbs__player_daily.sql:137` | Move `dim_team_owner` to identity. Keep names and ref() calls. |
| `int_cbs__player_daily` | `fct_cbs_player_game_attribution` | `dbt_league/models/intermediate/int_cbs__player_daily.sql:60` | Move `fct_cbs_player_game_attribution` to reconstruction. Keep names and ref() calls. |
| `int_cbs__roster_stints` | `dim_player_identity` | `dbt_league/models/intermediate/int_cbs__roster_stints.sql:611` | Move `dim_player_identity` to identity. Keep names and ref() calls. |
| `int_matchup_season_standard` | `dim_matchup_period` | `dbt_league/models/intermediate/int_matchup_season_standard.sql:45,74` | Move `int_matchup_season_standard` to core. Keep names and ref() calls. |
| `int_matchup_season_standard` | `fct_player_daily_performance` | `dbt_league/models/intermediate/int_matchup_season_standard.sql:59` | Move `int_matchup_season_standard` to core. Keep names and ref() calls. |
| `int_player_identity_context` | `dim_player_identity` | `dbt_league/models/intermediate/int_player_identity_context.sql:58` | Move `dim_player_identity` to identity. Keep names and ref() calls. |
| `mart_cbs_draft_zip_fill` | `stg_cbs__draft` | `dbt_league/models/marts/reporting/mart_cbs_draft_zip_fill.sql:41` | Documented kind-d exception; no Phase B rewire. See the exact-edge exception register. |
| `mart_daily_roster_snapshot` | `stg_box_scores` | `dbt_league/models/marts/reporting/mart_daily_roster_snapshot.sql:62` | Move `mart_daily_roster_snapshot` to core. Keep names and ref() calls. |
| `mart_draft_board` | `stg_draft` | `dbt_league/models/marts/reporting/mart_draft_board.sql:37` | Documented kind-d exception; no Phase B rewire. See the exact-edge exception register. |
| `mart_draft_board` | `stg_draft_settings` | `dbt_league/models/marts/reporting/mart_draft_board.sql:38` | Documented kind-d exception; no Phase B rewire. See the exact-edge exception register. |
| `mart_period_standings` | `stg_cbs__standings` | `dbt_league/models/marts/reporting/mart_period_standings.sql:30` | Move `mart_period_standings` to core. Keep names and ref() calls. |
| `mart_player_fpts_reconciliation` | `stg_cbs__player_season_stats` | `dbt_league/models/marts/reporting/mart_player_fpts_reconciliation.sql:118` | Documented kind-d exception; no Phase B rewire. See the exact-edge exception register. |
| `mart_player_fpts_reconciliation` | `stg_cbs__scoring_settings` | `dbt_league/models/marts/reporting/mart_player_fpts_reconciliation.sql:40` | Documented kind-d exception; no Phase B rewire. See the exact-edge exception register. |
| `mart_team_points_reconciliation` | `stg_cbs__ui_standings` | `dbt_league/models/marts/reporting/mart_team_points_reconciliation.sql:61` | Documented kind-d exception; no Phase B rewire. See the exact-edge exception register. |
| `mart_team_position_eligibility` | `stg_cbs__mlbam_crosswalk` | `dbt_league/models/marts/reporting/mart_team_position_eligibility.sql:256` | Documented kind-d exception; no Phase B rewire. See the exact-edge exception register. |
| `mart_team_position_eligibility` | `stg_cbs__rosters` | `dbt_league/models/marts/reporting/mart_team_position_eligibility.sql:185,194,210` | Documented kind-d exception; no Phase B rewire. See the exact-edge exception register. |
| `stg_cbs__player_season_stats` | `dim_stat` | `dbt_league/models/staging/stg_cbs__player_season_stats.sql:88` | Rewire the join to stat_classification; retain the three passthrough columns and join cardinality. |
| `stg_cbs__team_owners` | `int_cbs__team_owner_season` | `dbt_league/models/staging/stg_cbs__team_owners.sql:28` | Move `stg_cbs__team_owners` to identity. Keep names and ref() calls. |

### Exact-edge kind-d exception register

The eight entries are of two kinds.

**Audits, permanent.** An audit must read its control independently; these four never expire.

| Reader | Staging input | Reason to retain this direct input |
|---|---|---|
| `mart_player_fpts_reconciliation` | `stg_cbs__scoring_settings` | The reconciliation re-prices players against the platform's own rule feed, its control. Scoring rules are a universal concept the project already converges (`dim_league_scoring_rule`), but an audit that read the modeled rules it is checking would be circular, so it reads the captured weights independently. |
| `mart_player_fpts_reconciliation` | `stg_cbs__player_season_stats` | Platform-delivered season totals are the control for reconstructed player points. An audit that read modeled totals on both sides would be circular. |
| `mart_team_points_reconciliation` | `stg_cbs__ui_standings` | Captured team totals are the control for attributed player-game totals. The audit reads that observed side independently; reading the modeled standings it checks would be circular. |
| `mart_cbs_draft_zip_fill` | `stg_cbs__draft` | The fill audit compares assembled picks with the original draft capture, its control, to tell an absent recording from an assembled pick. An audit that read the assembly it is checking would be circular. |

**Modeling gaps, expiring.** Each row is an exception until the named model exists; then the reader moves onto it and the row is retired.

| Reader | Staging input | Reason to retain this direct input |
|---|---|---|
| `mart_draft_board` | `stg_draft` | Exception until a core draft-pick fact exists. The board joins draft picks, keeper status and purchase values to performance for draft-value analysis, and no core fact yet preserves the complete draft-pick population. The fact is a half-session item for 2.5 or 2.6. |
| `mart_draft_board` | `stg_draft_settings` | Exception until draft format and auction settings live in `dim_league_format` (or a draft-settings dimension). Draft settings are league settings like scoring and roster shape, which the project already converges (`dim_league_format`, `dim_league_scoring_rule`, `dim_roster_slot_counts`, `dim_lineup_slot`); these two have not been converged yet. |
| `mart_team_position_eligibility` | `stg_cbs__rosters` | Exception until the dense rostered atom exists in core: rostered player-days with eligibility as of the day, zero-game days included (the data-interface contract's R5/W10). Until then, going through performance facts would lose rostered players without a scored game and the delivered eligibility array. |
| `mart_team_position_eligibility` | `stg_cbs__mlbam_crosswalk` | Exception until the same dense rostered atom exists in core. The roster-eligibility path needs the platform-ID to MLBAM bridge for current roster rows, zero-game players included; it is an ID lookup, not a second ambiguous-name resolver. B2's anchor projection does not carry that bridge: it covers the 2003-2025 year-end rows, keyed by name with no platform id, and resolves an MLBAM id only for players with MLB games that season, so zero-game players stay unresolved there (56 of 10,449 rows in the frozen corpus). |

Phase C's test will flag the four expiring rows on every run, so they are retired rather than forgotten.

The two other current kind-d edges are resolved by moves. `mart_daily_roster_snapshot` becomes core: its read of `stg_box_scores` preserves rostered player-days with no performance, as explained in the [dbt README](../dbt_league/README.md#reading-the-dag-the-edges-that-look-odd). A pass-through fact would lose that purpose or merely add a redundant node. `mart_period_standings` also becomes core, where its captured period-standing input is permitted. Neither needs a reporting exception after its move.

An exception applies only to its exact reader/input pair and reason. It does not authorize new staging reads by the same model. Phase C checks that listed exceptions still exist and flags unused entries as well as new violations.

## 5. The surviving backward edge

`stg_cbs__player_season_stats` reads `dim_stat` at line 88. The staging query uses only `stat_name`, `stat_category` and `canonical_key`; `dim_stat` passes those three columns through from `stat_classification` without filtering rows. Its additional display-name calculations are not used here.

The two-line change in Phase B is to update the header reference from `dim_stat` to `stat_classification`, then change the join:

```sql
inner join {{ ref('stat_classification') }} d
    on m.canonical_key = d.canonical_key
```

Keep the join type and predicates. Compare output grain, duplicates, nulls and values; a successful parse alone does not prove equivalence. No SQL is changed in this design phase.

## 6. Consumer exposures and how reads were derived

Read sets were derived by searching SQL FROM/JOIN operands and literal ref/table strings in `output/` and `dashboard/`, then tracing entry points through imported functions, re-exports, callbacks and shared catalog helpers. Python was parsed as text/AST; no consumer, database query, Sheets write or publication was executed. SQL comments, docstrings, unused helpers, CTE names, information-schema probes and Parquet output reads were excluded. The set covers all reachable supported options, including optional relations, rather than claiming every run reads every listed input.

Dynamic cases were checked explicitly: `almanac_data.count_value_occurrences_for_scope` chooses the team or player weekly fact; the dashboard resolves `DECLARED_PERIODS`, its optional relation bindings and its output relation loop; records-book loading selects ESPN or CBS adapters. CBS team history supplies its own optimal-lineup callback, so the default ESPN callback and its three additional model reads are excluded from the CBS exposure. Shared renderer code alone does not imply a shared read set.

The ESPN exposure combines its head-to-head and season-points products. The CBS exposure uses the CBS adapter. The two read 31 and 24 models respectively, with a union of 46. `stg_scoring_settings` is absent from both almanac declarations; their glossary reads `dim_league_scoring_rule`. That staging model remains correctly declared for the separate records book.

| Exposure | Entry point | Models | Other direct inputs |
|---|---|---|---|
| `weekly_recap` | `output/generate_summary.py` | 8 | None |
| `records_report` | `output/generate_records_report.py` | 5 | None |
| `records_sheet` | `output/generate_records_report.py` | 5 | None |
| `espn_almanac` | `output/generate_almanac_sheet.py` | 31 | `slot_classification` seed |
| `cbs_almanac` | `output/generate_almanac_sheet.py` | 24 | `cbs_franchises` seed, `draft_assembly_plan` seed, `mlb_team_abbrevs` seed, `slot_classification` seed |
| `shared_almanac_export` | `output/export_shared.py` | 13 | `slot_classification` seed; `raw.team_owners` source timestamp |
| `season_report` | `output/generate_season_report.py` | 16 | `slot_classification` seed |
| `records_book` | `output/generate_records_book.py` | 20 | None |
| `dashboard_extract` | `dashboard/publish_extract.py` | 20 | None |

There are nine exposures, up from five, and 60 distinct declared models, up from 26. The season report has 16 models plus one seed, rather than the earlier estimate of 17 models. The earlier review supplied no per-model read list for that consumer, so an exact missing/extra-model comparison with its estimate cannot be reconstructed. The records book and dashboard each read 20 models, and the shared export reads 13. Direct seed and raw-source reads are also declared rather than hidden behind a model-only total. The existing support address and anonymized sample URLs are retained; exposure ownership uses a role label.

## 7. Implementation and enforcement

**Phase B:** move the eleven existing files with their schema properties and preserve materialization, alias, schema, tags, unique keys and relation names. Update the two tests that read moved SQL by filesystem path. Configure every model's semantic layer. Make the seed join change above. The attribution chain becomes reconstruction -> convergence -> core through file classification alone; do not rewrite a valid forward edge to make it adjacent.

Identity-first processing also needs semantic work beyond file movement. The current roster walk-back resolves identity after computing stints, and attribution and lineup code contain their own fallback expressions. Factor the existing event and anchor identity-selection projections into two identity-owned input models, preserving source-row multiplicity, scoped precedence, unresolved fallback keys and provenance. Reconstruction then consumes the resolved event/anchor inputs before deriving intervals. These proposed input models are additions, not renames of any existing model. The 106-row table describes existing models; adding both projections would produce 108 models and 16 identity models. Native MLBAM game keys and source-ID label lookups do not require an additional resolver. Do not silently merge formerly distinct event streams merely because they resolve to the same person. Any golden change stops implementation for review.

Build from the immutable local fixture into an empty analytics schema in a new clone-local database. A successful comparison against a database containing old relations is insufficient. Use the frozen ESPN and CBS byte-diff suites, the separate ESPN-points rehearsal corpus when available, the BBCode golden tests with their required data backend, and dbt data tests. Goldens and fixture contents remain immutable.

**B1 status, 2026-10-16.** The eleven moves landed with `git mv`, each with its schema.yml block relocated unchanged; no model, column, relation or file was renamed, and every moved model kept its materialization. `meta.dag_layer` is set: each directory carries a default in `dbt_project.yml` (staging, reconstruction for intermediate, core, reporting), and the fourteen identity and four convergence models override it in their own `config()`. R1 is done: `stg_cbs__player_season_stats` joins the `stat_classification` seed instead of `dim_stat`. Under the section 1 rule the parsed manifest has zero backward edges, eight registered reporting-to-staging skips (exactly the section 4 register). Section 4's evidence column records the Phase A file paths. R2 and R3, the two identity input projections, remain pending as B2; the manifest enforcement test remains Phase C.

**B2 status, 2026-10-18.** R2 and R3 landed as two commits. `int_cbs__resolved_identity_events` (one row per captured transaction row) and `int_cbs__resolved_identity_anchors` (one row per captured year-end roster row) are identity models that own the CBS name normalization and the resolution policies. Each policy is a separate named column set: season-only for the lineup intervals, franchise-context first for the roster walk-back and the attribution fact, plus the league name dictionary's CBS id. `int_cbs__lineup_intervals`, `int_cbs__roster_stints` and `fct_cbs_player_game_attribution` read those columns and no longer read `dim_player_identity` or `int_player_identity_context` themselves. The walk-back still runs at name grain, and the attribution fact still re-aggregates anchors to its own grain. Output columns and grain are unchanged, the byte-diffs pass, and row counts, key counts, unresolved populations and content hashes match the pre-B2 build. The parsed manifest has 108 models, 16 identity, and zero backward edges, eight registered reporting-to-staging skips. Pending: B3, the final fresh build, the remaining documentation and the README diagram. B4, folders that match the six layers. Phase C, the CI manifest test below.

**Phase C:** add a CI-only manifest test requiring complete, valid layer metadata; reject every backward model edge; reject reporting-to-staging edges except the eight exact documented pairs; permit same-layer edges and other forward skips. dbt checks actual cycles. A separate consumer-read contract check can compare traced read sets with exposures, including supported dynamic-table choices and direct seeds/sources. Do not implement these enforcement tests before Phase B stabilizes the graph.
