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

-- ALL-SEASON lineage rows only. The seed is season-scoped (MLB-115): a row
-- carrying a season_year speaks about one franchise-season, which this
-- franchise-grain dim has no key to express -- applying it here would silently
-- rewrite the franchise across its whole history. Those rows belong to
-- dim_franchise_season; a blank season_year means "always," which is every row
-- the seed held before it gained the column.
lineage as (
    select
        league_key,
        cast(franchise_id as varchar)                    as franchise_id,
        cast(canonical_franchise_id as varchar)          as canonical_franchise_id,
        nullif(cast(canonical_name as varchar), '')      as canonical_name,
        nullif(cast(canonical_abbrev as varchar), '')    as canonical_abbrev
    from {{ ref('franchise_lineage') }}
    where nullif(cast(season_year as varchar), '') is null
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
),

-- THE CONFIGURED-NAME ANCHOR (MLB-229). A configured name belongs to the
-- FRANCHISE, not to the id it happens to be written against. A league that
-- names a re-minted id -- the era it thinks of as current -- has named the
-- whole lineage, including the earlier ids that are the same team. Resolving
-- per-id instead would let one franchise show two names, and would make an
-- explicit name on a non-anchor id invisible to anything keyed on the anchor.
--
-- Latest-observed wins among the members that carry one, mirroring the
-- DISPLAY anchor immediately above -- if a league has named two eras of one
-- franchise differently, the current era is the one it means. The
-- franchise_id tie-break is the same determinism guard for the same reason.
configured_anchor as (
    select
        league_key,
        canonical_franchise_id,
        override_name,
        override_abbrev
    from (
        select
            r.league_key,
            r.canonical_franchise_id,
            r.override_name,
            r.override_abbrev,
            row_number() over (
                partition by r.league_key, r.canonical_franchise_id
                order by f.last_observed_season desc nulls last,
                         r.franchise_id desc
            ) as recency_rank
        from resolved r
        join franchises f
            on r.league_key = f.league_key
            and r.franchise_id = f.franchise_id
        where r.override_name is not null
    )
    where recency_rank = 1
)

select
    r.league_key,
    r.franchise_id,
    r.canonical_franchise_id,
    (r.franchise_id = r.canonical_franchise_id)       as is_canonical_anchor,
    coalesce(r.override_name, anchor.franchise_name)  as canonical_name,
    coalesce(r.override_abbrev, anchor.abbrev)        as canonical_abbrev,

    -- PROVENANCE (MLB-229). canonical_name above is a COALESCE, so it cannot
    -- tell a name the league CONFIGURED from one merely OBSERVED -- and those
    -- two answer different questions. A configured name is a statement of
    -- identity: the league is saying "this franchise IS this team", which is
    -- why two ids carrying the same configured name are one team and two ids
    -- that merely happen to be observed under the same string are not.
    -- Grouping on the coalesced column cannot distinguish them, so the
    -- configured name is published separately. NULL means "no explicit name"
    -- -- the whole signal, not a missing value.
    --
    -- LINEAGE-WIDE, unlike canonical_name's per-id override: every member of a
    -- lineage reports the same configured name, because they are the same
    -- franchise. canonical_name is deliberately left exactly as it was -- it
    -- is a rendered label with goldens behind it, and widening its resolution
    -- would move output under a ticket that is not about display.
    conf.override_name                                as configured_name,
    conf.override_abbrev                              as configured_abbrev,
    (conf.override_name is not null)                  as has_configured_name
from resolved r
join display_anchor anchor
    on r.league_key = anchor.league_key
    and r.canonical_franchise_id = anchor.canonical_franchise_id
left join configured_anchor conf
    on r.league_key = conf.league_key
    and r.canonical_franchise_id = conf.canonical_franchise_id
