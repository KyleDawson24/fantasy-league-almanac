-- dim_franchise_season.sql
-- Canonical franchise identity AT A SEASON (MLB-115). The season-grain twin of
-- dim_franchise: same resolution, one extra key, and the only place a
-- season-scoped lineage row can be honoured.
--
-- WHY IT EXISTS. dim_franchise answers "which franchise is this id?" -- one
-- answer for all time. That cannot express a platform reusing a LIVE id for a
-- new team: an abandoned slot taken over by a new manager is the same id and
-- genuinely different franchises on either side of the handover. Because
-- dim_franchise is franchise-grain, applying such a row there would rewrite the
-- franchise across its whole history, which is the opposite of the intent. So
-- the seed's season-scoped rows are read HERE and ignored there.
--
-- Precedence, per franchise-season:
--   1. a season-scoped lineage row for exactly this (franchise_id, season_year)
--   2. otherwise dim_franchise's franchise-level answer
-- Display always resolves from the CANONICAL franchise, so a season parked on
-- the holding pen shows the pen's label rather than the team that later
-- inherited the id.
--
-- Most surfaces should keep joining dim_franchise. Reach for this one when the
-- surface is season-grain AND attribution matters -- record books, season
-- standings, anything that must not credit a franchise for a season it did not
-- play.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, franchise_id, season_year).
-- ==========================================================================
{{ config(materialized='view') }}

with spine as (
    select
        league_key,
        franchise_id,
        season_year
    from {{ ref('int_franchise_seasons') }}
),

-- SEASON-SCOPED rows only; the all-season ones are already resolved into
-- dim_franchise and would double-apply here.
--
-- The NAME columns are read too (MLB-229). The seed schema has always
-- described canonical_name / canonical_abbrev as valid optional overrides on
-- any row, and this model read only canonical_franchise_id -- so a name
-- written against a season-scoped row did nothing at all, silently. Since an
-- explicitly configured canonical name is now the aggregation identity, "did
-- nothing" would have meant a league could not name a single season's team and
-- would get no error saying so.
season_override as (
    select
        league_key,
        cast(franchise_id as varchar)                             as franchise_id,
        cast(nullif(cast(season_year as varchar), '') as integer) as season_year,
        cast(canonical_franchise_id as varchar)                   as canonical_franchise_id,
        nullif(cast(canonical_name as varchar), '')               as override_name,
        nullif(cast(canonical_abbrev as varchar), '')             as override_abbrev
    from {{ ref('franchise_lineage') }}
    where nullif(cast(season_year as varchar), '') is not null
),

franchise_level as (
    select
        league_key,
        franchise_id,
        canonical_franchise_id
    from {{ ref('dim_franchise') }}
),

resolved as (
    select
        s.league_key,
        s.franchise_id,
        s.season_year,
        coalesce(o.canonical_franchise_id, f.canonical_franchise_id)
            as canonical_franchise_id,
        (o.franchise_id is not null) as is_season_override,
        o.override_name              as season_override_name,
        o.override_abbrev            as season_override_abbrev
    from spine s
    join franchise_level f
        on s.league_key = f.league_key
        and s.franchise_id = f.franchise_id
    left join season_override o
        on s.league_key = o.league_key
        and s.franchise_id = o.franchise_id
        and s.season_year = o.season_year
)

select
    r.league_key,
    r.franchise_id,
    r.season_year,
    r.canonical_franchise_id,
    r.is_season_override,
    (r.franchise_id = r.canonical_franchise_id) as is_canonical_anchor,

    -- NAME PRECEDENCE, per franchise-season (MLB-229):
    --   1. a name written on THIS season's own lineage row;
    --   2. otherwise the franchise-level answer.
    -- Explicit, and applied to the DISPLAY name as well as to the configured
    -- one. Honouring a season-scoped name in the identity columns but not in
    -- the display would have the matrix call a team one thing while the rest
    -- of the almanac called it another -- for a season the league had gone out
    -- of its way to name.
    coalesce(r.season_override_name, canon.canonical_name)
        as canonical_name,
    coalesce(r.season_override_abbrev, canon.canonical_abbrev)
        as canonical_abbrev,

    -- Provenance: whether this franchise-season's name was configured by the
    -- league or merely observed. dim_franchise_identity keys on the
    -- difference. The season row wins here for the same reason it wins above,
    -- and a season row naming a franchise makes THAT SEASON configured even
    -- where the franchise as a whole is not.
    coalesce(r.season_override_name, canon.configured_name)
        as configured_name,
    coalesce(r.season_override_abbrev, canon.configured_abbrev)
        as configured_abbrev,
    (coalesce(r.season_override_name, canon.configured_name) is not null)
        as has_configured_name
from resolved r
join {{ ref('dim_franchise') }} canon
    on r.league_key = canon.league_key
    and r.canonical_franchise_id = canon.franchise_id
