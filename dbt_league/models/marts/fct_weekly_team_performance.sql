-- fct_weekly_team_performance.sql
-- Phase 7 E2 transitional compatibility view. The canonical model was
-- renamed to fct_weekly_team_active_performance to make the
-- active/inactive symmetry explicit; this view exposes the same rows
-- under the old name so Python consumers (generate_summary.py,
-- generate_records_report.py, league_notes.py, records.py) continue
-- to resolve their existing FROM clauses without per-commit churn.
--
-- Lifecycle:
--   - Born in sub-chunk E2 (this commit).
--   - Sub-chunk G rewires the Python consumers to reference
--     fct_weekly_team_active_performance directly.
--   - Sub-chunk H drops this file as part of dead-model cleanup.
--
-- Do NOT add new consumers against this name. New code should use
-- fct_weekly_team_active_performance.

{{ config(materialized='view') }}

select * from {{ ref('fct_weekly_team_active_performance') }}
