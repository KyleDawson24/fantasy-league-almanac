-- dim_franchise.sql
-- Canonical franchise identity (MLB-64). The platform re-mints a team id when
-- a franchise leaves and comes back (Foster's Folly is id 13 through 2019 and
-- id 30 from 2021); this collapses those ids into ONE franchise per the
-- curated cbs_franchise_lineage seed -- harvested from the historian's
-- continuity sheet, so the links are data, not code. franchise_id ->
-- canonical_franchise_id (the EARLIEST id in a lineage, its anchor) + the
-- canonical display name / abbrev (the anchor's, unless the seed overrides).
-- An unlinked franchise is its own canonical; the #### sentinel (9999) maps to
-- itself. This is the single join every team-grain surface uses to tell a
-- franchise's whole story across renames and re-mints.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, franchise_id).
-- ==========================================================================
{{ config(materialized='view') }}

with franchises as (
    select
        league_key,
        cast(franchise_id as varchar) as franchise_id,
        abbrev,
        franchise_name
    from {{ ref('cbs_franchises') }}
),

lineage as (
    select
        league_key,
        cast(franchise_id as varchar)                    as franchise_id,
        cast(canonical_franchise_id as varchar)          as canonical_franchise_id,
        nullif(cast(canonical_name as varchar), '')      as canonical_name,
        nullif(cast(canonical_abbrev as varchar), '')    as canonical_abbrev
    from {{ ref('cbs_franchise_lineage') }}
),

resolved as (
    select
        f.league_key,
        f.franchise_id,
        coalesce(l.canonical_franchise_id, f.franchise_id)
            as canonical_franchise_id,
        l.canonical_name   as override_name,
        l.canonical_abbrev as override_abbrev
    from franchises f
    left join lineage l
        on f.league_key = l.league_key
        and f.franchise_id = l.franchise_id
)

select
    r.league_key,
    r.franchise_id,
    r.canonical_franchise_id,
    (r.franchise_id = r.canonical_franchise_id)      as is_canonical_anchor,
    coalesce(r.override_name, anchor.franchise_name) as canonical_name,
    coalesce(r.override_abbrev, anchor.abbrev)       as canonical_abbrev
from resolved r
join franchises anchor
    on r.league_key = anchor.league_key
    and r.canonical_franchise_id = anchor.franchise_id
