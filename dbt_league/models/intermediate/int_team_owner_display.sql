-- int_team_owner_display.sql
-- v1.2: (season, team) -> owner display label. Joins the team->owner
-- bridge (stg_team_owners) to dim_owner and collapses co-owned teams into
-- a single "Name / Name" string, mirroring the box-score format_owners()
-- co-owner convention. This is the single join point downstream models
-- use to attach owner_display alongside the existing owner_name.
--
-- ==========================================================================
-- GRAIN: one row per (season_year, team_id).
-- ==========================================================================
--
-- Materialization: view. Small per-season-team lookup.

{{ config(materialized='view') }}

select
    sto.season_year,
    sto.team_id,
    listagg(do.owner_display, ' / ')
        within group (order by do.owner_display) as owner_display
from {{ ref('stg_team_owners') }} sto
inner join {{ ref('dim_owner') }} do
    on sto.owner_id = do.owner_id
group by 1, 2
