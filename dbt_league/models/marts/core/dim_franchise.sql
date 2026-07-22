-- dim_franchise.sql
-- Canonical franchise identity (MLB-64). The platform re-mints a team id when
-- a franchise leaves and comes back (Foster's Folly is id 13 through 2019 and
-- id 30 from 2021); this collapses those ids into ONE franchise per the
-- curated franchise_lineage seed -- harvested from the historian's continuity
-- sheet, so the links are data, not code. Reads its observed franchises from
-- the platform-general int_franchise_registry seam, so any league flows
-- through. franchise_id ->
-- canonical_franchise_id (the EARLIEST id in a lineage, its anchor) + the
-- canonical display name / abbrev.
-- An unlinked franchise is its own canonical; the #### sentinel (9999) maps to
-- itself. This is the single join every team-grain surface uses to tell a
-- franchise's whole story across renames and re-mints.
--
-- TWO ANCHORS, DELIBERATELY (MLB-113). Identity anchors on the EARLIEST id in a
-- lineage; DISPLAY anchors on the LATEST-SEEN one. Kyle's rule, 2026-07-22: a
-- franchise displays the name from an override seed if one is given, else its
-- most recent name. Collapsing both onto the earliest id -- as this model did
-- until MLB-113 -- shows a franchise that left and came back the name it wore
-- in its OLDEST era, which is the one name it is guaranteed to have outgrown.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, franchise_id).
-- ==========================================================================
{{ config(materialized='view') }}

with franchises as (
    select
        league_key,
        franchise_id,
        observed_abbrev as abbrev,
        observed_name   as franchise_name,
        last_observed_season
    from {{ ref('int_franchise_registry') }}
),

lineage as (
    select
        league_key,
        cast(franchise_id as varchar)                    as franchise_id,
        cast(canonical_franchise_id as varchar)          as canonical_franchise_id,
        nullif(cast(canonical_name as varchar), '')      as canonical_name,
        nullif(cast(canonical_abbrev as varchar), '')    as canonical_abbrev
    from {{ ref('franchise_lineage') }}
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
),

-- The DISPLAY anchor: within a lineage, the member seen most recently. The
-- franchise_id tie-break is a determinism guard, not a rule -- eras do not
-- overlap, so it only decides a lineage carrying no season signal at all.
display_anchor as (
    select
        league_key,
        canonical_franchise_id,
        franchise_name,
        abbrev
    from (
        select
            r.league_key,
            r.canonical_franchise_id,
            f.franchise_name,
            f.abbrev,
            row_number() over (
                partition by r.league_key, r.canonical_franchise_id
                order by f.last_observed_season desc nulls last,
                         r.franchise_id desc
            ) as recency_rank
        from resolved r
        join franchises f
            on r.league_key = f.league_key
            and r.franchise_id = f.franchise_id
    )
    where recency_rank = 1
)

select
    r.league_key,
    r.franchise_id,
    r.canonical_franchise_id,
    (r.franchise_id = r.canonical_franchise_id)       as is_canonical_anchor,
    coalesce(r.override_name, anchor.franchise_name)  as canonical_name,
    coalesce(r.override_abbrev, anchor.abbrev)        as canonical_abbrev
from resolved r
join display_anchor anchor
    on r.league_key = anchor.league_key
    and r.canonical_franchise_id = anchor.canonical_franchise_id
