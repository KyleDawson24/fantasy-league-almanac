-- dim_matchup_period.sql
-- Consumer-facing dimension for matchup-period calendar metadata. Thin
-- view over the matchup_schedule seed.
--
-- v1.1.0 DAG cleanup: introduces a public-facing dim so output scripts
-- and external readers depend on a "first-class" model rather than an
-- internal seed. The seed remains the source of truth for editors
-- (CSV edits + reseed); the dim is what consumers reference. The
-- calendar-automation work tracked on the roadmap will replace the
-- hand-maintained seed with an extract-driven append-only raw table
-- plus a thin overrides seed; once that lands, dim_matchup_period
-- re-points at the new sources without changing the consumer contract.
--
-- Carries through every seed column unchanged for v1.1.0. Future
-- additions (e.g., a derived days_in_period column, week_number_within_
-- season) can land here without affecting upstream.
--
-- Materialization: view. ~25-row × N-season table; no aggregation or
-- caching value at this layer. Python consumers reach this via
-- output/records.py::load_schedule_lookup (single query per script
-- run, dict-cached).

{{ config(materialized='view') }}

select
    season_year,
    matchup_period,
    start_date,
    end_date,
    is_abnormal,
    abnormal_reason,
    is_playoff,
    playoff_round
from {{ ref('matchup_schedule') }}
